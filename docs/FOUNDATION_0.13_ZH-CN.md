# Ask AI MCP 0.13 测试底座

## 定位

0.13.1 是隔离开发和灰度验证用的测试大版本。当前生产基线继续保持
0.12.3；未经完整离线验收、冻结差异外部 Review、物理机灰度和用户明确
合并决定，不得替换生产入口。

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

1. 在隔离 worktree 完成 Ruff、格式检查和完整离线 pytest。
2. 对七个 profile 校验工具顺序和参数 schema 指纹。
3. 校验同一 SQLite 中历史记录可读、新记录可写、汇总不漂移。
4. 以冻结 base/head 将 0.13 差异交给独立 Review 经纪；不自动应用 finding。
5. 物理机并行注册测试入口，生产 0.12.3 保持可回退且不覆盖。
6. 分别灰度 Core、Coding、Review；Perception/H3 只做对应专项回归。
7. 灰度完成后由用户单独决定是否合并、部署及更新全局提示词。

## 全局提示词切换门禁

开发阶段不修改 `%USERPROFILE%\.codex\AGENTS.md`。只有 0.13 物理部署通过后，
才备份旧文件并将角色表述统一为“Ask AI 受限 Worker”；模型、价格、额度、
超时等易变信息继续只从实时 MCP schema/backend status 获取。新会话必须验证
提示词已加载，且持久指令字节数不得因重复项目细节而膨胀。
