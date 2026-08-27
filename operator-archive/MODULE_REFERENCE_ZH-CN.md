# Ask AI MCP 通用模块与接口说明

适用版本：0.9.0
本文档只描述可复用产品结构，不记录某一台物理机的用户名、安装位置、环境变量值或 GUI
配置。机器实况由同目录脚本生成到 `local-only/`。

## 1. 入口与上下文税

| 入口 | 工具数 | 用途 |
|---|---:|---|
| `ask-ai-mcp-core.exe` | 12 | 综合 subagent/API agent：工具制造、验证、审批、执行、计量 |
| `ask-ai-mcp-perception.exe` | 3 | 特化 Qwen3.8 API agent：静态多模态来源结构化 |
| `ask-ai-mcp-h3.exe` | 4 | 特化本地 H3 agent：视频生成、插帧和超分 |
| `ask-ai-mcp-subagent.exe` | 15 | Core + Perception 的兼容组合入口 |
| `ask-ai-mcp-full.exe` | 19 | Core + Perception + H3 的兼容全功能入口 |
| `ask-ai-mcp-review.exe` | 3 | 独立、只读、默认关闭的异构 Coding Review |
| `ask-ai-mcp-coding.exe` | 3 | 独立、默认关闭、只产出外部候选 diff 的 Coding worker |

专用会话优先启用最窄入口：coding/文档工具制造只启用 Core，视觉来源处理只启用
Perception，视频生成只启用 H3，冻结代码评审才临时启用 Review。Review 不并入任何既有
入口；`subagent` 和 `full` 用于兼容，不是默认推荐方案。

## 2. 综合 subagent / API agent（Core）

### Provider

- `deepseek`：DeepSeek 独立 API，使用 CNY 会话预算门禁；
- `opencode`：OpenCode Go，固定路由 DSV4 Flash/Pro 与 GPT-5.6 Luna；
- `local_qwen`：本机 Qwen3.8 toolsmith 影子/边界测试，不等于默认生产替代。

通过 `ASK_AI_MCP_TOOLSMITH_PROVIDER` 显式选择。接口不接受任意 prompt、base URL、模型名、
Shell 命令或仓库写权限。

### 12 个 MCP 接口

| 接口 | 关键参数 | 功能 |
|---|---|---|
| `usage_status` | `days`：1–366，默认 15 | 读取本地 CNY/USD、token、provider 和生命周期统计 |
| `workflow_guidance` | `topic`：`overview/budget/build/review/approval/run` | 按需返回一个阶段的协议，减少常驻上下文 |
| `list_pending_reviews` | 无 | 返回跨 Claude/Codex 的待审队列，不返回正文或补丁 |
| `open_budget_session` | 可选 `command.label` | 创建会话预算 ID；DeepSeek Flash 初始 5 元、Pro 0 元 |
| `budget_status` | `command.budget_session_id` | 查询预算、调用次数和生命周期数 |
| `add_budget_block` | session ID、`model` | 用户明确授权后增加固定 5 元预算块 |
| `close_budget_session` | session ID | 关闭会话预算，不删除审计记录 |
| `build_helper_tool` | session ID、`spec` | 生成、静态扫描并隔离测试一个受限工具候选 |
| `review_tool_candidate` | `job_id`、`mode=summary/full` | 摘要审查或对完整补丁生成桌面身份凭证 |
| `approve_tool_candidate` | job/hash/version/capabilities | 将精确哈希候选写入本地注册表，不执行 |
| `list_registered_tools` | 无 | 重新哈希并列出已注册工具、审批和阻塞原因 |
| `run_verified_tool` | name/version/hash/input files/JSON params | 在 Docker 中处理白名单文件副本并输出独立产物 |

`ToolBuildSpec` 主要字段：

- `name`：3–64 位小写 snake_case；
- `category`：文档解析、表格处理、格式校验、OCR、测试或 coding；
- `purpose`、`input_contract`、`output_contract`：有长度边界的结构化规格；
- `acceptance_tests`：1–20 条机械验收条件；
- `allowed_packages`、`prohibited_capabilities`、`fixture_notes`；
- `model`：`deepseek-v4-flash` 或显式选择 `deepseek-v4-pro`。

构建样例：

