#!/usr/bin/env bash
set -euo pipefail

secret_dir=/srv/secrets/feishu-dify-gateway
identity_file=$secret_dir/feishu_user_open_id
image=feishu-dify-gateway:0.1.0
container=feishu-open-id-capture

for name in feishu_app_id feishu_app_secret; do
  if [[ ! -f $secret_dir/$name || -L $secret_dir/$name ]]; then
    printf 'Required secret file is missing or unsafe: %s\n' "$name" >&2
    exit 1
  fi
done
if [[ -e $identity_file ]]; then
  printf 'Identity secret already exists; refusing to overwrite it.\n' >&2
  exit 1
fi
if docker container inspect "$container" >/dev/null 2>&1; then
  printf 'Capture container already exists.\n' >&2
  exit 1
fi

cleanup() {
  docker rm -f "$container" >/dev/null 2>&1 || true
}
trap cleanup EXIT
timeout --signal=TERM 6m docker run --rm --name "$container" \
  --user 10001:10001 \
  --read-only \
  --cap-drop ALL \
  --security-opt no-new-privileges:true \
  --tmpfs /tmp:size=16m,mode=1777 \
  --env GATEWAY_SECRETS_DIR=/run/secrets \
  --volume "$secret_dir:/run/secrets:rw" \
  "$image" \
  /app/.venv/bin/python -m feishu_dify_gateway.capture_open_id
trap - EXIT

if [[ ! -s $identity_file || $(stat -c '%a' "$identity_file") != 600 ]]; then
  printf 'Captured identity file failed the existence or mode check.\n' >&2
  exit 1
fi
printf 'Feishu identity file is present with mode 600; no value was displayed.\n'
