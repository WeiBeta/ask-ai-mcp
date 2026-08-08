# Ask AI MCP 简中使用与运维说明书

适用版本：Ask AI MCP 0.4.1
适用平台：Windows 11
适用客户端：Codex Desktop、Claude Desktop
仓库：`xujinglong8814-WeiBeta/ask-ai-mcp`（私有）

## 1. 这套系统现在能做什么

Ask AI MCP 已完成双端加载、跨客户端发现、独立完整审阅、双端批准、注册和 Docker 隔离运行验收。GPT/Codex 与 Claude 现在可以在规则允许时，把重复、机械、可测试的工具制造工作交给 DeepSeek。

需要特别区分：

- **调用 DeepSeek**：目前只有 `build_helper_tool` 及其服务端自动修复会调用 DeepSeek API 并产生费用。
- **运行已验证工具**：`run_verified_tool` 在本机 Docker 中离线运行，不调用 DeepSeek，不产生模型费用。
- **查询和审批**：用量、预算、操作指南、待审队列、摘要、完整审阅、批准和注册表查询都是本地操作，不调用 DeepSeek。

0.4.1 没有开放任意提示词转发，也没有开放“把整份业务文件直接交给 DeepSeek 处理”的接口。DeepSeek 负责制造候选工具；经过验证的本地工具负责处理明确暂存的文件副本。

## 2. 不可突破的角色边界

| 角色 | 必须负责 | 可以委派 | 禁止委派 |
|---|---|---|---|
| GPT/Codex | 架构、安全、规格、测试要求、仓库修改、最终结论与交付 | 可机械验证的脚本候选、测试、格式检查器、解析器 | 最终事实、正文、来源冲突、发布判断 |
| Claude | 事实综合、来源仲裁、文档结构、文风、篇幅、表格表达与交付质量 | 文档预处理工具、格式检查工具、机械性代码候选 | 正文撰写、续写、共同写作、最终措辞与结论 |
| DeepSeek | 受限工具制造、候选修复、机械性代码 | 严格规格下的候选文件和测试 | 直接交付正文、事实判断、整份知识库处理、自我批准 |

DeepSeek 的任何代码均不可信。通过静态检查和隔离测试，只能证明它满足已有检查与测试，不能替代 GPT/Codex 或 Claude 的独立审查。

## 3. 什么时候应当调用 DeepSeek

只有同时满足以下基础条件，才考虑调用：

1. 结果能够机械验证；
2. 结构化规格明显短于预期产物；
3. 预计至少需要三轮实现或调试，或者产物将注册并重复使用。

### 3.1 推荐工况

- DOCX、PPTX、XLSX、PDF、Markdown、文本等来源的窄范围解析器；
- 表格表头归一化、CSV/XLSX 结构清单、坐标与来源映射工具；
- 文档格式、页眉页脚、样式、工作表结构、幻灯片结构检查脚本；
- 文件清单、哈希、重复文件、缺失文件和命名规范检查；
- 合成夹具、单元测试、回归测试和格式故障复现；
- 预计会反复失败、反复调试的小型机械性脚本；
- 将作为本机复用工具注册的预处理或验证器。

### 3.2 不应调用的工况

- 简单、一次性、几分钟即可直接完成的脚本；
- 主要工作是推理、写作、判断、总结或解释；
- 规格比预期代码或产物还长；
- 无法写出确定验收测试；
- 需要决定哪个来源正确、采用哪个数字或形成业务结论；
- 需要生成可直接放入 DOCX、PPTX、XLSX 或报告的正式正文；
- 需要发送密钥、完整知识库、整份业务文件或大量无关原文。

### 3.3 一个简单判断式

> 如果“写规格 + 审候选”的成本不明显低于控制模型直接完成，就不要调用 DeepSeek。

## 4. 标准文件目录

### 4.1 可漫游交接目录

本机标准目录：

