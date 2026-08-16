# Ask AI MCP 目标机交接

本迁移包只安装 Ask AI MCP Server，不包含 ComfyUI、模型、视频、业务文件、桌面个人
配置、本机审计状态或任何 API 密钥。

## GPT 的职责

1. 先运行 `scripts\inspect-machine.ps1`，只读核对 Windows、PowerShell、Python、uv、
   Docker/WSL 和现有安装目录。
2. 阅读 `profile.json`，不得擅自在 Core、Subagent、H3、Full 之间改换 profile。
3. 在执行系统变更前，列出准备新增或启用的组件并取得用户同意。
4. Windows 主机只做必要的新增或启用；不得关闭、卸载或降级现有功能与运行时。
5. 使用系统 Python 3.13 和系统 uv，通过包内锁定依赖建立独立 `.venv`。
6. Docker/WSL 仅在用户需要制造候选工具或运行已验证工具时安装或启用。
7. 安装后生成适配实际用户名和路径的 Codex/Claude MCP 配置。
8. API 密钥只能由用户在目标机的隐藏提示中录入 Windows Credential Manager。

## 固定安全边界

- 不得在脚本、聊天、日志、环境变量、JSON、TOML 或普通文本中索取或保存 API 密钥。
- 不得恢复来源不明的 `%LOCALAPPDATA%\AskAIMCP`。
- 不得把整块磁盘、用户目录或业务资料目录设为允许输入根。
- 原始业务文件保持只读；只允许处理明确暂存的副本。
- MCP stdout 只用于协议，诊断信息不得写入 stdout。

## 推荐流程

```powershell
pwsh -NoProfile -File .\scripts\inspect-machine.ps1
pwsh -NoProfile -File .\scripts\install-mcp.ps1 -Profile core
```

其他包将第二条命令中的 profile 改为 `subagent`、`h3` 或 `full`。安装脚本不会录入凭据，也不会修改
Codex 或 Claude 的个人配置；GPT 应根据 `config-templates` 中的片段做合并，不能覆盖
用户已有配置。

安装完成后，由用户亲自在隐藏提示中运行：

```powershell
<安装目录>\.venv\Scripts\ask-ai-mcp-credentials.exe set
```

最后完全重启相应桌面客户端，并确认 Core 12、Subagent 15、H3 4、Full 19 项工具。
