# OpenCode Go 预部署交接

更新日期：2026-08-22（Asia/Shanghai）
状态：固定协议适配、双层虚拟额度账本与独立 Review 入口已实现；真实 A/B/C/D 只对冻结
commit/diff 执行。

## 目标与工具面

OpenCode Go 是执行 provider，不是新的通用聊天工具。它复用现有受限业务契约：

- coding/toolsmith：使用 `ask-ai-mcp-core.exe`，不加载 H3 或多模态工具；
- 258V 多模态专机：使用 `ask-ai-mcp-perception.exe`，只声明
  `source_backend_status`、`source_extract`、`source_job_status` 三个工具；
- 兼容 `subagent`/`full` 入口仍存在，但不建议给专用会话加载无关工具。

Perception 入口保持本机 Qwen 已验证的白名单、暂存副本、PDF/PPTX 预处理、来源哈希、
坐标校验及 `canonical_evidence_v1` 输出，仅替换模型传输层。当前公开契约仍只处理静态图片、
PDF、PPTX 和 UTF-8 文本；音频、视频需要独立预处理与时间码契约。

## 固定路由

代码不接受任意 base URL 或任意模型名：

| 阶段 | 模型 | 协议 | 固定端点 |
|---|---|---|---|
| 初次构建、再生成、静态修复 | DSV4 Flash | Chat Completions | `/zen/go/v1/chat/completions` |
| 用户显式选择 Pro 的对应阶段 | DSV4 Pro | Chat Completions | `/zen/go/v1/chat/completions` |
| 语义测试修复 | GPT-5.6 Luna | Responses | `/zen/go/v1/responses` |
| 多模态来源结构化 | Qwen3.8 Max | Anthropic Messages | `/zen/go/v1/messages` |

Responses 请求使用严格 JSON Schema；Messages 适配器只接受 MCP 已生成的 Base64 data image，
不会读取 URL、`file://` 或任意路径。

## 凭据与配置

密钥只通过交互提示进入 Windows Credential Manager，不接受命令行密钥参数：

```powershell
ask-ai-mcp-credentials set --provider opencode-go --account primary
ask-ai-mcp-credentials status --provider opencode-go --account primary
```

账户别名可使用受限小写标识符。每份合法订阅必须同时配置独立的本地订阅标识；运行时不会
自动轮换或跨账户规避额度：

```text
ASK_AI_MCP_OPENCODE_ACCOUNT=primary
ASK_AI_MCP_OPENCODE_SUBSCRIPTION_ID=go-primary
```

coding 专用入口：

```text
ASK_AI_MCP_TOOLSMITH_PROVIDER=opencode
```

258V perception 专用入口：

```text
ASK_AI_MCP_SOURCE_PROVIDER=opencode
ASK_AI_MCP_SOURCE_INPUT_ROOTS=<分号分隔的窄目录>
```

## 计量与限额

本地 SQLite 记录 provider、实际模型、协议、账户、输入/输出、缓存读写 token 和估算虚拟
美元，不保存密钥、完整 prompt、来源正文或普通模型输出。价格表固定为
`opencode-go-2026-08-21`，避免官网价格变化后悄悄重算历史。

每个 `(account_alias, subscription_id)` 分别显示并保守检查共享滚动 5 小时 12 美元、
7 天 30 美元和 30 天 60 美元；同时检查 GLM/Kimi/Pro 各 15 美元、Flash 30 美元的模型
月度额度。模型有效余额取共享月余额与模型余额较小值，跨模型共享消费不会重复扣除。
DeepSeek 按官方 UTC 两段 peak 窗口计价；缓存写等缺失价格明确为 unsupported，不默认为 0。
滚动窗口是本地保护性近似，不冒充 OpenCode 服务端的精确订阅
结算周期；服务端 429 始终是最终权威。由于密钥约定只供本 MCP 使用，本地账本通常能覆盖
全部调用，但真实联调仍必须校对响应 usage 字段和控制台账单。

## 上线前验收

1. 在独立测试账户写入 primary 密钥，不把密钥写入配置文件或日志。
2. 调用 `/zen/go/v1/models`，确认目标模型 ID 与当前代码一致。
3. 分别执行 Flash 构建、Luna 语义修复和 Qwen 单图来源提取的 opt-in 测试。
4. 对照 OpenCode 控制台核对 token、缓存和虚拟美元；字段语义不同则先修正账本。
5. 验证 258V 客户端只启用 perception MCP；coding 客户端只启用 core MCP。
6. 真实 API 集成测试必须保持 opt-in，不进入普通 `pytest`。
