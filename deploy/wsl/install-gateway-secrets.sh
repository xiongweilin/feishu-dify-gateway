#!/usr/bin/env bash
set -euo pipefail

target_dir=/srv/secrets/feishu-dify-gateway
install -d -m 700 "$target_dir"

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
write_value feishu_allowed_open_id 'Allowed Feishu open_id'
write_value feishu_alert_recipient_open_id 'Alert recipient Feishu open_id'
write_value dify_api_key 'Dedicated Dify Chatflow API Key'
write_value user_hmac_key 'User pseudonym HMAC key'
write_value notification_hmac_key 'Shared notification HMAC key'

chmod 600 "$target_dir"/*
printf 'Gateway secret files installed. No value was displayed.\n'