```text
%USERPROFILE%\Documents\AskAI-Exchange
```

当前物理机实际路径：

```text
C:\Users\user\Documents\AskAI-Exchange
```

目录结构：

```text
AskAI-Exchange\
├─ staged-input\       # 明确选中的业务文件副本；唯一允许 MCP 读取的根
├─ accepted-output\    # 经控制模型复核后接受的中间产物
├─ handoff\            # GPT/Claude 交接说明、审查报告、说明书
├─ manifests\          # 来源映射、原始路径、哈希、工具版本、验证证据
└─ archive\            # 已结束任务的冷归档
```

只有 `staged-input` 被配置为 `ASK_AI_MCP_ALLOWED_INPUT_ROOTS`。MCP 无权从 `accepted-output`、`handoff`、`manifests` 或 `archive` 读取业务内容。

推荐每个任务使用独立 ID：

```text
20260808-customer-a-table-check
```

对应目录示例：

```text
staged-input\20260808-customer-a-table-check\
accepted-output\20260808-customer-a-table-check\
handoff\20260808-customer-a-table-check\
manifests\20260808-customer-a-table-check\
```

原始业务文件不要直接放入交接目录。先复制所需文件到 `staged-input/<task-id>/`；原件保持只读并留在原业务位置。

### 4.2 项目源码目录

```text
C:\Dev\ask-ai-mcp
```

用途：服务端源码、测试、文档、`uv.lock` 和 Git 历史。源码通过私有 GitHub 仓库同步，不应把业务文件提交到仓库。

### 4.3 本机运行状态目录

```text
%LOCALAPPDATA%\AskAIMCP
```

典型内容：

```text
AskAIMCP\
├─ usage.db            # 用量、预算、生命周期、审阅证明等 SQLite 状态
├─ jobs\               # 候选规格、候选文件、测试和审查证据
├─ registry\           # 已批准、精确哈希固定的工具注册表
└─ runs\               # 每次已验证工具运行的暂存与输出
```

这是**本机状态**，不是日常双机实时同步目录。不要在两个正在运行的客户端之间实时同步 `usage.db` 或整个状态目录。

### 4.4 凭据位置

DeepSeek API 密钥保存在 Windows Credential Manager，不在仓库、配置文件或交接目录中。迁移到另一台物理机时应在目标机重新录入，不要导出到普通文本文件。

### 4.5 双端配置位置

Codex：

```text
%USERPROFILE%\.codex\config.toml
%USERPROFILE%\.codex\AGENTS.md
```

本机 Claude MSIX：

```text
%LOCALAPPDATA%\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Roaming\Claude\claude_desktop_config.json
```

不同 Claude 安装渠道的路径可能不同，应以 Claude Desktop 的“Edit Config”实际打开位置为准。

## 5. 标准业务交接流程

### 5.1 准备任务副本

1. 为任务创建唯一 `task-id`；
2. 把必要的原始文件复制到 `staged-input/<task-id>/`；
3. 在 `manifests/<task-id>/source-manifest.md` 记录：
   - 原始路径；
   - 暂存副本相对路径；
   - SHA-256；
   - 文件大小；
   - 复制时间；
   - 已知限制或敏感级别。
4. 不复制与任务无关的整包资料。

注意：`run_verified_tool` 会把明确列出的文件再次复制进私有运行目录，并重命名为 `input-0001.ext` 等稳定名称。若业务需要保留原始文件名映射，应在 manifest 中保存“原始文件 → SHA-256 → staged_name”关系，不能仅依赖工具输出中的暂存名称。

### 5.2 控制模型决定是否需要新工具

优先调用本地接口：

- `list_registered_tools`：是否已有合适工具；
- `workflow_guidance(topic="overview")`：查看总规则；
- `workflow_guidance(topic="build")`：需要造工具时查看构建规则；
- `list_pending_reviews`：查看另一客户端留下的待审候选；
- `usage_status`：查看试运行期费用和生命周期经济性。