```json
{
  "budget_session_id": "<UUID>",
  "spec": {
    "name": "extract_invoice_tables",
    "category": "document_parser",
    "purpose": "Extract invoice tables from staged synthetic fixtures.",
    "input_contract": "Read-only copied PDF fixtures inside the isolated input directory.",
    "output_contract": "JSON rows with source page and table coordinates.",
    "acceptance_tests": ["A stdlib unittest validates source coordinates."],
    "allowed_packages": ["pypdf"],
    "model": "deepseek-v4-flash"
  }
}
```

执行样例：

```json
{
  "command": {
    "name": "extract_invoice_tables",
    "version": "1.0.0",
    "candidate_sha256": "<64位SHA-256>",
    "input_files": ["D:\\BusinessInput\\invoice.pdf"],
    "parameters_json": "{\"language\":\"zh-CN\"}"
  }
}
```

## 3. 特化 Qwen3.8 API agent（Perception）

Provider 为 `opencode` 时固定使用 Qwen3.8 Max 的 Anthropic Messages 端点；Provider 为
`local_qwen` 时使用本机 OpenAI Chat Completions 兼容服务。两者共享相同的文件白名单、
PDF/PPTX 预处理、来源哈希、坐标校验和 `canonical_evidence_v1` 输出。

| 接口 | 参数 | 功能 |
|---|---|---|
| `source_backend_status` | 无 | 查询 provider、模型、协议和 ready 状态 |
| `source_extract` | `command` | 异步提交来源忠实结构化任务 |
| `source_job_status` | `job_id` | 查询进度及带哈希的 `evidence.json` 产物 |

`source_extract.command`：

- `source_files`：1–20 个白名单根目录内的绝对普通文件路径；
- `profile`：`document_evidence` 或 `visual_structure`；
- `detail_level`：`compact/standard/detailed`；
- `visual_scope`：视觉任务默认为 `structure_index`；需要指定对象详情时使用
  `selected_details`；节点/连线拓扑使用 `topology`；
- `focus_ids`：仅供 `selected_details` 使用，最多 8 个受限标识符，不接受自由提示；
- `focus_region_xywh`：仅供 `selected_details` 使用，必填；按原图宽高归一化的
  `[x,y,width,height]` 裁剪框，各值在 0–1 内且不得越界；
- `page_start/page_end`：可选且必须成对；
- `language_hint`：可选语言标签。

样例：

```json
{
  "command": {
    "source_files": ["D:\\SourceInbox\\workflow.pdf"],
    "profile": "visual_structure",
    "detail_level": "standard",
    "visual_scope": "structure_index",
    "focus_ids": [],
    "focus_region_xywh": null,
    "page_start": 1,
    "page_end": 8,
    "language_hint": "zh-CN"
  }
}
```

`structure_index` 每张视觉输入只返回一个受限结构索引：标题、区域/泳道、判断节点及接口
编号/名称/方法，不展开节点、连线或接口请求与返回字段。需要完整节点/连线时把
`visual_scope` 设为 `topology`；需要接口详情时再次提交相同来源，并设置：

```json
{
  "command": {
    "source_files": ["D:\\SourceInbox\\workflow.png"],
    "profile": "visual_structure",
    "detail_level": "standard",
    "visual_scope": "selected_details",
    "focus_ids": ["08", "17"],
    "focus_region_xywh": [0.78, 0.16, 0.21, 0.15],
    "language_hint": "zh-CN"
  }
}
```

`selected_details` 一次只处理一张图片、一个 PDF 渲染页或一个 PPTX 渲染页；文档输入
必须用相同的 `page_start/page_end` 选中单页。

当前生产边界是静态图片、PDF、PPTX 和 UTF-8 文本。视频、音频、DOCX、XLSX 与时间码
尚未进入公开契约。

## 4. 特化本地 H3 agent

| 接口 | 参数 | 功能 |
|---|---|---|
| `h3_backend_status` | 无 | 检查/按需启动 ComfyUI、模型和后处理权重 |
| `h3_generate_video` | `command` | 提交 480p、24 fps、4–15 秒 H3 原片 |
| `h3_postprocess_video` | `command` | 对已选原片执行显式插帧、超分或组合 |
| `h3_job_status` | `prompt_id` | 查询任务；终态且队列空闲时释放模型显存 |

