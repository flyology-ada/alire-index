#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
website_kit=${WEBSITE_KIT_DIR:-"$repo_root/vendor/website-kit"}
site_dir=${SITE_DIR:-"$repo_root/build/site"}
python_bin=${PYTHON:-python3}

if [[ ! -f "$website_kit/scripts/install-assets.mjs" ]]; then
  printf 'website-kit is unavailable at %s\n' "$website_kit" >&2
  printf 'set WEBSITE_KIT_DIR or check out flyology-ada/website-kit there\n' >&2
  exit 2
fi

if ! "$python_bin" -c 'import tomllib' 2>/dev/null; then
  printf '%s must provide Python 3.11 or newer (tomllib is required)\n' "$python_bin" >&2
  exit 2
fi

"$python_bin" "$repo_root/scripts/generate-site.py" --output "$site_dir"
node "$website_kit/scripts/install-assets.mjs" "$site_dir"
node "$website_kit/scripts/check-site.mjs" "$site_dir"