若已有已验证工具，直接进入本地运行流程，不需要调用 DeepSeek，也不需要打开预算会话。

### 5.3 需要制造新工具时

1. 当前聊天调用一次 `open_budget_session`；
2. 控制模型自行定义窄规格和验收测试；
3. 只发送合成或最小化脱敏夹具描述；
4. 调用 `build_helper_tool`；
5. 服务端进行静态检查、Docker 隔离测试和有限修复；
6. 通过后仅产生 `review_pending` 候选，不会自动注册或执行。

同一聊天必须复用同一个 `budget_session_id`，不得重开会话绕过预算，也不得把 Claude 的 ID 给 Codex 使用，反之亦然。

### 5.4 审阅与批准

1. `review_tool_candidate(mode="summary")`：核对身份、哈希、文件、风险、测试和尝试记录；
2. `mode="full"`：控制模型独立阅读完整补丁，并记录精确审阅证明；
3. 发现测试缺口、安全问题或规格偏离时停止，不得修改旧候选后继续批准；
4. 只有精确未修改哈希才能批准；
5. Pro 或具备 `write_dedicated_output` 的工具，需要 Claude 与 Codex 对同一哈希分别完整审阅并批准；
6. `list_pending_reviews` 用于跨端接力，不需要人工抄写完整补丁。

### 5.5 本地运行与结果接收

1. `list_registered_tools` 必须显示 `runnable = true` 且 `blocking_reasons = []`；
2. 只把 `staged-input` 下的明确文件路径传给 `run_verified_tool`；
3. 运行器再次复制输入，只读挂载候选与输入，网络关闭；
4. 工具只向 `%LOCALAPPDATA%\AskAIMCP\runs\<run-id>\output` 写入；
5. 控制模型核对输出内容、哈希、数量、来源关系和业务语义；
6. 只有接受的结果才复制到 `accepted-output/<task-id>/`；
7. 把运行 ID、工具名、版本、candidate SHA、输入哈希和输出哈希写入 `manifests/<task-id>/`。

工具输出只是预处理材料，不会自动成为事实或正文。

## 6. GPT/Codex 的预期工况

GPT/Codex 是工程控制者。典型工作方式：

- 发现 Office/PDF/表格处理需要反复写脚本时，判断是否值得制造复用工具；
- 自行制定数据契约、最小依赖、禁止能力和测试；
- 让 DeepSeek 生成候选实现和测试；
- 自行完成静态、安全、架构和仓库级审查；
- 绝不让 DeepSeek 直接修改正式仓库；
- 将通过审查的通用工具注册并复用；
- 对 DeepSeek 输出做机械验证，不把摘要当作正确性证明。

例子：

- 合适：编写一个可重复使用的 XLSX 表头与合并单元格检查器；
- 合适：为复杂 OOXML 故障制造最小复现脚本和回归测试；
- 不合适：只需十行即可一次完成的文件改名脚本；
- 不合适：决定技术方案、形成正式架构结论或撰写交付说明。

## 7. Claude 的预期工况

Claude 是业务文档控制者。典型工作方式：

- 遇到复杂表格、格式或来源包时，可直接为自己的工作流制造窄工具，不必让 Codex 代为发起；
- 让工具承担结构盘点、位置记录、格式异常检查等机械任务；
- 回到原始来源或可验证副本，独立核验数字、页码、工作表、单元格和关系；
- 自己负责正文、事实综合、文风、篇幅和交付品质；
- 审阅 Codex 创建的输出型工具时必须亲自加载完整补丁，不能把 Codex 的批准当作自己的批准。

例子：

- 合适：制造 PPTX 页级结构清单和版式异常检查工具；
- 合适：制造 DOCX 表格尺寸、跨页、样式一致性检查工具；
- 不合适：让 DeepSeek 补写缺失的 1300 字正文；
- 不合适：让 DeepSeek 模仿当前文风共同撰写报告；
- 不合适：让 DeepSeek决定冲突来源中哪个数据应写入正文。

