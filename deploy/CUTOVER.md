# 切换运行手册

本文只负责新链路启用与验收。旧链路的不可逆清除不在本文自动执行，必须在全部验收通过后另行取得逐项批准。

## 1. 准备

1. 飞书应用仅本人可见，启用机器人和长连接事件订阅，只授予单聊消息读取、发送所需权限。
2. 发布专用 Dify Chatflow 并创建独立 API Key。
3. 在 WSL 交互执行 `sudo deploy/wsl/install-gateway-secrets.sh`；只检查 secret 文件存在、权限 `600`、UID/GID `10001`，不输出值。
4. 在云端先以仓库所有者执行 `deploy/cloud/install-runtime.sh`，再交互执行 `sudo deploy/cloud/install-cloud-secrets.sh`；Windows 交互执行 `deploy/windows/set-feishu-gateway-credential.ps1`。
5. 保存当前容器、Alertmanager、Prometheus、Tailscale Serve、云端 relay 和 Windows 任务的最小基线。

## 2. 启动候选，不切流

1. 在 WSL 运行 `docker compose build` 和 `docker compose up -d`。
2. 验证容器内 `8082`、受限监听 `8083`、宿主机 `127.0.0.1:18082`、`/healthz`、`/readyz` 和 `/metrics`。
3. 证明宿主机入口访问 `/v1/alerts/alertmanager` 返回 404，而 `shared-net` 内同一路径可达。
4. 执行 `sudo deploy/wsl/enable-gateway-serve.sh`，确认只新增 TCP 8082 映射。
5. 从云端使用签名测试通知验证确认式投递；此时仍不替换正式 relay。

## 3. 功能与故障验收

1. 私聊验证普通文本、连续会话、`/help`、`/new`、`/status`、`/alerts`。
2. 验证 firing 和 resolved 告警各一次；重复 fingerprint 只投递一次。
3. 验证 GitHub、Sonar 和 Windows 维护通知；队列文件只在 gateway 返回 2xx 后删除。
4. 注入重复事件、飞书 429/5xx、Dify 超时、gateway 重启与 WSL 离线，验证重试、幂等、队列保留和恢复补发。
5. 让就绪检查连续失败三次，并模拟持续五分钟；验证 Gmail 首次、六小时限频和恢复通知。
6. 检查日志和指标，不得出现正文、原始用户标识、URL、凭证或上游响应体。

## 4. 原子切流

1. 将 Prometheus scrape fragment 合并到活动配置，运行 `promtool check config` 后 reload。
2. 将 Alertmanager receiver fragment 合并到活动配置，运行 `amtool check-config`；只有测试告警成功后 reload 正式配置。
3. 云端保存旧 relay 单元和队列基线，安装新单元；确认新队列为空、服务健康后再替换正式进程。
4. 替换 Windows 周维护通知 helper；验证一次成功后再停用旧轮询任务。
5. 在观察窗内验证三类通知源、队列深度、最后成功时间和错误率；任一失败立即停止后续步骤并恢复对应基线。

## 5. 最终门禁

只有上述验收全部通过，才能请求不可逆清除批准。批准必须逐项点名数据库卷、文件目录、历史备份、Git 全历史强推、DNS 记录和旧证书 lineage。批准前不得删除任何旧数据、入口、证书、日志、备份或恢复基线。
