#!/usr/bin/env bash
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  printf 'Run this script with sudo.\n' >&2
  exit 1
fi

baseline=${1:-/srv/stack/feishu-dify-gateway/runtime/tailscale-serve-before-gateway.json}
if [[ ! -f $baseline || -L $baseline ]]; then
  printf 'A regular baseline file is required.\n' >&2
  exit 1
fi
if ! jq -e '((keys - ["TCP"]) | length) == 0' "$baseline" >/dev/null; then
  printf 'This rollback script only supports a TCP-only baseline.\n' >&2
  exit 1
fi

tailscale serve reset
while IFS=$'\t' read -r port target; do
  [[ $port =~ ^[0-9]+$ && -n $target ]] || exit 1
  tailscale serve --bg --yes --tcp="$port" "tcp://$target"
done < <(jq -r '(.TCP // {}) | to_entries[] | [.key, .value.TCPForward] | @tsv' "$baseline")

current=$(mktemp)
trap 'rm -f "$current"' EXIT
tailscale serve status --json > "$current"
diff -u <(jq -S '.TCP // {}' "$baseline") <(jq -S '.TCP // {}' "$current")
printf 'Tailscale TCP baseline restored.\n'