## 8. 模型与预算规则

### 8.1 Flash

- 每个聊天预算会话初始 CNY 5；
- 状态为 `active` 时无需逐次向用户确认；
- 用尽后进入 `flash_extension_required`；
- 每次只能在用户明确同意后增加一个 CNY 5 块。

### 8.2 Pro

- 默认预算为 CNY 0；
- 首次启用和每次续额都必须说明模型、必要原因和 CNY 5 金额；
- 必须收到用户明确同意；
- 不得从 Flash 预算推导 Pro 授权；
- 不得自动升级到 Pro。

### 8.3 修复边界

- 初始候选默认 Thinking High；
- 静态策略修复最多一次，关闭 Thinking，输出上限 4096 token；
- 语义测试修复使用 Thinking High，输出上限 16384 token；
- 一个生命周期最多三个候选尝试；
- 无效的首次结构化响应可重新生成一次；
- 生命周期数和 API 调用数是审计指标，不是额外硬上限。

已开始的生命周期允许结束并轻微超额；下一个生命周期必须等预算续额。

## 9. 备份策略

### 9.1 日常漫游备份

优先备份：

```text
%USERPROFILE%\Documents\AskAI-Exchange
```

该目录包含业务暂存副本、已接受输出、交接报告、manifest 和归档。可以纳入用户现有的 Documents 备份方案。不要把 Windows Credential Manager 导出的明文密钥放进去。

### 9.2 源码备份

源码与说明书通过私有 GitHub 仓库备份：

```text
https://github.com/xujinglong8814-WeiBeta/ask-ai-mcp
```

业务文件、API 密钥、`%LOCALAPPDATA%\AskAIMCP` 和桌面个人配置不得提交到仓库。

### 9.3 本机状态冷备份

需要保留预算、审阅证明、候选和注册工具时，对以下目录做冷备份：

```text
%LOCALAPPDATA%\AskAIMCP
```

正确步骤：

1. 完全退出 Claude Desktop 与 Codex Desktop；
2. 确认 Ask AI MCP 进程已经结束；
3. 再复制整个 `%LOCALAPPDATA%\AskAIMCP`；
4. 把备份标注物理机、Windows 用户、Ask AI MCP 版本、时间和 Git commit；
5. 不要在服务运行时用云盘实时双向同步 `usage.db`。

建议保留级别：

- 必须：`usage.db`、`jobs`、`registry`；
- 可选：`runs`。如果已把重要结果和 manifest 保存到 `AskAI-Exchange`，旧 runs 可按保留策略归档；
- 不可替代：Credential Manager 中的 API 密钥，应在目标机重新录入。

### 9.4 配置备份

可以备份经过检查的配置片段和双端简中提示词，但迁移后必须更新：

- Windows 用户名与绝对路径；
- Claude 实际配置文件位置；
- 仓库路径；
- `ASK_AI_MCP_ALLOWED_INPUT_ROOTS`；
- 目标机的 Docker/WSL、Python 与 uv 环境。

## 10. 迁移到另一台物理机

当前建议流程：

1. 安装系统级 Git、Python 3.13、uv；
2. 启用或安装 WSL 2 与 Docker Desktop，不关闭现有 Windows 功能；
3. 登录专用 GitHub 账号并克隆私有仓库到 `C:\Dev\ask-ai-mcp`；
4. 运行 `uv sync --all-groups`；
5. 在 Windows Credential Manager 重新录入 DeepSeek API 密钥；
6. 创建 `%USERPROFILE%\Documents\AskAI-Exchange` 目录结构；
7. 恢复漫游交接目录；
8. 如需继承审阅、预算和注册工具，停机恢复 `%LOCALAPPDATA%\AskAIMCP`；
9. 合并 Claude 与 Codex MCP 配置，修正绝对路径；
10. 重启两个 GUI，确认 12 项工具；
11. 调用 `workflow_guidance`、`list_pending_reviews`、`usage_status` 做无费用检查；
12. 用合成 CSV 做一次 `run_verified_tool` 回归，不调用 DeepSeek。

