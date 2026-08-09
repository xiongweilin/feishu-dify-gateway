#!/usr/bin/env bash
set -euo pipefail

relay_dir=/etc/feishu-relay
watchdog_dir=/etc/feishu-gateway-watchdog

if [[ $EUID -ne 0 ]]; then
  printf 'Run this script with sudo.\n' >&2
  exit 1
fi

install -d -m 700 "$relay_dir" "$watchdog_dir"

write_value() {
  local path=$1
  local prompt=$2
  local value
  IFS= read -r -s -p "$prompt: " value
  printf '\n'
  if [[ -z "$value" ]]; then
    printf 'Value cannot be empty: %s\n' "$path" >&2
    return 1
  fi
  umask 077
  printf '%s' "$value" > "$path"
  unset value
}

write_value "$relay_dir/notification_hmac_key" 'Shared notification HMAC key'
write_value "$watchdog_dir/smtp_username" 'QQ 邮箱 SMTP 用户名'
write_value "$watchdog_dir/smtp_app_password" 'QQ 邮箱 SMTP 授权码'
write_value "$watchdog_dir/smtp_recipient" '备用接收邮箱'

chmod 600 "$relay_dir"/* "$watchdog_dir"/*
printf 'Cloud secret files installed. No value was displayed.\n'
