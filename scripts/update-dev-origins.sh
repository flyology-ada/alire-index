#!/bin/sh
set -eu

usage () {
  cat <<'EOF'
Usage: ./scripts/update-dev-origins.sh [--crate NAME]... [--releases-only]
                                      [--commit] [--push]

Fast-forward this index checkout, publish releases tagged <crate>/v<version>,
then pin every active -dev manifest to the HEAD advertised by its configured
Git origin. Each -dev manifest is re-rendered from the alire.toml at that
commit plus a generated [origin] table, so its dependencies track the source.
Manifests that share an origin are advanced to the same exact commit. A -dev
manifest whose source declares a different version is reported and skipped. A
stable release retires development versions of the same crate at that semantic
version or lower on subsequent runs.

  --crate NAME
            update only the origin group containing NAME; may be repeated
  --releases-only
            publish matching tags without advancing development origins
  --commit  commit the validated manifest updates
  --push    commit the updates and push the current branch
  -h, --help
            show this help

With no --crate option, every development origin is scanned. Selecting one
crate scans all development manifests that share its origin so packages from
one source repository cannot acquire inconsistent pins. Release tags use the
form <crate>/v<version>, and the tagged alire.toml must declare that same crate
name and stable semantic version.

The checkout must be clean, attached to a branch, and have an upstream. Alire
is taken from $ALR when set, or from PATH otherwise.
EOF
}

fail () {
  printf '%s\n' "update-dev-origins.sh: $*" >&2
  exit 1
}

commit_changes=0
push_changes=0
update_development=1
selected_crates=

select_crate () {
  crate_name=$1
  case $crate_name in
    ''|*[!a-z0-9_]*) fail "invalid crate name: $crate_name" ;;
  esac
  case " $selected_crates " in
    *" $crate_name "*) ;;
    *) selected_crates="${selected_crates}${selected_crates:+ }$crate_name" ;;
  esac
}

while [ "$#" -gt 0 ]; do
  case $1 in
    --crate)
      [ "$#" -ge 2 ] || fail "--crate requires a crate name"
      shift
      select_crate "$1"
      ;;
    --crate=*)
      select_crate "${1#--crate=}"
      ;;
    --commit)
      commit_changes=1
      ;;
    --releases-only)
      update_development=0
      ;;
    --push)
      commit_changes=1
      push_changes=1
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      usage >&2
      fail "unknown option: $1"
      ;;
  esac
  shift
done

command -v git >/dev/null 2>&1 || fail "git was not found on PATH"
command -v awk >/dev/null 2>&1 || fail "awk was not found on PATH"

if [ -n "${ALR:-}" ]; then
  [ -x "$ALR" ] || fail "ALR is not executable: $ALR"
  alr=$ALR
elif command -v alr >/dev/null 2>&1; then
  alr=$(command -v alr)
else
  fail "Alire was not found; set ALR or add alr to PATH"
fi

index_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
[ -f "$index_root/index/index.toml" ] || \
  fail "$index_root is not an Alire index checkout"

if [ -n "$(git -C "$index_root" status --porcelain)" ]; then
  fail "the index checkout is not clean"
fi

current_branch=$(git -C "$index_root" symbolic-ref --quiet --short HEAD) || \
  fail "the index checkout has a detached HEAD"
upstream=$(git -C "$index_root" rev-parse \
  --abbrev-ref --symbolic-full-name '@{upstream}' 2>/dev/null) || \
  fail "branch $current_branch has no upstream"

case $upstream in
  */*) ;;
  *) fail "cannot identify the upstream remote and branch from $upstream" ;;
esac
upstream_remote=${upstream%%/*}
upstream_branch=${upstream#*/}

printf '%s\n' "Refreshing index checkout from $upstream..."
git -C "$index_root" fetch "$upstream_remote" "$upstream_branch"
git -C "$index_root" merge --ff-only "$upstream"

local_head=$(git -C "$index_root" rev-parse HEAD)
upstream_head=$(git -C "$index_root" rev-parse "$upstream")
[ "$local_head" = "$upstream_head" ] || \
  fail "the index checkout contains commits not present at $upstream"

temporary_root=$(mktemp -d "${TMPDIR:-/tmp}/alire-index-update.XXXXXX")
cleanup () {
  rm -rf -- "$temporary_root"
}
trap cleanup EXIT
trap 'exit 1' HUP INT TERM