正式的自动化迁移包仍是后续事项。在迁移工具完成前，优先采用“Git 源码 + Documents 交接目录 + 停机状态备份 + 目标机重新录入密钥”的方式。

## 11. 常见状态与处理

| 状态或错误 | 含义 | 处理 |
|---|---|---|
| `active` | 当前模型预算可用 | 可以开始新候选生命周期 |
| `flash_extension_required` | Flash 块已耗尽 | 说明金额并询问是否追加 CNY 5 |
| `pro_authorization_required` | Pro 尚未授权 | 说明必要原因与 CNY 5，等待明确同意 |
| `pro_extension_required` | Pro 额度耗尽 | 再次逐次申请 CNY 5 |
| `dual_desktop_approval_required` | 输出型或 Pro 工具缺第二端批准 | 另一桌面完整审阅并批准精确哈希 |
| `full_review_required` | 当前批准方未加载精确完整补丁 | 调用 `review_tool_candidate(mode="full")` |
| `execution_contract_missing` | 历史工具无标准运行契约 | 保持不可运行，不猜测补齐 |
| `input file is outside configured allowed roots` | 输入不在 `staged-input` | 复制必要文件到任务目录；不要扩大到用户目录或盘符根 |
| `runner unavailable` | Docker/WSL 运行器未就绪 | 检查 Docker Desktop 与 WSL 2，不关闭其他系统功能 |

## 12. 当前已验证工具

### `synthetic_csv_inventory` 0.1.0

- candidate SHA-256：`ef2f04d6752705d038b8f0ed104e24612cfee830f7669412be60659d8a600335`；
- 双端批准：已完成；
- 能力：读取暂存副本、写专用输出目录；
- 状态：可运行；
- 用途：确定性盘点暂存 CSV 表头、数据行数与列数；
- 注意：正式执行器会将输入平铺并命名为 `input-0001.csv` 等稳定名称，原始路径映射应记录在 manifest 中。

### `normalize_synthetic_headers` 0.0.1-smoke

- 历史烟雾候选；
- 缺少标准 execution contract 和输出能力；
- 状态：不可运行；
- 不应尝试猜测补齐或直接用于业务。

## 13. 每次任务结束前的检查清单

- [ ] 原始业务文件未被修改；
- [ ] 只暂存了任务所需副本；
- [ ] 输入和输出 SHA-256 已记录；
- [ ] 使用的工具名、版本、candidate SHA 与 run ID 已记录；
- [ ] DeepSeek 只参与允许的机械性工作；
- [ ] 最终事实、正文与结论由 GPT/Codex 或 Claude 独立完成；
- [ ] 重要输出已从本机 runs 复制到 `accepted-output`；
- [ ] 双端交接内容已保存到 `handoff`；
- [ ] 任务 manifest 完整；
- [ ] 需要漫游的目录已进入备份；
- [ ] 不再需要构建时已关闭当前预算会话。

## 14. 安全底线

- 不开放盘符根目录、整个用户目录或完整业务资料库作为输入根；
- 不把 API 密钥写入 JSON、TOML、Markdown、日志、Git 或交接目录；
- 不把整份业务文档通过 `build_helper_tool` 发送给 DeepSeek；
- 不让 DeepSeek 撰写或续写最终正文；
- 不因测试通过而省略独立代码审查；
- 不在两个活动进程之间实时同步 SQLite 状态库；
- 不自动批准、自动升级 Pro 或绕过 CNY 5 预算门禁；
- 不关闭、卸载或降级物理机已有 Windows 功能来满足本项目。
