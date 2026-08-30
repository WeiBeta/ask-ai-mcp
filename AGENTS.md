# Ask AI MCP 仓库指令

## 产品与工具链

- 当前产品目标仅为 Windows 11，本阶段不增加 macOS 支持。项目限制应实现在 runner、容器或代码中，不得通过缩减物理机能力实现。
- 使用系统 Python 3.13 和系统 `uv`。依赖必须安装在 `.venv`，并与 `uv.lock` 保持一致。
- MCP stdout 必须保持协议纯净；应用日志只能写入 stderr 或本地审计存储。

## 架构与职责边界

- 全局 Ask AI Worker 安全规则在本仓库继续生效。供应商和模型名称只是路由元数据，不是职责名称或可持久的业务逻辑。
- 保持 Core、Coding、Review、Perception、H3 及兼容 Full 的显式边界。不得新增 generic prompt forwarding、任意模型/URL/路径/Shell 或无限制委派接口，也不得为方便而把专用工具并入常驻入口。
- 外部生成代码必须留在仓库外候选区，通过静态检查和隔离测试后才能返回候选 patch；不得直接修改工作树。
- 只委派可机械验证、预计需要至少三轮实现/调试，或会产生可复用注册工具的任务；委派规格必须明显短于预期产物。

## Codex 任务与经纪会话

- 项目主线控制者负责真源核验、架构与策略、最终实现取舍、本地验证、finding 裁决和交付决定。长期 Coding/Review 经纪会话只负责免费 backend/schema/白名单/额度/冻结范围预检、创建唯一仓库外作业、观察同一作业并回传结构化结果；不得实现代码、裁定 finding、应用候选或修改仓库。
- 新建机械 Coding/Review 经纪任务显式使用 `gpt-5.6-luna` / `max`。涉及源码实现、调试、独立审查、finding 裁决、Evidence 核验或集成的任务使用 `gpt-5.6-sol` / `xhigh`。指定组合不可用时停止并如实回报，不得静默降档、换模型或沿用默认值。
- 派工必须给出任务 ID、目标、不可变 base/head、允许与禁止路径、验证条件、预算/付费边界、回执目标和下一门禁。Coding/Review 只接收冻结输入；不得把控制模型名称误当成外部 Worker 路由。
- 经纪会话没有新工单时保持空闲，不建立长期 active Goal、heartbeat、定时巡检或空轮询。收到工单后只为该工单建立有终点的短期工作；完成、拒绝或阻断后主动向主线发送一次结构化回执并收口。主线不得用 `wait_threads`、`read_thread` 或重复消息维持经纪会话活跃；只有主动回执缺失后的人工排障可做一次有界读取。
- 经纪回执至少包含任务 ID、终态、实际模型/策略、base/head 或 patch/receipt 哈希、job/group ID、冻结输入规模、调用次数、等待与总耗时、用量/成本可见性、failure/validation/transport 分类、分页完整性、候选摘要、未实测项，以及自动重试/分片/换模型/第二作业/仓库写入均是否发生。外部结果仍由主线独立核验。
- 若完整结构化回执被任务消息工具或安全门禁拒绝，源任务不得反复改写、拆分或换通道；只允许向回执目标再发送一次最简失败回执，内容限于任务 ID、`ReceiptDeliveryFailed`、不含敏感细节的简短原因和“请主任务直接读取本任务”。若最简回执也失败，立即停止跨任务发送并在自身终态记录 `ReceiptDeliveryFailedTwice`，不得因此重做业务工作。
- 主线只有收到 `ReceiptDeliveryFailed`，或用户明确报告完整与最简回执均失败后，才可对指定源任务执行一次有界 `read_thread`；读取结果仍须结合固定 Git 对象、Evidence 与实际 worktree 独立核验。不得把最简失败回执解释为任务成功、失败或 finding 结论，也不得重复读取。
- 专用任务成功交付代码时，除非派工明确要求不提交取证，必须形成单一主题的普通提交，并证明 exact changed paths、HEAD 和最终 Git 状态；仍有任务产生的 staged、unstaged 或 untracked 文件时不得回执完成。提交不授权 push、merge、PR、发布或物理部署，这些仍遵循用户检查点。
- 新建 Codex 专用任务优先使用应用提供的物理 worktree，不得在其内部再次创建嵌套 worktree。开始 mutation 前核验实际路径、分支、HEAD、允许范围和现有用户改动；创建任务时的预备路径不能替代任务最终回执中的实际路径。
- 必须区分业务门禁/范围/哈希/状态失败与任务自己编写的只读命令错误。前者按协议停止；后者只有在先证明 HEAD、index、worktree 和 refs 零 mutation 后，才可在同一精确范围内修正并重跑一次，且不得借此重试付费调用、扩大范围或重复 mutation。
- 用户明确暂停、GUI 重启、断线或上下文压缩前，先保存可脱离聊天恢复的结构化暂停回执；恢复后从已核验状态继续，不重做已完成工作。不得自动归档或删除 Codex 任务、session、lock、数据库或保留中的证据。
- 建议、推荐默认、沉默或含糊短语不构成 push、merge、发布、付费扩容、系统变更或破坏性操作授权。需要用户检查点时采用四句式反馈：现状、限制、影响、需要用户做什么；等待前先完成不依赖该信息的安全工作。

