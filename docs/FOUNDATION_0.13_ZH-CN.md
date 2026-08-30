# Ask AI MCP 0.13 测试底座

## 定位

0.13.7 是当前物理机已经启用的灰度测试基线，0.12.3 继续作为可回滚版本。
0.13.7 已完成完整离线验收、冻结差异外部 Review、物理安装刷新和 GUI 重启后的
Core/Coding/Review 免费状态验收；当前阶段以真实业务样本继续验证路由、留存、超时与
失败诊断，不把测试大版本描述为稳定 1.0。Perception/H3 仍按需启用并在实际调用前做专项回归。

## 分层

```text
稳定 EXE / profile
        ↓
显式 capability：toolsmith / coding / review / perception / h3
        ↓
provider-neutral worker role 与受限路由
        ↓
entitlement gate ─ immutable usage ledger ─ user approval policy
        ↓
同一份兼容 SQLite 审计存储
```

- EXE 只决定加载哪些能力，不代表供应商、模型或账户。
- Worker role 表示职责；供应商和模型只作为可变路由元数据。
- entitlement 只判断已配置订阅的额度门禁，不替代人工授权。
- ledger 只追加不含 prompt/源码的用量记录并生成摘要；每次外部调用可用归因 UID 与独立加密
  线缆证据关联，但敏感正文不进入 SQLite。
- approval policy 独立管理私有源码外发、新增或充值订阅、扩大白名单和
  付费重试等需要明确授权的动作。
- 计费模块不是新的 MCP 进程，也不创建第二本账。

## 兼容边界

0.13 初始底座保持以下 0.12.3 合约：

- `ask-ai-mcp`、Core、Subagent、Perception、H3、Review、Coding、Full 的
  入口名称不变；
- 七个 profile 的工具名称、顺序和输入参数 schema 保持不变；
- `usage_store=` 等内部注入点保留兼容别名；
- SQLite 路径、表结构、历史 CNY 审计行和现有 USD 订阅账本不迁移；
- 冻结 commit、指定文件、仓库外候选、双哈希 patch/receipt、路径白名单、
  无自动付费重试/分片/换模型等门禁不放宽。

MCP 展示文字中的职责名称逐步改为 Ask AI / Worker；DeepSeek、GLM、Kimi、
Qwen 等名称只在实际模型目录、适配器、价格和供应商诊断中出现。

## 0.13.1 加密线缆诊断

Coding/Review 的生产 HTTP 路径常态保留供应商无关的加密线缆捕获。常规 audit 只记录
`wire_capture_uid`、HTTP 状态/版本、响应 header 名称、分块数、明文字节计数、完整/截断状态和
异常类型；请求/响应正文进入 job 下独立 `wire` 目录，以 Windows Credential Manager 中的独立
AES-256 密钥逐块认证加密。API key、Authorization、Cookie 和异常正文永不入仓。

默认每个 Coding/Review job 池最多保留 100 MiB 明文等价值；达到上限后按完整调用 UID 原子淘汰
最老密文包，不混合覆盖不同调用，也不删除常规归因 audit。可用
`ASK_AI_MCP_WIRE_CAPTURE_ENABLED=false` 显式关闭，或用
`ASK_AI_MCP_WIRE_CAPTURE_MAX_BYTES` 下调上限；不得超过 `104857600`。离线注入 transport 默认不
启用物理凭据和线缆捕获，专项 fixture 必须显式注入测试密钥。

若供应商控制台证明一次传输失败已正常计费，可由本机操作员运行
`ask-ai-mcp-review-reconcile`，以精确 job UID、带时区时间、input/output token 总数和实际美元成本
生成一次性 receipt。只有 `PROVIDER_REQUEST_FAILED` 且尚无 usage 的 Review job 可接受；同字段
重复执行幂等，不同字段冲突拒绝。该命令不是 MCP 工具，不支持任意账本行修改；dashboard 未提供
cache/reasoning 明细时明确记录为 `provider_dashboard_totals`，不得伪称明细为零。

## 验收顺序

1. 已在隔离 worktree 完成 Ruff、格式检查和完整离线 pytest。
2. 已对七个 profile 校验工具顺序和参数 schema 指纹。
3. 已校验同一 SQLite 中历史记录可读、新记录可写、汇总不漂移。
4. 已用冻结 base/head 完成独立 Review、主控裁决和修订闭环；未自动应用 finding。
5. 已刷新物理安装并保留 0.12.3 回滚基线；0.13.7 当前运行于专用 `codex/0.13-foundation` 分支。
6. 已完成 Core、Coding、Review 的 GUI 重启后免费状态验收；Perception/H3 在实际调用前做对应专项回归。
7. 已按用户决定切换供应商无关的全局提示词；后续只在真实样本发现可复现缺陷时进入 0.13.x 小版本修订。

