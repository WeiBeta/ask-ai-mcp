# Qwen3.8 Subagent 接入前交接

更新日期：2026-08-15（Asia/Shanghai）
状态：本地 OpenAI Chat Completions 适配器、路由模式部署与真实联调已完成。

## 1. 当前停点

已按部署会话提供的运行参数实现真实适配器：

- Base URL：`http://127.0.0.1:38827/v1`；
- Chat endpoint：`/v1/chat/completions`；
- 固定 Model ID：`qwen3.8-27b-local`；
- OpenAI Chat Completions 兼容 JSON/HTTP，无真实 API key；
- 上下文 128K，视觉上限 16,384 image tokens；
- 连接超时 30 秒、文本读取 600 秒、视觉读取 5,400 秒；
- POST 不自动重试；超时后检查带固定 `model` 查询参数的 `/slots`，仍在处理时明确禁止重复提交；
- `/health`、`/slots` 和 `/v1/models` 用于健康与任务状态检查。

部署已切换为 llama.cpp router mode：HTTP 路由器保持在线，模型按请求自动加载；任务结束且
槽位空闲后调用 `/models/unload`，模型退出显存但路由器不退出。未配置来源 provider 时，
`UnconfiguredQwenBackend` 仍明确返回 `ready=false`，不会伪造结果。

2026-08-15 实机验收：路由器空载约占用 1.6 GiB 显存；最小文本请求 7.7 秒返回预期结果，
模型加载后总显存约 22.7 GiB；卸载完成后回到约 1.6 GiB，且 `/health` 持续返回正常。
真实多模态 opt-in 测试也已通过，卸载状态转换可能在接口确认后短暂异步完成。

### 1.1 当前工具与网络状态

截至 2026-08-15，部署侧确认：

- 服务仅监听 `127.0.0.1:38827`；
- 在线搜索未接入；
- Qwen 侧 MCP Server 未配置；
- llama.cpp 内置工具未启用；
- 未向模型授予任意文件、Shell 或其他工具权限；
- 模型可以生成 tool-call 格式，但当前没有执行器代它联网或调用工具。

这满足当前受控后端的安全基线。Qwen 只负责对 MCP 控制器明确提供的消息和 Base64
图片进行推理；白名单文件暂存、来源校验、Docker 隔离执行和输出落盘均由 Ask AI MCP
控制器承担。完成本项目的本地 toolsmith 与来源结构化联调，不需要给 Qwen 进程开放
通用联网、Shell 或文件系统权限。

如果未来确实需要自主在线检索，应新增独立的最小权限联网 profile 和受限搜索工具，
与当前离线 toolsmith/source profile 分离。不得把通用 Shell、任意文件访问或“模型生成
tool-call 即自动执行”作为 ready 条件。

官方模型卡：<https://huggingface.co/Qwen/Qwen3.8-27B>

## 2. 对外 MCP 面

`ask-ai-mcp-subagent.exe` 包含原 Core 十二工具，加：

- `source_backend_status`
- `source_extract`
- `source_job_status`

来源接口是异步的，不接受任意 prompt。固定 profile 为：

- `document_evidence`
- `visual_structure`

Core 仍为 toolsmith-only；H3 仍独立。兼容 Full 同时包含 Core、Source 和 H3。

## 3. 真实后端必须实现的 Python 协议

实现位于 `src/ask_ai_mcp/source.py` 的 `SourceBackend`：

```python
def status(self) -> SourceBackendStatus: ...


def extract(
    self,
    command: SourceExtractionCommand,
    staged_sources: list[StagedSource],
    output_directory: Path,
) -> SourceBackendResult: ...
```

后端只能读取已暂存副本，只能写入给定 output directory。不能读取原路径、用户目录、
仓库、Credential Manager 或任意不相关环境变量。