生成参数：`prompt`、`resolution`（landscape/portrait/square 480p）、
`duration_seconds=4..15`、`seed`、可选 `first_frame/last_frame`。末帧必须同时提供首帧。

```json
{
  "command": {
    "prompt": "A locked camera observes rain moving across a neon street.",
    "resolution": "landscape_480p",
    "duration_seconds": 8,
    "seed": 42,
    "first_frame": "D:\\H3-Workspace\\inputs\\start.png"
  }
}
```

后处理参数：`source_video`、`visual_profile=anime/realistic`、`interpolation`、`upscale`、
`target_fps=24/48/72`、`target_short_side=480/720/1080`、`seed`。模型组合由接口严格校验，
ComfyUI 不猜测画风。任务完成后保持 ComfyUI 在线，只卸载模型并释放 VRAM。

## 5. 特化本地 ACE 1.5 agent

状态：规划占位，当前 0.9.0 仓库没有 ACE 1.5 MCP 接口、运行器、模型清单或已验证安装。
未来应保持独立音频入口，避免向 Core、Perception 或 H3 注入音频 schema。建议边界：

- `audio_backend_status`；
- `audio_generate`（异步提交）；
- `audio_job_status`；
- 必要时另设受限音频后处理接口。

在模型、许可证、时长、采样率、输入音频白名单、输出目录和 VRAM 释放行为完成实测前，
不得把此规划描述为可用功能。

## 6. 影子测试与 replay 模块

影子测试是本地 CLI/数据模块，不增加 MCP 工具声明，因此不产生 MCP 工具面上下文税。

- 设置 `ASK_AI_MCP_REPLAY_CAPTURE=1` 后，新生命周期保存脱敏、可校验的 replay capsule；
- `ask-ai-mcp-replay import-existing` 从历史作业导入可用样本；
- `ask-ai-mcp-replay shadow-qwen --lifecycle-id <UUID>` 让本机 Qwen 重放同一规格；
- 可选参数：`--jobs-root`、`--usage-database`、`--replay-root`。

DeepSeek 候选及其测试是参考实现，不是绝对正确的金标准；报告分别保存双方自测、交叉测试、
静态策略结果、模型身份和候选哈希。普通 usage/protocol audit 不保存完整 prompt 或输出。

## 7. 独立异构 Coding Review

Review 入口固定三个接口：`code_review_backend_status`、`code_review_submit`、
`code_review_status`。模型枚举固定为 GLM 5.3、Kimi K3 和 DeepSeek V4 Pro，均请求 `max`；接口
不接受自由 prompt、任意模型、Shell、网络地址、工作树写入、提交、推送或补丁生成参数。

Controller 只从仓库白名单生成不可变 diff 和最小上下文，排除密钥、二进制、vendor、生成物、
超大文件、submodule 与越界 reparse point；模型只看到仓库相对路径。finding 必须是严格 JSON，
落在实际改动 hunk，并由 Sol/人工盲审裁决。输入、输出和审计产物位于独立 job 目录；SQLite
只保留哈希、计量、finding 指纹、裁决和后续 outcome。详细配置与 A/B/C 流程见
`docs/CODE_REVIEW_ZH-CN.md`。

## 8. 独立 Coding Subagent

Coding 入口固定 `coding_backend_status`、`coding_submit`、`coding_status` 三个接口，仅允许
DeepSeek V4 Flash 与 GLM-5.3-Flash，均请求 `max`。它从白名单仓库的不可变 commit 冻结指定
文件，只在独立 job 目录生成候选 diff，不运行命令、不修改工作树、不应用或推送补丁。详见
`docs/CODING_SUBAGENT_ZH-CN.md`。

## 8. OpenCode Go 计量

OpenCode 价格快照固定版本，历史记录不随官网改价重算。账本按 `account_alias` 与
`subscription_id` 隔离记录实际模型、协议、输入/输出 token、缓存读写和虚拟美元，同时检查
共享滚动 5 小时、7 天、30 天窗口及模型月度额度。`effective_remaining` 取共享月余额与模型
余额较小值，跨模型共享消费只扣一次。无官方价格的分项为 `null/unsupported`，不按零价处理；
供应商未返回权威余额或实际成本时明确标记本地估算。不会自动切换账户规避额度；服务端 429
始终是最终权威。