## 全局提示词切换门禁

0.13.7 物理部署通过后，用户级 `%USERPROFILE%\.codex\AGENTS.md` 已切换为供应商无关的
“Ask AI 受限 Worker”表述，并经 GUI 重启后的新会话验证。模型、价格、额度、超时和
路由顺序等易变信息只从实时 MCP schema/backend status 获取，不复制进全局提示词。
项目专属的架构、留存、验证和发布细节继续只保留在仓库 `AGENTS.md` 与版本文档中，
避免全局上下文重复膨胀。

## 0.13.2 Grok 与本地路由

- Coding 高级候选与 Review 增加显式 `grok-4.6`，固定走 Responses 协议；
- 本地账本实现 Grok 200K 输入阈值的两档价格，不需要 Controller 临时换算；
- backend status 给出谷峰价格、远端可用性和有效余额过滤后的建议顺序；
- 建议顺序不构成自动路由，失败仍禁止自动重试、分片和换模型；
- 按作者的账户级授权接受 OpenCode 同类模型的数据留存/改进条款，不增加逐仓库留存开关。

## 0.13.3 Grok 标准价门禁

- 首次真实 Review 证明 OpenCode Grok Responses 路由存在，但通用 `reasoning=max` 被上游即时 400；
- 按 Grok 官方枚举改用 `xhigh`，不以第二次付费请求试错；
- Review/Coding 在付费前将 Grok 输入限制为 199,999 token，达到 200K 高价档时 fail closed；
- Review 返回确定性分片建议，Coding 要求显式减少 target/context 文件；两者均不自动重新提交。

## 0.13.4 Grok 流式传输灰度

- 0.13.3 的真实 Review 请求完整发出后，远端在约 30 秒内、任何响应头之前断开；
- Grok Responses 改为单次 SSE 流式请求，以便上游尽早建立响应并持续发送事件；
- 只接受 `response.completed`、`response.failed` 或 `response.incomplete` 的完整终态对象；
- 缺少终态、畸形 SSE 或中途断流继续 fail closed，不自动重试、换模型或追加调用；
- GLM、Kimi 与 DeepSeek 的 Chat Completions 传输保持不变。

## 0.13.5 SSE 尾部断流门禁

- 真实 Grok Review 已连续运行 10 分 27 秒并成功，证明 SSE 消除了约 30 秒无响应头断连；
- 若完整终态事件已收到并通过严格解码，随后发生尾部读错误时保留该终态；
- 缺少终态、半截 JSON、畸形 SSE、非 Grok 路线继续按 transport failure 处理；
- 加密 wire 以 `complete_after_transport_error` 记录内容无关的尾部异常类型；
- Review/Coding 都有 manager-level 单调用测试，禁止借此重试或回退模型。

## 0.13.6 Review staging 诊断与策略路由

- staging 净化不等价时只返回长度、哈希、段数量、排除原因计数、宿主路径替换计数和前导字节数；
- `.meta` 等非审查文本仍被排除，不保存失败补丁正文、文件路径或匹配值；
- Review 默认 `route_mode=policy`，由 MCP 在唯一 provider call 前结合峰谷、availability、有效余额
  和输入边界原子选择一条首次 Worker 路线；
- 旧客户端显式传 `model` 时兼容为 `route_mode=explicit`，但控制者 Codex 模型永远不是 Review
  Worker 枚举；
- 选路只发生在首次提交前，任何失败都不得触发重试、分片、回退、换模或第二 job。

## 0.13.7 本机存储留存

- `usage_status` 复用现有 Core 接口，返回 11 个不含宿主路径和正文的存储域状态；不新增留存 MCP
  入口，也不开放任意删除接口。
- `usage.db`、`code-review/review.db` 属于逻辑永久账本；阈值只触发维护提示，不静默删除调用、
  finding、裁决或 outcome。`registry` 是已批准产品状态，禁止自动淘汰。
- Coding、Review、Toolsmith、Source、verified run、smoke 和新版 Review staging 仅在终态、完整导出、
  receipt 与哈希一致后，才允许按完整目录从旧到新滚动。Review 有 finding 时还必须全部完成永久裁决。
- 新 staging 使用 patch SHA 命名目录原子发布 `review.patch` 与 `receipt.json`；0.13.6 旧双文件继续
  可读但保持保护状态，不做猜测性迁移或清理。
- 纯 LLM replay 固定为 100 MiB；只淘汰完整、哈希有效、未标记保留的 capsule。单个 capsule 超限
  在写入前拒绝，不能先删旧数据再失败。
- Windows 状态轮询与终态 `job.json` 原子替换发生短暂共享冲突时，只重试本地文件替换；绝不重发
  provider 请求。