catalog_list="$temporary_root/catalog-list"
catalog_inventory="$temporary_root/catalog-inventory"
development_candidates="$temporary_root/development-candidates"
stable_versions="$temporary_root/stable-versions"
manifest_inventory="$temporary_root/manifest-inventory"
release_sources="$temporary_root/release-sources"
remote_heads="$temporary_root/remote-heads"
remote_tags="$temporary_root/remote-tags"
source_repositories="$temporary_root/source-repositories"
selected_origins="$temporary_root/selected-origins"
update_plan="$temporary_root/update-plan"
release_plan="$temporary_root/release-plan"
: >"$catalog_inventory"
: >"$development_candidates"
: >"$stable_versions"
: >"$manifest_inventory"
: >"$release_sources"
: >"$remote_heads"
: >"$remote_tags"
: >"$source_repositories"
: >"$selected_origins"
: >"$update_plan"
: >"$release_plan"
rendered_root="$temporary_root/rendered"
mkdir "$rendered_root"
rendered_count=0

(
  cd "$index_root"
  find index -type f -name '*.toml' ! -path 'index/index.toml' -print | \
    LC_ALL=C sort
) >"$catalog_list"
[ -s "$catalog_list" ] || fail "no release manifests were found"

top_level_field () {
  field=$1
  manifest=$2
  awk -v field="$field" '
    BEGIN { in_top_level = 1 }
    /^\[[^]]+\][[:space:]]*$/ { in_top_level = 0 }
    in_top_level && $0 ~ "^[[:space:]]*" field "[[:space:]]*=" {
      value = $0
      if (value !~ /^[^=]+=[[:space:]]*"[^"]+"[[:space:]]*$/) {
        invalid = 1
        next
      }
      sub(/^[^=]+=[[:space:]]*"/, "", value)
      sub(/"[[:space:]]*$/, "", value)
      found++
      result = value
    }
    END {
      if (invalid || found != 1) {
        exit 1
      }
      print result
    }
  ' "$manifest"
}

origin_field () {
  field=$1
  manifest=$2
  awk -v field="$field" '
    /^\[[^]]+\][[:space:]]*$/ {
      in_origin = ($0 == "[origin]")
    }
    in_origin && $0 ~ "^[[:space:]]*" field "[[:space:]]*=" {
      value = $0
      if (value !~ /^[^=]+=[[:space:]]*"[^"]+"[[:space:]]*$/) {
        invalid = 1
        next
      }
      sub(/^[^=]+=[[:space:]]*"/, "", value)
      sub(/"[[:space:]]*$/, "", value)
      found++
      result = value
    }
    END {
      if (invalid || found != 1) {
        exit 1
      }
      print result
    }
  ' "$manifest"
}

origin_optional_field () {
  field=$1
  manifest=$2
  awk -v field="$field" '
    /^\[[^]]+\][[:space:]]*$/ {
      in_origin = ($0 == "[origin]")
    }
    in_origin && $0 ~ "^[[:space:]]*" field "[[:space:]]*=" {
      value = $0
      if (value !~ /^[^=]+=[[:space:]]*"[^"]+"[[:space:]]*$/) {
        invalid = 1
        next
      }
      sub(/^[^=]+=[[:space:]]*"/, "", value)
      sub(/"[[:space:]]*$/, "", value)
      found++
      result = value
    }
    END {
      if (invalid || found > 1) {
        exit 1
      }
      if (found == 1) {
        print result
      }
    }
  ' "$manifest"
}

semver_core_is_valid () {
  awk -v version="$1" '
    BEGIN {
      if (split(version, part, ".") != 3) {
        exit 1
      }
      for (i = 1; i <= 3; i++) {
        if (part[i] !~ /^[0-9]+$/) {
          exit 1
        }
      }
    }
  '
}

semver_at_least () {
  candidate=${1%%+*}
  target=$2
  awk -v candidate="$candidate" -v target="$target" '
    BEGIN {
      split(candidate, left, ".")
      split(target, right, ".")
      for (i = 1; i <= 3; i++) {
        if ((left[i] + 0) > (right[i] + 0)) {
          exit 0
        }
        if ((left[i] + 0) < (right[i] + 0)) {
          exit 1
        }
      }
      exit 0
    }
  '
}

