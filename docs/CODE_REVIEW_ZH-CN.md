# 独立异构 Coding Review

## 产品边界

`ask-ai-mcp-review.exe` 是可选的只读 reviewer，不是 coding agent。它不属于 Core、
Subagent、Perception、H3 或 Full，普通开发保持关闭。固定工具只有：

- `code_review_backend_status`：本地配置、三个固定模型的远端可用性、双层账本与月报；
- `code_review_stage_patch`：免费校验并原子封存 bounded patch 与内容无关 receipt；
- `code_review_submit`：提交冻结 refs 或同时固定 patch/receipt 哈希的 staged patch；
- `code_review_status`：分页读结果，并记录盲审裁决或延迟 outcome。

固定模型为 `glm-5.3`、`kimi-k3`、`deepseek-v4-pro`。三条 Review 路由统一请求
`reasoning_effort=max` 与 131,072 token 总生成上限。该上限同时容纳 reasoning 和可见 JSON，
不是上下文窗口；宽松基线用于预付额度测试期，后续根据分模型日志中的 reasoning、可见输出、
finish reason 与成本分布再收敛。固定 profile 为 `general`、`security`、
`concurrency`、`data_integrity`。没有 generic prompt、任意模型、
任意 URL、Shell、任意文件写入、Git 写操作、提交、推送、自动重试或默认补丁；唯一写入面是
配置好的项目专属仓库外 patch staging 目录。

每次任务把实际 reasoning effort 与输出上限写入 manifest、Review SQLite 和共享 API usage
账本。若 provider 返回 `finish_reason=length`，且 reasoning token 占 completion token 至少 95%，
任务以 `REASONING_BUDGET_EXHAUSTED` 失败；其他长度截断为 `OUTPUT_TRUNCATED`。状态接口返回
机器可读 `failure_code`、安全明确的 detail 和仅含哈希/usage 的 audit artifact，不会自动重试。
HTTP/传输失败另以不含响应正文的 `provider_failure_class` 区分 timeout、rate-limit、auth、
upstream、transport 与 unknown。

付费前的 prompt 预检只针对模型上下文硬边界：以固定 UTF-8 估算、1M context、128K 总生成
预算和额外安全余量判断。普通 2–3 万 token 大审查不会因为旧 8K/16K 经验而被提前拒绝；只有
逼近上下文边界时才以 `REVIEW_PARTITION_REQUIRED` 零 provider call 失败，并返回确定性、
advisory-only 的按文件分片计划。Controller 必须显式生成并重新封存每个 bounded patch；服务端
不会自动分片、自动提交或自动重试。

## 仓库与快照安全

