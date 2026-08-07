# ADR-002：飞书消息统一派发给控制平面 Codex，移除 Dify Chatflow

## 状态

Accepted

## 日期

2026-08-07

## 背景

ADR-001 决定用专用 Dify Chatflow 处理飞书文本会话。实际使用中，聊天与运维任务都由同一个模型体系承担，维护两套对话入口（Dify Chatflow 与控制平面 Codex）既增加复杂度，也无法把“任意任务派发”纳入既有预算、证据与审批语义。控制平面已具备 Codex Agent 执行、预算门禁、分阶段通知与审计记录能力。

## 决定

- 飞书非命令消息与 `/task <描述>` 统一调用控制平面 `POST /v1/tasks`，由 Codex Agent 执行。
- 删除 Dify Chatflow 集成：`DifyClient`、`dify_api_key` 密钥、`/new` 会话与对话状态全部移除。
- 告警策略命令（`/cp policy|run|ignore`）、证据查询（`/cp evidence`）沿用既有共享密钥通道。
- readiness 只依赖飞书长连接；Prometheus 状态仅用于 `/status` 展示，不再阻塞 readyz。
- 任务执行过程（接收 → Agent 启动 → 心跳 → 完成/失败）通过通知通道推送飞书。

## 备选方案

- 保留 Dify Chatflow 作为聊天入口、Codex 只处理运维：拒绝，双入口职责重叠，且无法统一预算与审计。
- 在网关内直接调用模型 API：拒绝，绕过控制平面的预算、门禁、候选与证据语义。

## 后果

- 任意飞书消息都会消耗控制平面每日 Agent 预算；空消息与命令除外，预算耗尽时任务会被拒绝并提示。
- 非命令消息内容会进入 Codex 会话与 `data/agent-sessions` 审计文件；幂等与日志仍不保存消息正文之外的原始标识。
- Dify 本体与 Chatflow 继续运行，但不再由飞书机器人桥接；`feishu_secrets` 卷中的旧 `dify_api_key` 文件不再被读取。
