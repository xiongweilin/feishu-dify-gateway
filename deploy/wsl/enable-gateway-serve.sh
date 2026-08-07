#!/usr/bin/env bash
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  printf 'Run this script with sudo.\n' >&2
  exit 1
fi

runtime_dir=/srv/stack/feishu-dify-gateway/runtime
baseline=$runtime_dir/tailscale-serve-before-gateway.json
after=$runtime_dir/tailscale-serve-after-gateway.json
target=127.0.0.1:18082

install -d -m 700 "$runtime_dir"
temp=$(mktemp "$runtime_dir/.tailscale-baseline.XXXXXX")
trap 'rm -f "$temp"' EXIT
tailscale serve status --json > "$temp"
chmod 600 "$temp"

existing=$(jq -r '.TCP["8082"].TCPForward // empty' "$temp")
if [[ -n $existing && $existing != "$target" ]]; then
  printf 'TCP 8082 already points to an unexpected target.\n' >&2
  exit 1
fi
if [[ ! -e $baseline ]]; then
  mv "$temp" "$baseline"
  temp=$(mktemp "$runtime_dir/.tailscale-baseline.XXXXXX")
fi

tailscale serve --bg --yes --tcp=8082 "tcp://$target"
tailscale serve status --json > "$after"
chmod 600 "$after"

if [[ $(jq -r '.TCP["8082"].TCPForward // empty' "$after") != "$target" ]]; then
  printf 'TCP 8082 verification failed.\n' >&2
  exit 1
fi
if ! diff -u \
  <(jq -S '(.TCP // {}) | del(.["8082"])' "$baseline") \
  <(jq -S '(.TCP // {}) | del(.["8082"])' "$after"); then
  printf 'An unrelated Tailscale TCP mapping drifted. Restore the saved baseline.\n' >&2
  exit 1
fi

curl --fail --silent --show-error http://127.0.0.1:18082/healthz >/dev/null
printf 'Gateway Tailscale TCP mapping is active and unrelated mappings are unchanged.\n'