`ASK_AI_MCP_REVIEW_REPOSITORIES` 是仓库 ID 到精确 Git 根目录的 JSON 映射。解析后拒绝盘符
根、整个用户目录、非 Git 根、路径穿越、逃逸 symlink/junction/reparse point 和 submodule
内容。每个 patch root 的末级目录名必须与仓库 ID 完全一致。`code_review_stage_patch` 先验证
patch 已是最终脱敏 diff，再在该目录写临时文件，由控制器计算精确 UTF-8 字节数和 SHA-256，
按 hash 原子改名为只读 `.patch`，最后原子写只读 `.receipt.json`。任何中断最多留下无 receipt
的孤立 patch，后续提交会 fail closed；不会从 refs 重建、修复或自动重试。
`code_review_backend_status` 只报告 patch root 是否配置及数量，不返回宿主路径。
Codex/Claude 的具体白名单配置片段、转义规则和重启验收见
[简中使用与运维说明书](USER_MANUAL_ZH-CN.md#46-codingreview-仓库白名单配置)。

Controller 用固定的只读 Git 子命令解析两个 commit，生成不可变 diff，排除密钥、二进制、
vendor、生成物、超大文件和过量上下文。模型只收到仓库 ID、相对路径、最小 hunk 邻域和遗漏
说明，不收到真实宿主绝对路径或整个仓库。finding 必须落在给定改动 hunk 附近；模型返回的
evidence hash 由 controller 根据结构化证据摘要重新计算。
供应商常见的 `bug`、`regression`、`missing_tests` 等 category 同义词会被确定性归一到固定
枚举；prompt v2 同时显式要求每条 finding 只能使用 `correctness`、`security`、
`reliability`、`performance`、`maintainability`、`testing`。文件、行号、hunk、置信度和
其余结构约束不会因此放宽。
prompt v3 另把本次不可变快照的合法文件路径作为独立 JSON 数组列出，要求逐字复制其中一项。
路径比较保持大小写敏感；Git diff 的 `a/`、`b/` 前缀、相似前缀和父目录逃逸仍会拒绝。既有
Windows 反斜杠到仓库标准 `/` 的确定性规范化保持不变，但 prompt 要求直接输出标准 `/`。

本地状态默认在 `%LOCALAPPDATA%\AskAIMCP\code-review`。每个 job 的 `input`、`output`、
`audit` 分离。`review.db` 只保存快照/diff/结构化输出哈希、token/成本/延迟、finding 指纹、
裁决与 outcome，不保存完整源码、完整 diff、完整 prompt、密钥或常规模型完整输出。

## OpenCode Go 双层账本

价格目录固定为 `opencode-go-2026-08-27`，带 `effective_at` 和官方 source URL；远端
`/zen/go/v1/models` 只用于健康/可用性检查，不能静默改价或扩张 reviewer 模型枚举。

官方 Go 是双层限制：订阅共享滚动 5 小时 `$12`、每周 `$30`、每月 `$60`；模型月度 included
usage 上限分别为 GLM `$15`、Kimi `$15`、DeepSeek Pro `$15`。Coding 模块另用 GLM Flash
`$15` 与 DeepSeek Flash `$30`，但五个模型仍共享同一订阅的 `$60` 月窗口。

DeepSeek 仅在周一至周五 UTC `01:00–04:00`、`06:00–10:00` 使用 peak 费率；周末全天
off-peak，边界采用左闭右开。
账本分别保存 input、output、cache-read、cache-write token 与单价；官方未给出的价格项为
`null/unsupported`，不能默认为零。API 返回的实际 usage/成本与本地估算分开；拿不到供应商
权威余额时 `estimated=true`。所有共享窗口按 API 可见账户 UID 聚合一次，
不会把同一笔跨模型消费重复扣除。多个合法订阅必须显式配置不同 ID、凭据和路由；模块不会
自动轮转账户或绕过限额。

远端成功但本地结构校验失败也会计入失败调用和估算成本。审计仅保留响应 SHA-256、字节数、
finish reason 与 usage，完整常规模型响应不进入日志；通过校验的 findings 才进入独立输出。

官方目录：<https://opencode.ai/docs/go/>。

## 冻结 A/B/C 与裁决

先完成实现、测试、真实灰度和 Sol 常规审查，再冻结明确的 base/head commit 与 diff hash。
同一 `review_group_id` 强制绑定完全相同的 diff hash、prompt/contract 版本、输出上限和采样
配置；三个模型隔离运行，不能互看结果。修复不改写基线，而是在之后另开提交。

裁决阶段 `code_review_status` 隐藏模型名，逐条记录 TP、FP、duplicate、non-actionable 或
uncertain，以及 severity agreement、accepted/fixed、test-confirmed 和耗时。月报按语言、
任务类型、diff 大小、风险等级分层，汇总 precision、unique true findings、基于裁决集合的
recall proxy、false-positive burden、duplicate rate、severity calibration、测试确认率、
每千改动行发现数、每个采纳问题成本/延迟和 escaped defects。recall proxy 不代表绝对 recall。

第一批样本对同一冻结 diff 做 GLM/Kimi/Pro 影子 A/B/C；积累样本后再由效果日志决定
是否购买单模型 Coding Plan 或继续使用 Go 的异构深审组合，不预设赢家。

## 后续数据回收

先运行约十天至半个月的真实工况，再按模型分别统计 reasoning/visible 比例、`length`、结构化
成功率与成本分位数。只有日志证明 128K 长期没有收益时才下调；不得重新引入跨模型统一的
8K/16K 紧上限。显式 `include_files` 或 shard ID 仍属于后续契约，当前计划只提供建议。
