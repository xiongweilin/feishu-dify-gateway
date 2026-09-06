# ADR-004：通知送达账本与无外发 Synthetic 入口

## 状态

Accepted（2026-09-06）

## 背景

通知链路原本只有 `processed_events` 去重和 relay 的成功/失败计数，无法区分“请求被下一跳接收”和“下游通知供应商确认成功”。relay 失败时也会无限重试，无法形成永久失败终态。首次 synthetic 通知测试还必须避免未经人工授权产生外部发送。

## 决定

- 网关和 cloud relay 各自维护只含元数据的送达账本，使用同一 `event_id` 关联，但不持久化通知正文、凭据或第三方响应正文。
- `transport_accepted` 表示当前传输边界的接收确认；`delivery_confirmed` 表示 Feishu provider 返回成功。该状态不等价于最终用户阅读或业务结果确认。
- 网关状态路径为 `delivering` → `retrying` 或 `permanent_failed`，成功后进入 `delivery_confirmed`；重试沿用已有 event id 和 chunk 幂等键。
- relay 状态路径为 `queued` → `delivering` → `retrying` / `permanent_failed` / `transport_accepted`。仅对网络错误和有限 HTTP 状态重试；超过 `RELAY_MAX_ATTEMPTS` 或收到非重试 HTTP 状态后进入永久失败。永久失败队列项保留为审计材料，但不再进入 due 队列。
- `POST /v1/notifications/synthetic/prepare` 仅生成 `synthetic-*` 事件和 `prepared` 账本记录，返回 `externalSendStarted=false` 与 `requiresManualConfirmation=true`。当前没有自动执行 synthetic 的接口，也不会触发 sender、relay 或外部网络。
- `GET /v1/notifications/synthetic/probe` 只检查本地 synthetic 能力，返回 `ready`，不创建账本记录、不触发 sender、relay 或外部网络。
- synthetic prepare 与账本查询只在 gateway internal listener 提供，public listener 统一返回 404。

## 验证与边界

- Prometheus 指标只使用 bounded `source`、`state`、`result` 标签，不使用 event id 或消息内容。
- relay 日志只输出规范化状态、attempt 和错误码，不输出请求正文或响应正文。
- 该变更不修改云端权限、凭据、SSH/sudo/ACL、防火墙、SELinux 或运行中的 Prometheus/Alertmanager；首次外部发送不由代码路径自动触发。
- 本地 compose 额外将 internal listener 绑定到 `127.0.0.1:18084`，仅用于本机 dry-run/probe/ledger 检查；cloud relay 继续使用 `127.0.0.1:18082` 对应的 public listener。
