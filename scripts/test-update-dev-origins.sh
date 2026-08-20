#!/bin/sh
set -eu

index_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
updater="$index_root/scripts/update-dev-origins.sh"
temporary_root=$(mktemp -d "${TMPDIR:-/tmp}/alire-index-update-test.XXXXXX")

cleanup () {
  rm -rf -- "$temporary_root"
}
trap cleanup EXIT
trap 'exit 1' HUP INT TERM

fail () {
  printf '%s\n' "update-dev-origins test: $*" >&2
  exit 1
}

export GIT_AUTHOR_NAME='Alire index test'
export GIT_AUTHOR_EMAIL='alire-index-test@example.invalid'
export GIT_COMMITTER_NAME=$GIT_AUTHOR_NAME
export GIT_COMMITTER_EMAIL=$GIT_AUTHOR_EMAIL

make_development_source_manifest () {
  manifest=$1
  crate_name=$2
  version=$3
  dependency=${4:-}
  dependency_constraint=${5:-}
  pin_path=${6:-}
  mkdir -p "$(dirname -- "$manifest")"
  {
    printf 'name = "%s"\n' "$crate_name"
    printf 'description = "Development source manifest for %s"\n' "$crate_name"
    printf 'version = "%s"\n' "$version"
    printf 'licenses = "MIT"\n'
    if [ -n "$dependency" ]; then
      printf '\n[[depends-on]]\n%s = "%s"\n' \
        "$dependency" "${dependency_constraint:-*}"
    fi
    if [ -n "$pin_path" ]; then
      printf '\n[[pins]]\n%s = { path = "%s" }\n' "$dependency" "$pin_path"
    fi
  } >"$manifest"
}

make_source () {
  source_name=$1
  source_version=$2
  source_dependency=${3:-}
  source_constraint=${4:-}
  source_pin_path=${5:-}
  source_work="$temporary_root/$source_name-work"
  source_remote="$temporary_root/$source_name.git"
  git init -q --bare "$source_remote"
  git -C "$source_remote" symbolic-ref HEAD refs/heads/main
  git init -q --initial-branch=main "$source_work"
  printf '%s\n' "$source_name remote release" >"$source_work/release.txt"
  make_development_source_manifest \
    "$source_work/alire.toml" "$source_name" "$source_version" \
    "$source_dependency" "$source_constraint" "$source_pin_path"
  git -C "$source_work" add release.txt alire.toml
  git -C "$source_work" commit -q -m "Publish $source_name"
  git -C "$source_work" remote add origin "$source_remote"
  git -C "$source_work" push -q -u origin main
}

make_manifest () {
  manifest=$1
  crate_name=$2
  version=$3
  origin=$4
  subdir=${5:-}
  mkdir -p "$(dirname -- "$manifest")"
  {
    printf 'name = "%s"\n' "$crate_name"
    printf 'version = "%s"\n\n' "$version"
    printf '[origin]\n'
    printf 'commit = "0000000000000000000000000000000000000000"\n'
    if [ -n "$subdir" ]; then
      printf 'subdir = "%s"\n' "$subdir"
    fi
    printf 'url = "git+%s"\n' "$origin"
  } >"$manifest"
}

make_source_manifest () {
  manifest=$1
  crate_name=$2
  version=$3
  mkdir -p "$(dirname -- "$manifest")"
  {
    printf 'name = "%s"\n' "$crate_name"
    printf 'description = "Tagged source manifest for %s"\n' "$crate_name"
    printf 'version = "%s"\n' "$version"
    printf 'licenses = "MIT"\n'
  } >"$manifest"
}

make_source alpha 0.2.0-dev
make_source beta 0.1.0-dev beta_helper '~0.2.0-dev' beta_helper
make_source gamma 0.1.0-dev
alpha_remote="$temporary_root/alpha.git"
beta_remote="$temporary_root/beta.git"
gamma_remote="$temporary_root/gamma.git"

# Release tags are namespaced by crate so one monorepo can publish several
# manifests independently. Cover both lightweight and annotated tags.
make_source_manifest "$temporary_root/alpha-work/alire.toml" alpha 0.2.0
make_source_manifest \
  "$temporary_root/alpha-work/alpha_extra/alire.toml" alpha_extra 0.1.0
git -C "$temporary_root/alpha-work" add alire.toml alpha_extra/alire.toml
git -C "$temporary_root/alpha-work" commit -q -m 'Prepare alpha releases'
git -C "$temporary_root/alpha-work" tag alpha/v0.2.0
git -C "$temporary_root/alpha-work" tag -a alpha_extra/v0.1.0 \
  -m 'Publish alpha_extra 0.1.0'
git -C "$temporary_root/alpha-work" tag alpha/v0.2
git -C "$temporary_root/alpha-work" tag unrelated/v9.9.9
git -C "$temporary_root/alpha-work" push -q origin main --tags

