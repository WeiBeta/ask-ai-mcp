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
