# Feishu Gateway

个人飞书应用机器人的私有网关。它承担三类职责：

- 通过飞书长连接接收本人单聊消息；非命令消息直接派发给控制平面的 Codex Agent 执行；
- 接收 Alertmanager 和基础设施通知，投递到本人飞书私聊；
- 输出健康、就绪和 Prometheus 指标，不记录消息正文、凭证或原始用户标识。

## 边界

- 仅允许一个配置的飞书 `open_id`。
- 支持文本及 `/help`、`/status`、`/alerts` 只读命令；任意非命令消息等价于 `/task <描述>`，派发任务给 Codex。
- 控制平面命令：`/cp status`、`/cp approve <id>`、`/cp reject <id>`、`/cp rollback <id>`、`/cp pause`、`/cp resume`、`/cp promote <candidate_id>`；由控制平面确认后执行，非命令文本统一派发任务给 Codex。
- 控制平面策略命令：`/cp policy <fingerprint> auto|manual|ignore`、`/cp run <fingerprint>`、`/cp ignore <fingerprint>`、`/cp evidence`、`/cp dismiss <candidate_id>`。
- `/v1/notifications` 必须使用时间戳、事件 ID 和 HMAC-SHA256 签名。
- `/v1/alerts/alertmanager` 只通过 Docker `shared-net` 使用。
- 控制平面审批回调使用 `X-Control-Plane-Key` 共享密钥头；`CONTROL_PLANE_BASE_URL` 指向 Windows 宿主 `http://host.docker.internal:18083`。
- 飞书响应一律按不可信外部输入校验。
- 幂等库只保存事件 ID、状态和时间，不保存消息正文。

## 开发

```powershell
uv sync
uv run pytest
uv run ruff check .
uv run mypy
```

## 运行配置

Compose 从 `/srv/secrets/feishu-dify-gateway` 挂载以下文件：

| 文件 | 用途 |
|---|---|
| `feishu_app_id` | 飞书应用 App ID |
| `feishu_app_secret` | 飞书应用 App Secret |
| `feishu_user_open_id` | 唯一允许交互且接收告警的用户；由一次性捕获工具直接写入 |
| `user_hmac_key` | 对飞书用户标识做不可逆映射 |
| `notification_hmac_key` | 云端 relay 与 Windows helper 的请求签名 |
| `control_plane_key` | 控制平面审批与状态接口的共享密钥 |

所有文件必须为 `600`，目录必须为 `700`，并归属容器内专用 UID/GID `10001`；状态目录使用同一归属。凭证不得进入 Git、Docker 环境变量、日志或文档。实际凭证由用户在目标主机通过 `sudo deploy/wsl/install-gateway-secrets.sh` 交互录入；不要通过聊天或命令输出传递。

## 内部接口

- `POST /v1/alerts/alertmanager`
- `POST /v1/notifications`
- `GET /healthz`
- `GET /readyz`
- `GET /metrics`

容器内 `8082` 是只在 `shared-net` 使用的内部接口；宿主机 `127.0.0.1:18082` 只映射到受限的 `8083` 监听，后者会隐藏 Alertmanager 路由。

错误统一为：

```json
{"error":{"code":"ERROR_CODE","message":"Safe explanation"}}
```

详细决策见 [ADR-001](docs/decisions/0001-use-feishu-long-connection-and-dify.md)（长连接）与 [ADR-002](docs/decisions/0002-dispatch-messages-to-control-plane-codex.md)（消息派发 Codex、移除 Dify Chatflow）。