当前 `LocalQwenSourceBackend` 已实现 `document_evidence` 与 `visual_structure`，接受
PDF、PPTX、PNG/JPEG/WebP/BMP 图片和 UTF-8 TXT/MD/CSV/JSON。PDF 由锁定版本的
PDFium 渲染为确定性 PNG；PPTX 在拒绝宏、外部关系、包路径逃逸和异常展开体积后，通过
本机 PowerPoint 的固定只读 COM 脚本导出幻灯片。渲染清单记录原文件 SHA-256、页码/
幻灯片号、图像 SHA-256 和尺寸。

当前 llama.cpp/Qwen 运行时的真实两图请求只稳定处理最后一张图，因此适配器按每请求一张
视觉图像串行处理，在整个作业结束后才统一释放模型。单任务最多 32 页/幻灯片；超过限制
必须显式提供不超过 32 页的 `page_start`/`page_end`。图片仍放在文字指令之前。DOCX、
XLSX、视频、音频和时间范围不进入 0.6.2 公开 schema；未来完成独立预处理模块后再加入。

## 4. 固定输出契约

后端必须生成 `evidence.json`，并通过 `CanonicalEvidenceBundle`：

- `contract` 固定为 `canonical_evidence_v1`；
- `profile` 必须与请求一致；
- 每条 evidence 必须引用本任务输入 SHA-256；
- 必须提供页、幻灯片、工作表/单元格、对象、归一化区域或时间码之一；
- 必须包含逐字文本或结构化 data；
- 只做来源忠实结构化，不生成最终结论或交付正文。

控制器另行生成 `manifest.json`，记录模型身份、输入文件名、暂存名、哈希、大小、
媒体类型、输出哈希和警告。

## 5. 路径和资源边界

环境变量：

```text
ASK_AI_MCP_SOURCE_INPUT_ROOTS=<分号分隔的窄目录>
ASK_AI_MCP_SOURCE_JOBS_ROOT=<可选；默认 %LOCALAPPDATA%\AskAIMCP\source-jobs>
ASK_AI_MCP_SOURCE_PROVIDER=local_qwen
ASK_AI_MCP_QWEN_BASE_URL=http://127.0.0.1:38827/v1
ASK_AI_MCP_QWEN_MODEL_ID=qwen3.8-27b-local
ASK_AI_MCP_QWEN_CONNECT_TIMEOUT_SECONDS=30
ASK_AI_MCP_QWEN_TEXT_TIMEOUT_SECONDS=600
ASK_AI_MCP_QWEN_VISION_TIMEOUT_SECONDS=5400
ASK_AI_MCP_QWEN_UNLOAD_AFTER_TASK=1
```

限制：最多 20 个输入；单文件 8 GiB；合计 16 GiB；文档单任务最多 32 个视觉页；输出
最多 200 文件、500 MiB；只允许普通文件，拒绝逃逸白名单的解析路径和链接。GPU 队列
固定单并发。

## 6. 影子数据

设置以下变量后，新的 DeepSeek 工具制造生命周期会写入独立 replay capsule：

```text
ASK_AI_MCP_REPLAY_CAPTURE=1
ASK_AI_MCP_REPLAY_ROOT=%LOCALAPPDATA%\AskAIMCP\replay
```

普通 usage/protocol audit 仍然不保存 prompt、候选正文和来源正文。历史 review job 可用：

```powershell
python -m ask_ai_mcp.replay_cli import-existing
```

对一个已捕获生命周期执行本地 Qwen 影子重放：

```powershell
python -m ask_ai_mcp.replay_cli shadow-qwen --lifecycle-id <UUID>
```

影子报告分别记录双方的候选哈希、自带测试结果，以及 Qwen 实现运行 DeepSeek 参考测试的
交叉结果。参考候选测试不是独立金标准，报告固定标记
`reference_candidate_tests_are_not_an_independent_gold_oracle`，不得仅凭交叉测试通过就认定
模型等价。Qwen toolsmith 固定关闭隐藏推理并限制为 32K 输出；真实测试表明 `high + 65K`
可能在 Q4 模型上长时间不收敛。超时后若槽位仍运行，不会自动重提。

