# OpenCode Go 预部署交接

更新日期：2026-08-27（Asia/Shanghai）
状态：固定协议适配、UID 账户、共享虚拟额度账本及独立 Coding/Review 入口已实现；真实付费
联调尚未执行。

## 目标与工具面

OpenCode Go 是执行 provider，不是新的通用聊天工具。它复用现有受限业务契约：

- 常驻 toolsmith：使用 `ask-ai-mcp-core.exe`，不加载 H3 或多模态工具；
- 仓库 coding：按需启用 `ask-ai-mcp-coding.exe`，只声明三个候选制造工具；
- 冻结 diff 深审：按需启用 `ask-ai-mcp-review.exe`，只声明三个只读审查工具；
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
| Coding 候选 | DSV4 Flash 或 GLM-5.3-Flash | Chat Completions，`max` | `/zen/go/v1/chat/completions` |
| Review 深审 | DSV4 Pro、GLM-5.3 或 Kimi K3 | Chat Completions，`max` | `/zen/go/v1/chat/completions` |
| Core 工具制造 | 固定服务端策略 | 固定协议 | 固定 Go 端点 |
| 多模态来源结构化 | Qwen3.8 Max | Anthropic Messages | `/zen/go/v1/messages` |

Responses 请求使用严格 JSON Schema；Messages 适配器只接受 MCP 已生成的 Base64 data image，
不会读取 URL、`file://` 或任意路径。

## 凭据与配置

密钥只通过交互提示进入 Windows Credential Manager，不接受命令行密钥参数：

```powershell
ask-ai-mcp-credentials set --provider opencode-go --account-uid <API可见UID>
ask-ai-mcp-credentials status --provider opencode-go --account-uid <API可见UID>
```

每个合法付费 Go 账户只登记一个 API key。UID 是非秘密的 API 可见稳定主键，账户名只是
显示别名；运行时不会自动轮换或跨账户规避额度：

```text
ASK_AI_MCP_OPENCODE_ACCOUNT_UID=<API可见UID>
ASK_AI_MCP_OPENCODE_ACCOUNT_ALIAS=<Go账户名>
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
`opencode-go-2026-08-27`，避免官网价格变化后悄悄重算历史。

每个账户 UID 分别显示并保守检查共享滚动 5 小时 12 美元、
7 天 30 美元和 30 天 60 美元；同时检查 GLM/Kimi/Pro 各 15 美元、Flash 30 美元的模型
月度额度。模型有效余额取共享月余额与模型余额较小值，跨模型共享消费不会重复扣除。
DeepSeek 仅在工作日按官方 UTC 两段 peak 窗口计价；周末 off-peak。缓存写等缺失价格明确为
unsupported，不默认为 0。
滚动窗口是本地保护性近似，不冒充 OpenCode 服务端的精确订阅
结算周期；服务端 429 始终是最终权威。由于密钥约定只供本 MCP 使用，本地账本通常能覆盖
全部调用，但真实联调仍必须校对响应 usage 字段和控制台账单。

## 上线前验收

1. 开通付费账户后，用隐藏交互按 UID 写入密钥，不把密钥写入配置文件或日志。
2. 调用 `/zen/go/v1/models`，确认目标模型 ID 与当前代码一致。
3. 对两个 Coding 模型与三个 Review 模型执行相同合成输入的 opt-in 测试，验证 `max` 支持。
4. 对照 OpenCode 控制台核对 token、缓存和虚拟美元；字段语义不同则先修正账本。
5. 验证常态只启用 Core；Coding、Review、Perception 和 H3 均独立按需开关。
6. 真实 API 集成测试必须保持 opt-in，不进入普通 `pytest`。