## 源文件与产物安全

- 原始源文件只读。工具只处理副本，并写入专用输出目录。
- 文件访问必须在路径解析后落入明确白名单根；拒绝路径穿越、逃逸根目录的 symlink/junction/reparse point、盘符根和宽泛用户目录。
- 保留来源证据：文件哈希、页/幻灯片/工作表/单元格位置、提取方式、工具版本和警告。
- 常规 audit/usage/protocol 日志不得记录 API key、完整 prompt、完整源文本或常规模型完整输出；不得把整个知识库发送给 Worker。Coding/Review 的专用加密线缆证据仓是唯一例外：只允许用 Windows Credential Manager 中独立密钥进行分块认证加密，以调用归因 UID 关联，并按完整 UID 原子滚动淘汰；Authorization、Cookie、API key 永不入仓，解密能力不得暴露为 MCP 工具。

## Core toolsmith 生命周期

- Core、Coding 与 Review 共享供应商无关的订阅额度与审计底座，但保持各自的业务门禁；不得把旧 DeepSeek CNY budget session 当作当前协议。
- 当前预付订阅的 USD 2/日只是按月额度折算的使用节奏，不是日硬上限。已配置订阅在 backend/ledger 门禁内无需逐次确认；共享滚动窗口、单模型额度与上游限流仍可阻断调用，失败不得自动付费重试。
- 新增账户、订阅、充值、额度扩展或更昂贵路由仍须用户明确授权。历史 CNY budget 表只保留审计兼容，不得重新暴露为 MCP 工具。
- 不得放宽运行时与测试锁定的候选次数、regeneration、静态修复或语义修复上限。当前精确参数以 `lifecycle.py`、provider 常量和测试为真源，不得从历史提示词复制。
- 只有显式批准且哈希固定的工具才能处理真实文件副本。任何代码变更都会使工具恢复为 unverified。
- Verified execution 必须使用固定 `json_files_v1` contract、注册的精确 entrypoint 和专用输出。MCP schema 不得接受任意命令、函数名、输出路径、容器镜像或 mount 选项。
- Pro 或具有 `write_dedicated_output` 能力的工具，必须拥有与精确 job/candidate/patch 哈希绑定的当前客户端 full-review attestation；提升与执行仍须 Claude Desktop 和 Codex Desktop 双方批准。
- 持久指令、MCP 描述和构建/摘要结果保持简洁。状态流程使用 `workflow_guidance`，跨客户端接力使用 `list_pending_reviews`。

## 验证与固化

- 每项行为变更都必须增加或更新测试。普通单元测试不得运行 API integration test；集成测试必须显式 opt-in。
- 提交前运行 `uv run pytest`、`uv run ruff check .` 和格式检查。
- 接口、工具表面、安全门禁、价格/额度逻辑或运行时参数变更时，同步更新测试、用户文档、路线图与相关持久提示词。
- 保留无关用户改动，提交保持单一主题，不得将本机 local-only 配置或密钥纳入 Git。
- 面向自然人的版本说明、环境文档、交接与离线包必须提供简体中文版本；机器字段、稳定 ID 和无法翻译的第三方原文可保留原格式。工具失败、权限限制和未实测项必须明确记录，不得把近似或替代验证描述成完成。