# A tag and its source manifest must agree before anything is published.
make_source_manifest "$temporary_root/gamma-work/alire.toml" gamma 0.1.1
git -C "$temporary_root/gamma-work" add alire.toml
git -C "$temporary_root/gamma-work" commit -q -m 'Prepare mismatched gamma release'
git -C "$temporary_root/gamma-work" tag gamma/v0.1.0
git -C "$temporary_root/gamma-work" push -q origin main --tags

alpha_head=$(git -C "$alpha_remote" rev-parse refs/heads/main)
beta_head=$(git -C "$beta_remote" rev-parse refs/heads/main)

# A local-only source commit must not affect what the updater publishes.
printf '%s\n' 'not pushed' >>"$temporary_root/alpha-work/release.txt"
git -C "$temporary_root/alpha-work" commit -qam 'Keep local change unpublished'
[ "$(git -C "$temporary_root/alpha-work" rev-parse HEAD)" != "$alpha_head" ] || \
  fail "source fixture did not create an unpublished commit"

index_remote="$temporary_root/index.git"
index_seed="$temporary_root/index-seed"
index_work="$temporary_root/index-work"
git init -q --bare "$index_remote"
git -C "$index_remote" symbolic-ref HEAD refs/heads/main
git init -q --initial-branch=main "$index_seed"
mkdir -p "$index_seed/scripts" "$index_seed/index"
cp "$updater" "$index_seed/scripts/update-dev-origins.sh"
chmod +x "$index_seed/scripts/update-dev-origins.sh"
printf 'version = "1.1"\n' >"$index_seed/index/index.toml"
make_manifest \
  "$index_seed/index/al/alpha/alpha-0.1.0-dev.toml" \
  alpha 0.1.0-dev "$alpha_remote"
make_manifest \
  "$index_seed/index/al/alpha/alpha-0.0.9-dev.toml" \
  alpha 0.0.9-dev "$alpha_remote"
make_manifest \
  "$index_seed/index/al/alpha/alpha-0.2.0-dev.toml" \
  alpha 0.2.0-dev "$alpha_remote"
make_manifest \
  "$index_seed/index/al/alpha_extra/alpha_extra-0.1.0-dev.toml" \
  alpha_extra 0.1.0-dev "$alpha_remote" alpha_extra
make_manifest \
  "$index_seed/index/be/beta/beta-0.1.0-dev.toml" \
  beta 0.1.0-dev "$beta_remote"
make_manifest \
  "$index_seed/index/ga/gamma/gamma-0.1.0-dev.toml" \
  gamma 0.1.0-dev "$gamma_remote"
make_manifest \
  "$index_seed/index/al/alpha/alpha-0.1.0.toml" \
  alpha 0.1.0 "$alpha_remote"
make_manifest \
  "$index_seed/index/re/released/released-0.9.0-dev.toml" \
  released 0.9.0-dev "$beta_remote"
make_manifest \
  "$index_seed/index/re/released/released-0.10.0.toml" \
  released 0.10.0 "$beta_remote"
git -C "$index_seed" add .
git -C "$index_seed" commit -q -m 'Create fixture index'
git -C "$index_seed" remote add origin "$index_remote"
git -C "$index_seed" push -q -u origin main
git clone -q "$index_remote" "$index_work"

# The updater must first fast-forward a stale index checkout.
printf '%s\n' 'remote index update' >"$index_seed/REMOTE.md"
git -C "$index_seed" add REMOTE.md
git -C "$index_seed" commit -q -m 'Advance remote index'
git -C "$index_seed" push -q

fake_alr="$temporary_root/alr"
alr_log="$temporary_root/alr.log"
cat >"$fake_alr" <<'EOF'
#!/bin/sh
printf '%s\n' "$*" >>"${ALR_LOG:?}"
EOF
chmod +x "$fake_alr"

invalid_work="$temporary_root/index-invalid-work"
git clone -q "$index_remote" "$invalid_work"
if ALR=$fake_alr ALR_LOG=$alr_log \
  "$invalid_work/scripts/update-dev-origins.sh" --crate gamma \
  >"$temporary_root/invalid.stdout" 2>"$temporary_root/invalid.stderr"; then
  fail "release tag with mismatched source metadata was accepted"
fi
grep -F 'alire.toml at gamma/v0.1.0 declares version 0.1.1' \
  "$temporary_root/invalid.stderr" >/dev/null || \
  fail "release metadata mismatch was not explained"
[ ! -e "$invalid_work/index/ga/gamma/gamma-0.1.0.toml" ] || \
  fail "invalid tagged release created an index manifest"
