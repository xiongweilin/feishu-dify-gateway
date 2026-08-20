# Feishu Gateway

[![CI](https://github.com/ratiolin/feishu-dify-gateway/actions/workflows/ci.yml/badge.svg)](https://github.com/ratiolin/feishu-dify-gateway/actions/workflows/ci.yml) [![Quality Gate Status](https://sonarcloud.io/api/project_badges/measure?project=metratio_feishu-dify-gateway&metric=alert_status)](https://sonarcloud.io/summary/new_code?id=metratio_feishu-dify-gateway) [![Coverage](https://sonarcloud.io/api/project_badges/measure?project=metratio_feishu-dify-gateway&metric=coverage)](https://sonarcloud.io/summary/new_code?id=metratio_feishu-dify-gateway) [![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE) [![Python 3.12](https://img.shields.io/badge/Python-3.12-blue.svg)](pyproject.toml)
The private gateway for the personal Feishu app bot. It has three responsibilities:

- Receives my single-chat messages through the Feishu long connection; non-command messages are dispatched directly to the control plane's dsh Agent for execution;
- Receives Alertmanager and infrastructure notifications and delivers them to my Feishu private chat;
- Exposes health, readiness, and Prometheus metrics; records no message bodies, credentials, or raw user identifiers.

## Boundaries

- Allows only one configured Feishu `open_id`.
- Supports text and the read-only commands `/help`, `/status`, `/alerts`; any non-command message is equivalent to `/task <description>` and dispatches a task to the control plane's dsh Agent.
- Control-plane commands: `/cp status`, `/cp approve <id>`, `/cp reject <id>`, `/cp rollback <id>`, `/cp pause`, `/cp resume`, `/cp promote <candidate_id>`; executed after the control plane confirms; non-command text uniformly dispatches tasks to the dsh Agent.
- Control-plane policy commands: `/cp policy <fingerprint> auto|manual|ignore`, `/cp run <fingerprint>`, `/cp ignore <fingerprint>`, `/cp evidence`, `/cp dismiss <candidate_id>`.
- `/v1/notifications` must use a timestamp, event id, and HMAC-SHA256 signature.
- `/v1/alerts/alertmanager` is used only through the Docker `shared-net`.
- Control-plane approval callbacks use the `X-Control-Plane-Key` shared-key header; `CONTROL_PLANE_BASE_URL` points to the Windows host `http://host.docker.internal:18083`.
- Feishu responses are always validated as untrusted external input.
- The idempotency store keeps only event ids, status, and time — never message bodies.

## Development

```powershell
uv sync
uv run pytest
uv run ruff check .
uv run mypy
```

## Runtime configuration

Compose mounts the following files read-only from the external named volume `feishu_secrets` (Windows Docker Desktop, project directory `D:\infrastructure\compose\feishu-dify-gateway`):

| File | Purpose |
|---|---|
| `feishu_app_id` | Feishu app App ID |
| `feishu_app_secret` | Feishu app App Secret |
| `feishu_user_open_id` | The only user allowed to interact and receive alerts; written directly by the one-shot capture tool |
| `user_hmac_key` | irreversible mapping of the Feishu user identifier |
| `notification_hmac_key` | request signing for the cloud relay and the Windows helper |
| `control_plane_key` | shared key for the control plane's approval and status interfaces |

All files must be `600`, the directory `700`, owned by the dedicated in-container UID/GID `10001`; the state directory uses the same ownership. Credentials never enter Git, Docker environment variables, logs, or documentation. Real credentials are entered interactively by the user into Windows Credential Manager (`Agent:Metratio:FeishuFile:*` and existing Feishu entries) and the `feishu_secrets` volume (see `deploy/windows/` and `deploy/SECRETS.md`), never passed through chat or command output.

The cloud webhook relay and watchdog reach restricted endpoints through the Tailscale network (`http://metratio.tail1f4641.ts.net:8082`, mapped as Tailscale TCP 8082 → host `127.0.0.1:18082` → container restricted `8083` listener, which hides the Alertmanager route): the relay owns persistent queuing and retry delivery for external webhooks (`/v1/notifications`), and the watchdog sends out-of-band email via QQ SMTP when the gateway readiness check keeps failing (triggers at about 5 minutes, then reminds every 6 hours). Cloud deployment and secret entry: see `deploy/cloud/` (relay/watchdog systemd units and `install-cloud-secrets.sh`).

## Internal interfaces

- `POST /v1/alerts/alertmanager`
- `POST /v1/notifications`
- `GET /healthz`
- `GET /readyz`
- `GET /metrics`

Inside the container, `8082` is the internal interface used only on `shared-net`; the host `127.0.0.1:18082` maps only to the restricted `8083` listener, which hides the Alertmanager route.

Errors are unified as:

```json
{"error":{"code":"ERROR_CODE","message":"Safe explanation"}}
```

Detailed decisions: [ADR-001](docs/decisions/0001-use-feishu-long-connection-and-dify.md) (long connection) and [ADR-002](docs/decisions/0002-dispatch-messages-to-control-plane-codex.md) (dispatch messages to Codex, removed the Dify Chatflow).

