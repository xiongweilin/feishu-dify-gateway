# 凭证录入

凭证不得通过聊天、命令参数、Git、Docker 环境变量或日志传递。实施者只负责提供脚本；用户在交互终端自行运行并粘贴值。

1. 在飞书后台创建仅本人可见的自建应用，启用机器人及长连接事件订阅，获取 App ID、App Secret 和本人的 `open_id`。
2. 在 Dify 新建专用 Chatflow，发布后创建独立 API Key。
3. 在 Bitwarden 生成两个随机 32-byte HMAC key：用户映射 key、通知签名 key。
4. 在 WSL 以 `ratio` 用户运行 `deploy/wsl/install-gateway-secrets.sh`。
5. 在 Windows 运行 `deploy/windows/set-feishu-gateway-credential.ps1`，录入同一个通知签名 key。
6. 在云服务器交互运行 `deploy/cloud/install-cloud-secrets.sh`，录入同一个通知签名 key，以及专用 Gmail App Password。
7. 只核对文件存在、权限和服务健康；不要打印任何值。
