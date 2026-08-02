#!/usr/bin/env bash
set -euo pipefail

target_dir=/srv/secrets/feishu-dify-gateway
state_dir=/srv/data/feishu-dify-gateway
gateway_uid=10001
gateway_gid=10001

if [[ $EUID -ne 0 ]]; then
  printf 'Run this script with sudo so ownership can be set for the container user.\n' >&2
  exit 1
fi

install -d -o "$gateway_uid" -g "$gateway_gid" -m 700 "$target_dir" "$state_dir"

write_value() {
  local name=$1
  local prompt=$2
  local value
  IFS= read -r -s -p "$prompt: " value
  printf '\n'
  if [[ -z "$value" ]]; then
    printf 'Value cannot be empty: %s\n' "$name" >&2
    return 1
  fi
  umask 077
  printf '%s' "$value" > "$target_dir/$name"
  unset value
}

write_value feishu_app_id 'Feishu App ID'
write_value feishu_app_secret 'Feishu App Secret'
write_value dify_api_key 'Dedicated Dify Chatflow API Key'
write_value user_hmac_key 'User pseudonym HMAC key'
write_value notification_hmac_key 'Shared notification HMAC key'

chmod 600 "$target_dir"/*
chown "$gateway_uid:$gateway_gid" "$target_dir"/*
printf 'Gateway base secret files installed. Run deploy/wsl/capture-open-id.sh next.\n'