while IFS= read -r relative_manifest; do
  manifest="$index_root/$relative_manifest"
  crate_name=$(top_level_field name "$manifest") || \
    fail "$relative_manifest must contain exactly one quoted crate name"
  version=$(top_level_field version "$manifest") || \
    fail "$relative_manifest must contain exactly one quoted version"

  case $crate_name in
    ''|*[!a-z0-9_]*) fail "$relative_manifest has an invalid crate name" ;;
  esac
  case "$crate_name$version" in
    *"$(printf '\t')"*) fail "$relative_manifest contains a tab in its identity" ;;
  esac

  version_without_build=${version%%+*}
  version_core=${version_without_build%%-*}
  semver_core_is_valid "$version_core" || \
    fail "$relative_manifest has an unsupported semantic version: $version"

  printf '%s\t%s\t%s\n' \
    "$relative_manifest" "$crate_name" "$version" >>"$catalog_inventory"
  if [ "$version_without_build" = "${version_core}-dev" ]; then
    printf '%s\t%s\t%s\t%s\n' \
      "$relative_manifest" "$crate_name" "$version" "$version_core" \
      >>"$development_candidates"
  elif [ "$version_without_build" = "$version_core" ]; then
    printf '%s\t%s\n' "$crate_name" "$version" >>"$stable_versions"
  fi
done <"$catalog_list"

tab=$(printf '\t')
while IFS="$tab" read -r \
  relative_manifest crate_name version development_core; do
  manifest="$index_root/$relative_manifest"
  origin_url=$(origin_field url "$manifest") || \
    fail "$relative_manifest must contain exactly one quoted [origin] URL"
  old_commit=$(origin_field commit "$manifest") || \
    fail "$relative_manifest must contain exactly one quoted [origin] commit"

  case $origin_url in
    *"$(printf '\t')"*) fail "$relative_manifest contains a tab in its origin URL" ;;
    git+*) ;;
    *) fail "$relative_manifest origin is not a Git URL: $origin_url" ;;
  esac
  case ${origin_url#git+} in
    *'#'*) fail "$relative_manifest origin URL must not contain a revision fragment" ;;
  esac

  printf '%s\t%s\t%s\t%s\n' \
    "$relative_manifest" "$crate_name" "$origin_url" "$old_commit" \
    >>"$release_sources"

  retired_by=
  while IFS="$tab" read -r stable_crate stable_version; do
    [ "$stable_crate" = "$crate_name" ] || continue
    if semver_at_least "$stable_version" "$development_core"; then
      retired_by=$stable_version
      break
    fi
  done <"$stable_versions"

  if [ -n "$retired_by" ]; then
    printf '%s\n' \
      "Ignoring $crate_name $version: stable $retired_by is at least as new."
    continue
  fi

  printf '%s\t%s\t%s\t%s\n' \
    "$relative_manifest" "$crate_name" "$origin_url" "$old_commit" \
    >>"$manifest_inventory"
done <"$development_candidates"

if [ -z "$selected_crates" ]; then
  if [ ! -s "$release_sources" ]; then
    printf '%s\n' "No development manifests identify repositories to scan."
    exit 0
  fi
  awk -F '\t' '{ print $3 }' "$release_sources" | \
    LC_ALL=C sort -u >"$selected_origins"
else
  for requested_crate in $selected_crates; do
    if ! awk -F '\t' -v crate="$requested_crate" '
      $2 == crate {
        print $3
        found = 1
      }
      END { if (!found) exit 1 }
    ' "$release_sources" >>"$selected_origins"; then
      fail "no development manifest is named $requested_crate"
    fi
  done
  LC_ALL=C sort -u "$selected_origins" >"$temporary_root/sorted-origins"
  mv "$temporary_root/sorted-origins" "$selected_origins"
fi

source_repository_count=0

ensure_source_repository () {
  ensure_origin_url=$1
  source_repository=$(awk -F '\t' -v url="$ensure_origin_url" \
    '$1 == url { print $2; exit }' "$source_repositories")
  if [ -z "$source_repository" ]; then
    source_repository_count=$((source_repository_count + 1))
    source_repository="$temporary_root/source-$source_repository_count.git"
    git init -q --bare "$source_repository"
    printf '%s\t%s\n' \
      "$ensure_origin_url" "$source_repository" >>"$source_repositories"
  fi
}

