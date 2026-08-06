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

make_source () {
  source_name=$1
  source_work="$temporary_root/$source_name-work"
  source_remote="$temporary_root/$source_name.git"
  git init -q --bare "$source_remote"
  git -C "$source_remote" symbolic-ref HEAD refs/heads/main
  git init -q --initial-branch=main "$source_work"
  printf '%s\n' "$source_name remote release" >"$source_work/release.txt"
  git -C "$source_work" add release.txt
  git -C "$source_work" commit -q -m "Publish $source_name"
  git -C "$source_work" remote add origin "$source_remote"
  git -C "$source_work" push -q -u origin main
}

make_manifest () {
  manifest=$1
  crate_name=$2
  version=$3
  origin=$4
  mkdir -p "$(dirname -- "$manifest")"
  {
    printf 'name = "%s"\n' "$crate_name"
    printf 'version = "%s"\n\n' "$version"
    printf '[origin]\n'
    printf 'commit = "0000000000000000000000000000000000000000"\n'
    printf 'url = "git+%s"\n' "$origin"
  } >"$manifest"
}

make_source alpha
make_source beta
alpha_remote="$temporary_root/alpha.git"
beta_remote="$temporary_root/beta.git"
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
  alpha_extra 0.1.0-dev "$alpha_remote"
make_manifest \
  "$index_seed/index/be/beta/beta-0.1.0-dev.toml" \
  beta 0.1.0-dev "$beta_remote"
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

ALR=$fake_alr ALR_LOG=$alr_log \
  "$index_work/scripts/update-dev-origins.sh" --crate missing \
  >"$temporary_root/missing.stdout" 2>"$temporary_root/missing.stderr" && \
  fail "unknown crate selector was accepted"
grep -F 'no release manifest is named missing' \
  "$temporary_root/missing.stderr" >/dev/null || \
  fail "unknown crate selector failure was not explained"

ALR=$fake_alr ALR_LOG=$alr_log \
  "$index_work/scripts/update-dev-origins.sh" --crate released \
  >"$temporary_root/released.stdout"
grep -F 'no active development manifest remains' \
  "$temporary_root/released.stdout" >/dev/null || \
  fail "retired development selector was not explained"

ALR=$fake_alr ALR_LOG=$alr_log \
  "$index_work/scripts/update-dev-origins.sh" --crate alpha_extra --push

[ -f "$index_work/REMOTE.md" ] || \
  fail "stale index checkout was not fast-forwarded"
for manifest in \
  index/al/alpha/alpha-0.2.0-dev.toml \
  index/al/alpha_extra/alpha_extra-0.1.0-dev.toml; do
  grep -F "commit = \"$alpha_head\"" "$index_work/$manifest" >/dev/null || \
    fail "$manifest did not use alpha's remote HEAD"
done
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
grep -F 'index --check' "$alr_log" >/dev/null || \
  fail "Alire index validation was not run"
[ -z "$(git -C "$index_work" status --porcelain)" ] || \
  fail "--commit left changes in the index checkout"
git -C "$index_work" log -1 --format=%s | \
  grep -F 'Problem: Development index trails remote sources' >/dev/null || \
  fail "generated commit does not use the repository message format"

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
