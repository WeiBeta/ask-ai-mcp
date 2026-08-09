# 脱敏与排除清单

迁移包使用文件白名单构建，不从用户目录或运行状态目录收集内容。

以下内容必须始终排除：

- Windows Credential Manager 及 DeepSeek API 密钥；
- `.env`、令牌、Cookie、证书、私钥和浏览器状态；
- `%LOCALAPPDATA%\AskAIMCP` 下的 `usage.db`、`jobs`、`registry`、`runs`；
- `%USERPROFILE%\Documents\AskAI-Exchange` 及其中的业务和媒体文件；
- Codex、Claude 和其他应用的完整个人配置；
- Git 元数据、Git 凭据、开发虚拟环境、缓存和日志；
- ComfyUI 程序、模型、输入、输出和日志。

包内只允许存在构建出的 MCP wheel、锁定依赖、安装和盘点脚本、GPT 交接说明、配置
模板、profile 信息及 SHA-256 manifest。目标机密钥必须重新录入。