对一个已捕获生命周期执行本地 Qwen 影子重放：

```powershell
python -m ask_ai_mcp.replay_cli shadow-qwen --lifecycle-id <UUID>
```

影子报告分别记录双方的候选哈希、自带测试结果，以及 Qwen 实现运行 DeepSeek 参考测试的
交叉结果。参考候选测试不是独立金标准，报告固定标记
`reference_candidate_tests_are_not_an_independent_gold_oracle`，不得仅凭交叉测试通过就认定
模型等价。Qwen toolsmith 固定关闭隐藏推理并限制为 32K 输出；真实测试表明 `high + 65K`
可能在 Q4 模型上长时间不收敛。超时后若槽位仍运行，不会自动重提。

历史数据只能精确恢复最终成功候选；未落盘的早期静态拒绝正文会标记为缺失，不能伪造。

## 7. Provider 解耦

`ASK_AI_MCP_TOOLSMITH_PROVIDER` 支持配置值 `deepseek`、`opencode`、`local_qwen`。
DeepSeek 已验证；`local_qwen` 的协议适配与单元测试已完成、等待真实联调；`opencode`
仍会明确失败，直到对应运行时完成接入。只有 direct DeepSeek 继承人民币 budget gate，
订阅制和本地 provider 不继承。

`ASK_AI_MCP_QWEN_UNLOAD_AFTER_TASK=1` 只适用于支持路由模式的 llama.cpp server：任务结束、
且 `/slots?model=qwen3.8-27b-local` 无处理中任务时，适配器调用 `POST /models/unload` 并传入固定 Model ID。这样
HTTP 路由服务继续在线，但模型退出显存。单模型 server 若不提供该端点会返回安全警告，
不会终止服务或反复提交任务。

路由器会自动加载 GET 请求所指定的模型。因此适配器在查询 `/slots` 前先通过不会触发加载的
`/v1/models` 检查状态；若模型已经是 `unloaded`，就直接返回，不再查询槽位。运维验收卸载
状态时也只轮询 `/v1/models`，不要在卸载后再次调用带 `model` 的 `/slots`。

## 8. 真实接入验收顺序

1. [已完成] 恢复 router-mode 本地服务，只做健康检查与 Model ID 核对。
2. [已完成] 用一张合成图片验证 `visual_structure`、图片先于文字以及 provenance。
3. [已完成] 验证任务结束后的 `/models/unload`、服务仍在线和显存释放。
4. [已完成] 用两页合成 PDF 验证 PDFium 预处理、逐页请求、坐标归一化、页码和哈希 provenance。
5. 用短合成视频验证采帧、时间码和批处理边界。
6. 验证失败脱敏与超时后 `/slots` 防重提交。
7. 普通 pytest 不得启动模型；真实测试必须 opt-in。
8. 完成后再把 Codex/Claude 从 Core 切到显式 Subagent 入口。

首个真实 toolsmith 影子样本已完成：Qwen 静态策略通过，但自带测试和 DeepSeek 参考测试均
定位到同一个 `zipfile.ZipFile(bytes)`/缺少 `BytesIO` 的语义错误，修复响应随后因结构无效
终止。因此当前证据不足以替代 DeepSeek，但证明 replay、隔离执行、交叉测试、异常记录和
任务后显存释放链路均可用。

首个真实 toolsmith 影子样本已完成：Qwen 静态策略通过，但自带测试和 DeepSeek 参考测试均
定位到同一个 `zipfile.ZipFile(bytes)`/缺少 `BytesIO` 的语义错误，修复响应随后因结构无效
终止。因此当前证据不足以替代 DeepSeek，但证明 replay、隔离执行、交叉测试、异常记录和
任务后显存释放链路均可用。
