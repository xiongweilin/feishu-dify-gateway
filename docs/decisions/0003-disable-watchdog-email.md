# ADR-003：保留网关 watchdog 探测但禁用带外邮件

## 状态

Accepted

## 日期

2026-08-28

## 背景

云端 `feishu-gateway-watchdog.timer` 负责探测飞书网关就绪状态。其历史实现同时通过 SMTP 发送故障与恢复邮件。当前运行策略要求保留就绪探测和状态证据，但暂停邮箱告警，避免该通知渠道产生噪声；飞书通知链路与 Alertmanager → gateway 路径不在本决定范围内。

## 决定

- watchdog 继续由 systemd timer 每分钟运行并执行 readiness check。
- `WATCHDOG_EMAIL_ENABLED` 成为显式开关；云端 systemd unit 固定设置为 `false`。
- 禁用时不读取或发送 SMTP 凭据，状态文件仍记录就绪失败窗口；重新启用后可立即对持续失败发出通知。
- `/etc/feishu-gateway-watchdog` 中的 SMTP 文件保留，作为可逆恢复输入，不进入 Git、环境日志或镜像。

## 备选方案

- 停止或禁用 watchdog timer：拒绝，会同时失去云端网关就绪探测和状态证据。
- 删除 SMTP 凭据：拒绝，破坏可逆恢复并扩大变更范围。

## 后果

- 云端不再发送 SMTP 故障/恢复邮件。
- systemd timer、就绪检查、状态文件和飞书通知链路保持运行。
- 若未来需要恢复邮件，只需将开关改为 `true` 并重新加载 systemd；无需重新录入凭据。