git -C "$temporary_root/gamma-work" tag -d gamma/v0.1.0 >/dev/null
git -C "$temporary_root/gamma-work" push -q origin :refs/tags/gamma/v0.1.0

release_only_work="$temporary_root/index-release-only-work"
git clone -q "$index_remote" "$release_only_work"
ALR=$fake_alr ALR_LOG=$alr_log \
  "$release_only_work/scripts/update-dev-origins.sh" \
  --crate alpha --releases-only --commit
[ -f "$release_only_work/index/al/alpha/alpha-0.2.0.toml" ] || \
  fail "--releases-only did not publish a matching tag"
for manifest in \
  index/al/alpha/alpha-0.2.0-dev.toml \
  index/al/alpha_extra/alpha_extra-0.1.0-dev.toml; do
  grep -F 'commit = "0000000000000000000000000000000000000000"' \
    "$release_only_work/$manifest" >/dev/null || \
    fail "--releases-only advanced $manifest"
done

ALR=$fake_alr ALR_LOG=$alr_log \
  "$index_work/scripts/update-dev-origins.sh" --crate missing \
  >"$temporary_root/missing.stdout" 2>"$temporary_root/missing.stderr" && \
  fail "unknown crate selector was accepted"
grep -F 'no development manifest is named missing' \
  "$temporary_root/missing.stderr" >/dev/null || \
  fail "unknown crate selector failure was not explained"

ALR=$fake_alr ALR_LOG=$alr_log \
  "$index_work/scripts/update-dev-origins.sh" --crate released --releases-only \
  >"$temporary_root/released.stdout"
grep -F 'No matching releases were found.' \
  "$temporary_root/released.stdout" >/dev/null || \
  fail "retired development selector did not scan for releases"

ALR=$fake_alr ALR_LOG=$alr_log \
  "$index_work/scripts/update-dev-origins.sh" --crate alpha_extra --push \
  >"$temporary_root/alpha-extra.stdout"

[ -f "$index_work/REMOTE.md" ] || \
  fail "stale index checkout was not fast-forwarded"
# Alpha's sources have moved on to their release versions, so the development
# manifests no longer describe them and must be left alone rather than pinned
# to a commit that declares a different version.
for manifest in \
  index/al/alpha/alpha-0.2.0-dev.toml \
  index/al/alpha_extra/alpha_extra-0.1.0-dev.toml; do
  grep -F 'commit = "0000000000000000000000000000000000000000"' \
    "$index_work/$manifest" >/dev/null || \
    fail "$manifest tracked a source declaring a different version"
done
grep -F 'Ignoring index/al/alpha_extra/alpha_extra-0.1.0-dev.toml' \
  "$temporary_root/alpha-extra.stdout" >/dev/null || \
  fail "skipping a diverged development source was not explained"
for manifest in \
  index/al/alpha/alpha-0.0.9-dev.toml \
  index/al/alpha/alpha-0.1.0-dev.toml; do
  grep -F 'commit = "0000000000000000000000000000000000000000"' \
    "$index_work/$manifest" >/dev/null || \
    fail "$manifest was not retired by stable alpha 0.1.0"
done
grep -F 'commit = "0000000000000000000000000000000000000000"' \
  "$index_work/index/be/beta/beta-0.1.0-dev.toml" >/dev/null || \
  fail "unselected beta development manifest was changed"
grep -F 'commit = "0000000000000000000000000000000000000000"' \
  "$index_work/index/al/alpha/alpha-0.1.0.toml" >/dev/null || \
  fail "stable manifest was changed"
for release in \
  index/al/alpha/alpha-0.2.0.toml \
  index/al/alpha_extra/alpha_extra-0.1.0.toml; do
  [ -f "$index_work/$release" ] || \
    fail "$release was not published from its release tag"
  grep -F "commit = \"$alpha_head\"" "$index_work/$release" >/dev/null || \
    fail "$release did not use its tag commit"
done
grep -F 'description = "Tagged source manifest for alpha"' \
  "$index_work/index/al/alpha/alpha-0.2.0.toml" >/dev/null || \
  fail "release metadata did not come from the tagged source manifest"
grep -F 'subdir = "alpha_extra"' \
  "$index_work/index/al/alpha_extra/alpha_extra-0.1.0.toml" >/dev/null || \
  fail "monorepo release did not preserve its origin subdirectory"
git -C "$index_work" log -1 --format=%s | \
  grep -F 'Problem: Tagged crate releases are not indexed' >/dev/null || \
  fail "release commit does not use the repository message format"

