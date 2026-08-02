#!/usr/bin/env bash
set -euo pipefail

repo=/srv/feishu-dify-gateway
tools_dir=$repo/.tools
uv_bin=$tools_dir/uv
uv_image=ghcr.io/astral-sh/uv:0.12.1

if [[ $EUID -eq 0 ]]; then
  printf 'Run this script as the repository owner, not root.\n' >&2
  exit 1
fi
if [[ ! -d $repo/.git ]]; then
  printf 'Expected repository is missing: %s\n' "$repo" >&2
  exit 1
fi

install -d -m 700 "$tools_dir" "$repo/.python" "$repo/uv-cache"
if [[ ! -x $uv_bin ]]; then
  container_id=$(docker create "$uv_image")
  cleanup() {
    docker rm -f "$container_id" >/dev/null 2>&1 || true
  }
  trap cleanup EXIT
  docker cp "$container_id:/uv" "$uv_bin"
  chmod 700 "$uv_bin"
  docker rm "$container_id" >/dev/null
  trap - EXIT
fi

export UV_PYTHON_INSTALL_DIR=$repo/.python
export UV_CACHE_DIR=$repo/uv-cache
"$uv_bin" sync --frozen --no-dev --python 3.12
"$repo/.venv/bin/python" -c \
  'import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)'
printf 'Cloud Python 3.12 runtime is ready from the locked project environment.\n'