# An index manifest is its source manifest plus an [origin] table, so render
# development entries from the source at the pinned commit instead of editing
# the commit in place. Rewriting only the commit lets the recorded dependencies
# drift away from the sources they claim to describe.
render_development_manifest () {
  render_relative_manifest=$1
  render_crate_name=$2
  render_version=$3
  render_origin_url=$4
  render_commit=$5
  render_destination=$6

  render_subdir=$(origin_optional_field subdir \
    "$index_root/$render_relative_manifest") || \
    fail "$render_relative_manifest has an invalid [origin] subdir"
  case $render_subdir in
    *"$(printf '\t')"*)
      fail "$render_relative_manifest contains a tab in its origin subdir"
      ;;
    /*|..|../*|*/..|*/../*)
      fail "$render_relative_manifest has an unsafe [origin] subdir: $render_subdir"
      ;;
  esac

  ensure_source_repository "$render_origin_url"
  git --git-dir="$source_repository" fetch -q --depth=1 \
    "${render_origin_url#git+}" HEAD || \
    fail "could not fetch the default branch of $render_origin_url"
  render_fetched_commit=$(git --git-dir="$source_repository" \
    rev-parse 'FETCH_HEAD^{commit}') || \
    fail "the default branch of $render_origin_url does not resolve to a commit"
  [ "$render_fetched_commit" = "$render_commit" ] || \
    fail "$render_origin_url moved while the index was being updated"

  render_source_manifest=${render_subdir:+$render_subdir/}alire.toml
  render_candidate="$temporary_root/development-candidate"
  git --git-dir="$source_repository" show \
    "FETCH_HEAD:$render_source_manifest" >"$render_candidate" || \
    fail "$render_origin_url does not contain $render_source_manifest"

  render_source_name=$(top_level_field name "$render_candidate") || \
    fail "$render_source_manifest at $render_commit must contain exactly one quoted crate name"
  render_source_version=$(top_level_field version "$render_candidate") || \
    fail "$render_source_manifest at $render_commit must contain exactly one quoted version"
  [ "$render_source_name" = "$render_crate_name" ] || \
    fail "$render_source_manifest at $render_commit declares crate $render_source_name"
  if [ "$render_source_version" != "$render_version" ]; then
    printf '%s\n' \
      "Ignoring $render_relative_manifest: its source now declares $render_source_version."
    return 1
  fi
  if awk '
    /^[[:space:]]*\[origin\][[:space:]]*$/ { found = 1 }
    END { exit !found }
  ' "$render_candidate"; then
    fail "$render_source_manifest at $render_commit already contains an [origin] table"
  fi

  cp "$render_candidate" "$render_destination"
  printf '\n[origin]\ncommit = "%s"\n' "$render_commit" >>"$render_destination"
  if [ -n "$render_subdir" ]; then
    printf 'subdir = "%s"\n' "$render_subdir" >>"$render_destination"
  fi
  printf 'url = "%s"\n' "$render_origin_url" >>"$render_destination"
}

while IFS="$tab" read -r \
  relative_manifest crate_name origin_url old_commit; do
  if ! awk -v origin="$origin_url" \
    '$0 == origin { found = 1 } END { exit !found }' "$selected_origins"; then
    continue
  fi

  remote_url=${origin_url#git+}

  new_commit=$(awk -F '\t' -v url="$origin_url" \
    '$1 == url { print $2; exit }' "$remote_heads")
  if [ -z "$new_commit" ]; then
    printf '%s\n' "Resolving $origin_url..."
    git ls-remote --symref "$remote_url" HEAD 'refs/tags/*' \
      >"$temporary_root/ls-remote" || \
      fail "could not resolve remote HEAD for $origin_url"
    new_commit=$(awk '$2 == "HEAD" && $1 != "ref:" { print $1; exit }' \
      "$temporary_root/ls-remote")
    case $new_commit in
      *[!0-9a-fA-F]*|'')
        fail "remote HEAD for $origin_url is not a commit ID"
        ;;
    esac
    [ "${#new_commit}" -ge 40 ] || \
      fail "remote HEAD for $origin_url is not a full commit ID"
    printf '%s\t%s\n' "$origin_url" "$new_commit" >>"$remote_heads"

    awk '
      $2 ~ /^refs\/tags\// {
        ref = $2
        sub(/^refs\/tags\//, "", ref)
        if (ref ~ /\^\{\}$/) {
          sub(/\^\{\}$/, "", ref)
          peeled[ref] = $1
        } else {
          direct[ref] = $1
        }
      }
      END {
        for (ref in direct) {
          print ref "\t" (ref in peeled ? peeled[ref] : direct[ref])
        }
      }
    ' "$temporary_root/ls-remote" | LC_ALL=C sort \
      >"$temporary_root/resolved-tags"
    while IFS="$tab" read -r tag_name tag_commit; do
      [ -n "$tag_name" ] || continue
      case $tag_commit in
        *[!0-9a-fA-F]*|'')
          fail "tag $tag_name at $origin_url does not resolve to a commit ID"
          ;;
      esac
      [ "${#tag_commit}" -ge 40 ] || \
        fail "tag $tag_name at $origin_url does not use a full commit ID"
      printf '%s\t%s\t%s\n' \
        "$origin_url" "$tag_name" "$tag_commit" >>"$remote_tags"
    done <"$temporary_root/resolved-tags"
  fi

  while IFS="$tab" read -r tag_origin tag_name tag_commit; do
    [ "$tag_origin" = "$origin_url" ] || continue
    case $tag_name in
      "$crate_name"/v*) release_version=${tag_name#"$crate_name"/v} ;;
      *) continue ;;
    esac
    semver_core_is_valid "$release_version" || continue
    if awk -F '\t' -v crate="$crate_name" -v version="$release_version" '
      $2 == crate && $3 == version { found = 1 }
      END { exit !found }
    ' "$catalog_inventory"; then
      continue
    fi
    release_directory=${relative_manifest%/*}
    release_manifest="$release_directory/$crate_name-$release_version.toml"
    if awk -F '\t' -v manifest="$release_manifest" '
      $2 == manifest { found = 1 }
      END { exit !found }
    ' "$release_plan"; then
      continue
    fi
    if [ -e "$index_root/$release_manifest" ]; then
      fail "release tag $tag_name would overwrite $release_manifest"
    fi
    subdir=$(origin_optional_field subdir "$index_root/$relative_manifest") || \
      fail "$relative_manifest has an invalid [origin] subdir"
    case $subdir in
      *"$(printf '\t')"*)
        fail "$relative_manifest contains a tab in its origin subdir"
        ;;
      /*|..|../*|*/..|*/../*)
        fail "$relative_manifest has an unsafe [origin] subdir: $subdir"
        ;;
    esac
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
      "$relative_manifest" "$release_manifest" "$crate_name" \
      "$release_version" "$origin_url" "$tag_commit" "$tag_name" \
      "$subdir" >>"$release_plan"
  done <"$remote_tags"

  if [ "$update_development" -eq 1 ] && \
    awk -F '\t' -v manifest="$relative_manifest" '
      $1 == manifest { found = 1 }
      END { exit !found }
    ' "$manifest_inventory"; then
    development_version=$(top_level_field version \
      "$index_root/$relative_manifest") || \
      fail "$relative_manifest must contain exactly one quoted version"
    rendered_count=$((rendered_count + 1))
    rendered_manifest="$rendered_root/$rendered_count.toml"
    if render_development_manifest "$relative_manifest" "$crate_name" \
      "$development_version" "$origin_url" "$new_commit" \
      "$rendered_manifest" && \
      ! cmp -s "$rendered_manifest" "$index_root/$relative_manifest"; then
      printf '%s\t%s\t%s\t%s\n' \
        "$relative_manifest" "$old_commit" "$new_commit" \
        "$rendered_manifest" >>"$update_plan"
    fi
  fi
done <"$release_sources"

if [ ! -s "$update_plan" ] && [ ! -s "$release_plan" ]; then
  if [ "$update_development" -eq 1 ]; then
    printf '%s\n' \
      "No matching releases; selected development manifests already match their sources."
  else
    printf '%s\n' "No matching releases were found."
  fi
  exit 0
fi

while IFS="$tab" read -r \
  development_manifest release_manifest crate_name version origin_url \
  tag_commit tag_name subdir; do
  remote_url=${origin_url#git+}
  ensure_source_repository "$origin_url"

  printf '%s\n' "Publishing $tag_name from $origin_url..."
  git --git-dir="$source_repository" fetch -q --depth=1 \
    "$remote_url" "refs/tags/$tag_name" || \
    fail "could not fetch release tag $tag_name from $origin_url"
  fetched_commit=$(git --git-dir="$source_repository" \
    rev-parse 'FETCH_HEAD^{commit}') || \
    fail "release tag $tag_name does not resolve to a commit"
  [ "$fetched_commit" = "$tag_commit" ] || \
    fail "release tag $tag_name changed while the index was being updated"

  source_manifest=${subdir:+$subdir/}alire.toml
  candidate="$temporary_root/release-candidate"
  git --git-dir="$source_repository" show \
    "FETCH_HEAD:$source_manifest" >"$candidate" || \
    fail "release tag $tag_name does not contain $source_manifest"
  tagged_name=$(top_level_field name "$candidate") || \
    fail "$source_manifest at $tag_name must contain exactly one quoted crate name"
  tagged_version=$(top_level_field version "$candidate") || \
    fail "$source_manifest at $tag_name must contain exactly one quoted version"
  [ "$tagged_name" = "$crate_name" ] || \
    fail "$source_manifest at $tag_name declares crate $tagged_name"
  [ "$tagged_version" = "$version" ] || \
    fail "$source_manifest at $tag_name declares version $tagged_version"
  if awk '
    /^[[:space:]]*\[origin\][[:space:]]*$/ { found = 1 }
    END { exit !found }
  ' "$candidate"; then
    fail "$source_manifest at $tag_name already contains an [origin] table"
  fi

  destination="$index_root/$release_manifest"
  cp "$candidate" "$destination"
  printf '\n[origin]\ncommit = "%s"\n' "$tag_commit" >>"$destination"
  if [ -n "$subdir" ]; then
    printf 'subdir = "%s"\n' "$subdir" >>"$destination"
  fi
  printf 'url = "%s"\n' "$origin_url" >>"$destination"
  printf '%s\n' "$release_manifest: published from $tag_name"
done <"$release_plan"

while IFS="$tab" read -r \
  relative_manifest old_commit new_commit rendered_manifest; do
  cp "$rendered_manifest" "$index_root/$relative_manifest"
  if [ "$old_commit" = "$new_commit" ]; then
    printf '%s\n' "$relative_manifest: refreshed at $new_commit"
  else
    printf '%s\n' "$relative_manifest: $old_commit -> $new_commit"
  fi
done <"$update_plan"

git -C "$index_root" diff --check

settings_dir="$temporary_root/alire-settings"
mkdir "$settings_dir"
"$alr" -n -s "$settings_dir" index \
  --add="$index_root" --name=flyologyupdate >/dev/null
"$alr" -n -s "$settings_dir" index --check

if [ "$commit_changes" -eq 1 ]; then
  while IFS="$tab" read -r \
    development_manifest release_manifest crate_name version origin_url \
    tag_commit tag_name subdir; do
    git -C "$index_root" add -- "$release_manifest"
  done <"$release_plan"
  while IFS="$tab" read -r relative_manifest old_commit new_commit; do
    git -C "$index_root" add -- "$relative_manifest"
  done <"$update_plan"

  message_file="$temporary_root/commit-message"
  if [ -s "$release_plan" ]; then
    cat >"$message_file" <<'EOF'
Problem: Tagged crate releases are not indexed

Stable releases published by their source repositories remain unavailable to
Alire users until matching immutable manifests enter this index.

Solution: Publish validated tagged crate releases

Import each <crate>/v<version> release from its exact tagged source commit and
record it as an immutable index manifest. Development source pins are advanced
too unless the updater was invoked with --releases-only.
EOF
  else
    cat >"$message_file" <<'EOF'
Problem: Development index trails remote sources

Development manifests no longer all resolve to the default-branch heads
published by their configured Git origins.

Solution: Advance development source pins

Update every changed development manifest to the exact current commit
advertised by its remote default branch.
EOF
  fi
  git -C "$index_root" commit -F "$message_file"
fi

if [ "$push_changes" -eq 1 ]; then
  git -C "$index_root" push "$upstream_remote" "HEAD:$upstream_branch"
fi

if [ "$commit_changes" -eq 0 ]; then
  printf '%s\n' \
    "Validated updates are ready for review; rerun with --commit or --push."
elif [ "$push_changes" -eq 0 ]; then
  printf '%s\n' "Committed index updates; use git push to publish them."
else
  printf '%s\n' "Committed and pushed the index updates."
fi
