# OpenCode Go 预部署交接

更新日期：2026-08-27（Asia/Shanghai）
状态：固定协议适配、UID 账户、共享虚拟额度账本及独立 Coding/Review 入口已实现；五个固定
模型的真实付费联调已于 2026-08-27 完成。

## 目标与工具面

OpenCode Go 是执行 provider，不是新的通用聊天工具。它复用现有受限业务契约：

- 常驻 toolsmith：使用 `ask-ai-mcp-core.exe`，不加载 H3 或多模态工具；
- 仓库 coding：按需启用 `ask-ai-mcp-coding.exe`，只声明三个候选制造工具；
- 冻结 diff 深审：按需启用 `ask-ai-mcp-review.exe`，只声明四个只读审查/封存工具；
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
| Coding 默认候选 | DSV4 Flash 或 GLM-5.3-Flash | Chat Completions，`max` | `/zen/go/v1/chat/completions` |
| Coding 高级候选 | DSV4 Pro、GLM-5.3 或 Kimi K3 | Chat Completions，`max` | `/zen/go/v1/chat/completions` |
| Review 深审 | DSV4 Pro、GLM-5.3 或 Kimi K3 | Chat Completions，`max` | `/zen/go/v1/chat/completions` |
| Core 工具制造 | 固定服务端策略 | 固定协议 | 固定 Go 端点 |
| 多模态来源结构化 | Qwen3.8 Max | Anthropic Messages | `/zen/go/v1/messages` |

Core 初次生成/语义修复、Coding 五路与 Review 三路统一使用共享 Go 生成策略；文本 thinking
工况的客户端总生成上限为 131,072 token。Core 静态哈希绑定小修保持 thinking off/4,096，
Perception 保持 reasoning none 和按提取 profile 控制的结构化证据上限，H3 不使用 completion
预算。所有实际 effort/cap 必须写入状态、manifest 或 usage 审计，不能再散落为入口私有策略。

远程推理的网络等待不再使用零散的 10/15/30 分钟默认值。0.12.2 统一为：异步 Coding、
Review、远程 Perception 读取 2 小时；同步 Core toolsmith 读取 1 小时；连接/连接池 30 秒、
写入 10 分钟。所有上限仍为有限值，且不触发自动重试。非流式 Go 请求只能确认本地 worker
仍在等待，无法确认上游 request 状态或实时 token 输出；`/models` 仅用于模型可用性检查。

Responses 请求使用严格 JSON Schema；Messages 适配器只接受 MCP 已生成的 Base64 data image，
不会读取 URL、`file://` 或任意路径。

## 凭据与配置

密钥只通过交互提示进入 Windows Credential Manager，不接受命令行密钥参数：

```powershell
ask-ai-mcp-credentials set --provider opencode-go --account-uid <API可见UID>
ask-ai-mcp-credentials status --provider opencode-go --account-uid <API可见UID>
```

UID 和显示别名支持邮箱形式。若 Codex 内嵌终端无法完成 `getpass` 粘贴，可在用户自行打开的
PowerShell 7 中使用 `set --visible-input`；该模式会明确警告输入可见，仍不把密钥写进命令行、
配置或日志。这是终端交互兼容路径，不是 Credential Manager 权限修复。

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
`opencode-go-2026-08-28`，避免官网价格变化后悄悄重算历史。

每个账户 UID 分别显示并保守检查共享滚动 5 小时 12 美元、
7 天 30 美元和 30 天 60 美元；同时检查 GLM/Kimi/Pro/Grok 各 15 美元、Flash 30 美元的模型
月度额度。模型有效余额取共享月余额与模型余额较小值，跨模型共享消费不会重复扣除。
DeepSeek 仅在工作日按官方 UTC 两段 peak 窗口计价；周末 off-peak。缓存写等缺失价格明确为
unsupported，不默认为 0。
Grok 4.6 使用 Responses 协议与官方 `xhigh` 推理档，并按 200K 输入 token 阈值切换两档价格；
Coding/Review 将估算输入限制为 199,999 token，达到高价档前 fail closed。作者已在账户层明确
接受 OpenCode Go 对同类模型的数据留存/改进条款，因此不再增加逐仓库留存授权开关。
0.13.4 起 Grok 使用单次 SSE 响应，只接受完整终态事件；中途断流不会触发重试或换模型。
滚动窗口是本地保护性近似，不冒充 OpenCode 服务端的精确订阅
结算周期；服务端 429 始终是最终权威。由于密钥约定只供本 MCP 使用，本地账本通常能覆盖
全部调用，但真实联调仍必须校对响应 usage 字段和控制台账单。

## 上线前验收

1. 开通付费账户后，用隐藏交互按 UID 写入密钥，不把密钥写入配置文件或日志。
2. 调用 `/zen/go/v1/models`，确认目标模型 ID 与当前代码一致。
3. 对两条默认 Coding 路由与四个 Review 模型执行相同合成输入的 opt-in 测试；四条高级
   Coding 路由仅在明确授权时分别做单次测试，验证 `max` 支持且不触发自动 fallback。
4. 对照 OpenCode 控制台核对 token、缓存和虚拟美元；字段语义不同则先修正账本。
5. 验证常态只启用 Core；Coding、Review、Perception 和 H3 均独立按需开关。
6. 真实 API 集成测试必须保持 opt-in，不进入普通 `pytest`。

## 2026-08-27 联调结论

- GLM-5.3-Flash 与 DeepSeek V4 Flash 均成功生成同一修复候选；候选 SHA-256 完全一致。
- Kimi K3、GLM-5.3 与 DeepSeek V4 Pro 均返回可解析 Review；常见 category 同义词会在本地
  映射到固定枚举，finding 仍须经过改动 hunk 校验。
- DeepSeek 两模型首次返回区域门禁 403；在 Go 工作区显式同意中国托管后成功。新账户执行
  DeepSeek smoke 前必须完成同一 opt-in，不能把该 403 误判为密钥或额度故障。
- 成功调用但本地结构校验失败时仍记录 token、估算成本和失败状态；审计只保存响应哈希、
  字节数、finish reason 与 usage，不保存完整常规模型输出。
- 首轮旧代码漏记了一次成功响应及三次极小诊断请求；这批联调的本地账本不能作为控制台的
  完整对账样本。修复后的后续账本才适合长期比较。