# A published version retires the development line, but that manifest must
# remain a source descriptor for discovering later patch releases.
make_source_manifest "$temporary_root/alpha-work/alire.toml" alpha 0.2.1
git -C "$temporary_root/alpha-work" add alire.toml
git -C "$temporary_root/alpha-work" commit -q -m 'Prepare alpha patch release'
git -C "$temporary_root/alpha-work" tag alpha/v0.2.1
git -C "$temporary_root/alpha-work" push -q origin alpha/v0.2.1
patch_work="$temporary_root/index-patch-work"
git clone -q "$index_remote" "$patch_work"
ALR=$fake_alr ALR_LOG=$alr_log \
  "$patch_work/scripts/update-dev-origins.sh" \
  --crate alpha --releases-only --commit
[ -f "$patch_work/index/al/alpha/alpha-0.2.1.toml" ] || \
  fail "retired development source did not discover a later patch release"

# Omitting selectors must retain the original update-all behavior.
all_work="$temporary_root/index-all-work"
git clone -q "$index_remote" "$all_work"
ALR=$fake_alr ALR_LOG=$alr_log \
  "$all_work/scripts/update-dev-origins.sh" --commit
grep -F "commit = \"$beta_head\"" \
  "$all_work/index/be/beta/beta-0.1.0-dev.toml" >/dev/null || \
  fail "unfiltered update did not advance beta's remote HEAD"
grep -F 'commit = "0000000000000000000000000000000000000000"' \
  "$all_work/index/re/released/released-0.9.0-dev.toml" >/dev/null || \
  fail "unfiltered update advanced a retired development manifest"

ALR=$fake_alr ALR_LOG=$alr_log \
  "$index_work/scripts/update-dev-origins.sh" --crate=beta --commit
grep -F "commit = \"$beta_head\"" \
  "$index_work/index/be/beta/beta-0.1.0-dev.toml" >/dev/null || \
  fail "selected beta development manifest did not use beta's remote HEAD"
grep -F 'commit = "0000000000000000000000000000000000000000"' \
  "$index_work/index/re/released/released-0.9.0-dev.toml" >/dev/null || \
  fail "shared-origin selection advanced a retired development manifest"
grep -F 'beta_helper = "~0.2.0-dev"' \
  "$index_work/index/be/beta/beta-0.1.0-dev.toml" >/dev/null || \
  fail "development manifest did not adopt its source dependencies"
grep -F '[[pins]]' \
  "$index_work/index/be/beta/beta-0.1.0-dev.toml" >/dev/null && \
  fail "published manifest kept a local development pin"
grep -F 'path = ' \
  "$index_work/index/be/beta/beta-0.1.0-dev.toml" >/dev/null && \
  fail "published manifest kept a local pin path"
grep -F 'description = "Development source manifest for beta"' \
  "$index_work/index/be/beta/beta-0.1.0-dev.toml" >/dev/null || \
  fail "development manifest did not adopt its source metadata"
grep -F "url = \"git+$beta_remote\"" \
  "$index_work/index/be/beta/beta-0.1.0-dev.toml" >/dev/null || \
  fail "rendered development manifest lost its origin URL"
grep -F 'index --check' "$alr_log" >/dev/null || \
  fail "Alire index validation was not run"
[ -z "$(git -C "$index_work" status --porcelain)" ] || \
  fail "--commit left changes in the index checkout"
git -C "$index_work" log -1 --format=%s | \
  grep -F 'Problem: Development index trails remote sources' >/dev/null || \
  fail "generated commit does not use the repository message format"

unconstrained_work="$temporary_root/index-unconstrained-work"
make_development_source_manifest \
  "$temporary_root/gamma-work/alire.toml" gamma 0.1.0-dev \
  gamma_helper '' gamma_helper
git -C "$temporary_root/gamma-work" add alire.toml
git -C "$temporary_root/gamma-work" commit -q -m 'Pin gamma without a constraint'
git -C "$temporary_root/gamma-work" push -q origin main
git clone -q "$index_remote" "$unconstrained_work"
if ALR=$fake_alr ALR_LOG=$alr_log \
  "$unconstrained_work/scripts/update-dev-origins.sh" --crate gamma \
  >"$temporary_root/unconstrained.stdout" \
  2>"$temporary_root/unconstrained.stderr"; then
  fail "a pin standing in for an unstated version was published"
fi
grep -F 'leaves its dependency unconstrained' \
  "$temporary_root/unconstrained.stderr" >/dev/null || \
  fail "unconstrained pinned dependency was not explained"

printf '%s\n' 'dirty' >>"$index_work/REMOTE.md"
if ALR=$fake_alr ALR_LOG=$alr_log \
  "$index_work/scripts/update-dev-origins.sh" \
  >"$temporary_root/dirty.stdout" 2>"$temporary_root/dirty.stderr"; then
  fail "dirty index checkout was accepted"
fi
grep -F 'the index checkout is not clean' \
  "$temporary_root/dirty.stderr" >/dev/null || \
  fail "dirty checkout failure was not explained"

printf '%s\n' 'update-dev-origins test: PASS'
