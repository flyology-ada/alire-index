#!/bin/sh
set -eu

usage () {
  cat <<'EOF'
Usage: ./scripts/update-dev-origins.sh [--commit] [--push]

Fast-forward this index checkout, then pin every *-dev.toml manifest to the
HEAD advertised by its configured Git origin. Manifests that share an origin
are advanced to the same exact commit.

  --commit  commit the validated manifest updates
  --push    commit the updates and push the current branch
  -h, --help
            show this help

The checkout must be clean, attached to a branch, and have an upstream.
Alire is taken from $ALR when set, or from PATH otherwise.
EOF
}

fail () {
  printf '%s\n' "update-dev-origins.sh: $*" >&2
  exit 1
}

commit_changes=0
push_changes=0

while [ "$#" -gt 0 ]; do
  case $1 in
    --commit)
      commit_changes=1
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

manifest_list="$temporary_root/manifests"
remote_heads="$temporary_root/remote-heads"
update_plan="$temporary_root/update-plan"
: >"$remote_heads"
: >"$update_plan"

(
  cd "$index_root"
  find index -type f -name '*-dev.toml' -print | LC_ALL=C sort
) >"$manifest_list"
[ -s "$manifest_list" ] || fail "no development manifests were found"

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

while IFS= read -r relative_manifest; do
  manifest="$index_root/$relative_manifest"
  origin_url=$(origin_field url "$manifest") || \
    fail "$relative_manifest must contain exactly one quoted [origin] URL"
  old_commit=$(origin_field commit "$manifest") || \
    fail "$relative_manifest must contain exactly one quoted [origin] commit"

  case $origin_url in
    *"$(printf '\t')"*) fail "$relative_manifest contains a tab in its origin URL" ;;
    git+*) remote_url=${origin_url#git+} ;;
    *) fail "$relative_manifest origin is not a Git URL: $origin_url" ;;
  esac
  case $remote_url in
    *'#'*) fail "$relative_manifest origin URL must not contain a revision fragment" ;;
  esac

  new_commit=$(awk -F '\t' -v url="$origin_url" \
    '$1 == url { print $2; exit }' "$remote_heads")
  if [ -z "$new_commit" ]; then
    printf '%s\n' "Resolving $origin_url..."
    git ls-remote --symref "$remote_url" HEAD \
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
  fi

  if [ "$old_commit" != "$new_commit" ]; then
    printf '%s\t%s\t%s\n' \
      "$relative_manifest" "$old_commit" "$new_commit" >>"$update_plan"
  fi
done <"$manifest_list"

if [ ! -s "$update_plan" ]; then
  printf '%s\n' "All development manifests already match their remote HEADs."
  exit 0
fi

tab=$(printf '\t')
while IFS="$tab" read -r relative_manifest old_commit new_commit; do
  manifest="$index_root/$relative_manifest"
  rendered="$temporary_root/rendered"
  awk -v new_commit="$new_commit" '
    /^\[[^]]+\][[:space:]]*$/ {
      in_origin = ($0 == "[origin]")
    }
    in_origin && /^[[:space:]]*commit[[:space:]]*=/ {
      print "commit = \"" new_commit "\""
      replaced++
      next
    }
    { print }
    END {
      if (replaced != 1) {
        exit 1
      }
    }
  ' "$manifest" >"$rendered" || \
    fail "could not update the [origin] commit in $relative_manifest"
  mv "$rendered" "$manifest"
  printf '%s\n' \
    "$relative_manifest: $old_commit -> $new_commit"
done <"$update_plan"

git -C "$index_root" diff --check

settings_dir="$temporary_root/alire-settings"
mkdir "$settings_dir"
"$alr" -n -s "$settings_dir" index \
  --add="$index_root" --name=flyologyupdate >/dev/null
"$alr" -n -s "$settings_dir" index --check

if [ "$commit_changes" -eq 1 ]; then
  while IFS="$tab" read -r relative_manifest old_commit new_commit; do
    git -C "$index_root" add -- "$relative_manifest"
  done <"$update_plan"

  message_file="$temporary_root/commit-message"
  cat >"$message_file" <<'EOF'
Problem: Development index trails remote sources

Development manifests no longer all resolve to the default-branch heads
published by their configured Git origins.

Solution: Advance development source pins

Update every changed development manifest to the exact current commit
advertised by its remote default branch.
EOF
  git -C "$index_root" commit -F "$message_file"
fi

if [ "$push_changes" -eq 1 ]; then
  git -C "$index_root" push "$upstream_remote" "HEAD:$upstream_branch"
fi

if [ "$commit_changes" -eq 0 ]; then
  printf '%s\n' \
    "Validated updates are ready for review; rerun with --commit or --push."
elif [ "$push_changes" -eq 0 ]; then
  printf '%s\n' "Committed updates; use git push to publish them."
else
  printf '%s\n' "Committed and pushed the development manifest updates."
fi
