# MiniMax H3 本地部署与 MCP 开发交接

更新日期：2026-08-08（Asia/Shanghai）
适用版本：Ask AI MCP 0.6.0
目标主机：Windows 11、NVIDIA GeForce RTX 3090 24GB

本文是当前工作站 H3 部署与 MCP 接口的交接基线。后续开发会话应先阅读
仓库根目录的 `AGENTS.md`，再阅读本文；不要在未核对现状前重新安装
ComfyUI、替换模型、改变共享目录或降级主机组件。

## 1. 已确定的产品流程

固定流程如下：

1. `h3_generate_video` 只生成 480p、24fps 的低成本原片。
2. 人工查看原片并淘汰废片。
3. 仅对人工选中的原片调用 `h3_postprocess_video`。
4. GPT/Codex 或 Claude 必须显式指定画风组、插帧模型、超分模型、目标帧率和目标短边。
5. ComfyUI 只执行确定性工作流，不判断画风，也不自动选择模型。

这条边界用于节约 3090 的算力和等待时间，也避免把内容判断隐藏到 ComfyUI
工作流中。原片不会被后处理覆盖。

## 2. 目录与进程边界

| 用途 | 路径 |
|---|---|
| MCP 仓库 | `C:\Dev\ask-ai-mcp` |
| ComfyUI 程序、Python 与模型 | `C:\AI\ComfyUI-H3` |
| ComfyUI 日志 | `C:\AI\ComfyUI-H3\logs` |
| GPT/Claude 共用任务目录 | `C:\Users\user\Documents\AskAI-Exchange\H3-Workspace` |
| 可放参考帧及自动暂存视频 | `...\H3-Workspace\inputs` |
| H3 原片与后处理结果 | `...\H3-Workspace\outputs` |
| 后处理成片 | `...\H3-Workspace\outputs\postprocessed` |

程序和模型均在仓库外。仓库只保存适配器、严格数据模型、测试、启动脚本和文档。
Codex 与 Claude Desktop 只共享 `H3-Workspace`，不共享整个 C 盘、用户目录或
ComfyUI 安装目录。

ComfyUI 仅监听 `127.0.0.1:8188`。启动脚本：

```powershell
pwsh -NoProfile -File C:\Dev\ask-ai-mcp\scripts\start_comfyui_h3.ps1
```

当前脚本固定使用共享 `inputs`、`outputs`、`--reserve-vram 2`、无预览模式，
并把进程隐藏启动。重复运行会识别现有 8188 实例，不会再起一个副本。

常用只读检查：

```powershell
Invoke-RestMethod http://127.0.0.1:8188/system_stats
Invoke-RestMethod http://127.0.0.1:8188/queue
Get-Content C:\AI\ComfyUI-H3\logs\comfyui.stderr.log -Tail 80
```

已验证的运行栈：ComfyUI portable NVIDIA 0.31.0、前端 1.48.7、嵌入式
Python 3.13.14、PyTorch 2.13.0+cu130。运行时识别 RTX 3090，界面已切换为
简体中文。没有安装第三方 custom nodes；H3、插帧和 SeedVR2 均使用当前
ComfyUI 原生节点。

## 3. 已部署权重与校验值

下表路径均相对于 `C:\AI\ComfyUI-H3\ComfyUI\models`。

| 分组 | 相对路径 | SHA-256 |
|---|---|---|
| H3 UNet | `diffusion_models\minimax_h3_fl2va_pruned_int8_convrot.safetensors` | `e889202c41dafb67b10d67b97f0d8541508036a6090af23425a5c2615d03c47a` |
| H3 文本编码器 | `text_encoders\qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors` | `35a88d51044231fe332301d7a62aa81e3f2cba62febeb446e2c1e3e0ef76f2c6` |
| H3 音频 VAE | `vae\minimax_h3_audio_vae_fp32.safetensors` | `8e505d95dd1561d47abd43d4238fd40d9bb1ae9e147ed0a4cba778d76ae4db48` |
| H3 视频 VAE | `vae\minimax_h3_video_vae_fp16.safetensors` | `7c1f131492e7eddacaac9069a61b81bdd39de5cc96561e677c5eab1cdce5e522` |
| 动画插帧 | `frame_interpolation\rife_v4.26.safetensors` | `151874592c877740e5db11522f4514df569eeafb0a0fcb2696f16e9e8d317c94` |
| 写实插帧 | `frame_interpolation\film_net_fp16.safetensors` | `f226e51375dc839d4b40e5c3d63da560dd1ea1c962364ec78f5adf2d05db05c0` |
| 动画超分 | `upscale_models\realesr-animevideov3.pth` | `b8a8376811077954d82ca3fcf476f1ac3da3e8a68a4f4d71363008000a18b75d` |
| 写实超分 | `diffusion_models\seedvr2_3b_int8_convrot.safetensors` | `c3dec8bcc5916843a8a858572970597462e1f2dc598d6dfd818f6cd40f53a157` |
| SeedVR2 VAE | `vae\seedvr2_ema_vae_fp16.safetensors` | `20678548f420d98d26f11442d3528f8b8c94e57ee046ef93dbb7633da8612ca1` |

模型选择基于当前官方来源重新核对，不采用早期 Gemini 选型稿作为事实来源：

- MiniMax H3：<https://huggingface.co/MiniMaxAI/MiniMax-H3>
- ComfyUI H3 重打包：<https://huggingface.co/Comfy-Org/MiniMax-H3>
- ComfyUI RIFE/FILM：<https://huggingface.co/Comfy-Org/frame_interpolation>
- Real-ESRGAN 动画视频模型：<https://github.com/xinntao/Real-ESRGAN/blob/master/docs/anime_video_model.md>
- ComfyUI SeedVR2：<https://huggingface.co/Comfy-Org/SeedVR2>
- ComfyUI SeedVR2 官方工作流：<https://github.com/Comfy-Org/workflow_templates/blob/main/templates/utility_seedvr2_3b_int8_upscale_video.json>

H3 受其 Community License Agreement 约束；Real-ESRGAN 仓库为 BSD-3-Clause，
SeedVR2 ComfyUI 重打包标注 Apache-2.0，帧插值仓库包含 MIT/Apache-2.0 元数据。
对外分发或商业使用前仍应逐个复查上游权重许可和 H3 的地域/用途限制。

## 4. MCP 工具面与严格参数

Ask AI MCP 当前共 16 个工具，其中 H3 工具为：

- `h3_backend_status`：检查 ComfyUI、GPU、4 个 H3 权重和 5 个后处理权重。
- `h3_generate_video`：提交 4–15 秒的 H3 原片生成任务。
- `h3_postprocess_video`：处理人工选中的原片。
- `h3_job_status`：轮询生成或后处理任务并返回本地路径与 loopback 查看地址；任务进入
  成功或失败终态且 ComfyUI 全局队列空闲后，调用本机 `/free` 卸载模型并释放显存。

基础生成只允许以下预设：

| 枚举值 | 输出 |
|---|---|
| `landscape_480p` | 864×480、24fps |
| `portrait_480p` | 480×864、24fps |
| `square_480p` | 480×480、24fps |

后处理参数约束：

- `visual_profile`：`anime` 或 `realistic`。
- `interpolation`：`none`、`rife_4_26`、`film`。
- `upscale`：`none`、`anime_video`、`seedvr2_3b`。
- `target_fps`：仅 24、48、72；48/72 必须启用插帧。
- `target_short_side`：仅 480、720、1080；720/1080 必须启用超分。
- 动画组只允许 RIFE 与 AnimeVideo；写实组允许 RIFE/FILM 与 SeedVR2。
- 插帧和超分不能同时都为 `none`。

当前没有 60fps 选项，因为原片为 24fps，原生插帧节点使用整数倍。横屏 864×480
保持原始宽高比后，720 短边对应 1296×720，不是 1280×720。

后处理源视频必须位于解析后的共享工作区内。适配器按 SHA-256 将其复制为
`inputs\h3-selected-<hash16>.<ext>`，同一内容会复用暂存文件；源文件最大 2GiB。
输出固定写入 `outputs\postprocessed`，调用方不能提供任意输出目录。

实现入口：

- `src\ask_ai_mcp\h3.py`
- `src\ask_ai_mcp\models.py`
- `src\ask_ai_mcp\server.py`
- `tests\test_h3.py`
- `tests\test_server.py`

## 5. 桌面客户端配置

两个客户端均调用：

```text
C:\Dev\ask-ai-mcp\.venv\Scripts\ask-ai-mcp.exe
```

当前有效配置文件：

- Codex Desktop：`C:\Users\user\.codex\config.toml`
- Claude Desktop（MSIX）：`C:\Users\user\AppData\Local\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Roaming\Claude\claude_desktop_config.json`

两份配置中的以下环境变量已对齐：

```text
ASK_AI_MCP_H3_URL=http://127.0.0.1:8188
ASK_AI_MCP_H3_WORKSPACE_ROOT=C:\Users\user\Documents\AskAI-Exchange\H3-Workspace
ASK_AI_MCP_H3_COMFY_INPUT_ROOT=C:\Users\user\Documents\AskAI-Exchange\H3-Workspace\inputs
ASK_AI_MCP_H3_OUTPUT_ROOT=C:\Users\user\Documents\AskAI-Exchange\H3-Workspace\outputs
ASK_AI_MCP_H3_INPUT_ROOTS=C:\Users\user\Documents\AskAI-Exchange\H3-Workspace\inputs
ASK_AI_MCP_H3_AUTO_FREE_VRAM=1
```

`ASK_AI_MCP_H3_AUTO_FREE_VRAM` 默认启用，不写入配置也会生效。设为 `0`、`false`、
`no` 或 `off` 可显式禁用。释放操作不会结束 ComfyUI 进程，只发送
`{"unload_models": true, "free_memory": true}`；若仍有运行或排队任务则延后，最后一个
终态任务再次轮询时会完成释放。

Codex 的 `[sandbox_workspace_write]` 另含：

```toml
writable_roots = ["C:\\Users\\user\\Documents\\AskAI-Exchange\\H3-Workspace"]
```

配置内容已经分别用 `tomllib` 和 `json` 解析验证。2026-08-08 收尾时修复过 Codex
配置中一个重复回车字节，备份为
`C:\Users\user\.codex\config.toml.20260808-211600.bak`。不要在文档或日志中写入
API 密钥。配置变更后需完全重启对应桌面客户端以刷新 stdio MCP 工具面。

## 6. 真实端到端证据

以下调用均经过本地 ComfyUI，而不是伪造 MCP 返回值。

### H3 原片

- 直接适配器 smoke：prompt `f1263a77-b626-4986-8e46-3da2ee8d6c07`。
- 真实 MCP stdio：prompt `bf34cf8b-660f-4f2a-a184-62d539b2555a`。
- 样片目录：`H3-Workspace\outputs\smoke-tests`。
- 已检查媒体规格：864×480、24fps、107 帧、约 4.46 秒、H.264/AAC、32kHz 双声道。

### 动画组组合测试

- prompt：`62457d9e-f4d0-4ea7-9175-c0f94e4d4d6a`
- 组合：RIFE 4.26 + AnimeVideo，目标 720 短边、48fps。
- 输出：`outputs\postprocessed\H3_anime_720p_48fps_00001_.mp4`
- 实测：1296×720、48fps、213 帧、约 4.44 秒、32kHz 双声道。
- 3090 上该任务约 56 秒。

### 写实组 SeedVR2 测试

- prompt：`a3265b3d-4741-4bf2-b884-880828332db1`
- 组合：SeedVR2 3B，保持 24fps，目标 720 短边。
- 输出：`outputs\postprocessed\H3_realistic_720p_24fps_00001_.mp4`
- 实测：1296×720、24fps、107 帧、约 4.46 秒、32kHz 双声道。
- 3090 上该任务约 183 秒。

### 写实组 FILM 测试

- prompt：`f0225d0e-7c28-40e2-aea5-1b621afb0880`
- 组合：FILM，保持 480 短边，目标 48fps。
- 输出：`outputs\postprocessed\H3_realistic_480p_48fps_00001_.mp4`
- 实测：864×480、48fps、213 帧、约 4.44 秒、32kHz 双声道。
- 3090 上该任务约 26 秒。

上述结果证明两个画风组的四个核心后处理模型均可由真实 MCP stdio 链路调用。
动画组合已经合并实测；写实 FILM 与 SeedVR2 分别实测，但尚未对二者组合做一次完整
真机任务。

## 7. 代码验证状态

最后一次完整验证：

```text
uv lock
uv sync --all-groups
uv run ruff check .       -> All checks passed
uv run pytest             -> 136 passed, 3 skipped
fastmcp inspect           -> Ask AI MCP 0.6.0，共 16 个工具
```

普通 pytest 不运行外部 API 集成测试，符合仓库规则。当前 H3 相关修改仍在工作树中，
尚未由用户要求提交；接手时先运行 `git status --short` 和 `git diff --check`，保留
所有无关用户改动，不要使用 `git reset --hard` 或 `git checkout --` 清理。

## 8. 已知边界与下一步建议

已经验证：

- 480p/24fps H3 短原片生成。
- 动画 RIFE + AnimeVideo 的 720p/48fps 组合。
- 写实 SeedVR2 的 720p/24fps 超分。
- 写实 FILM 的 480p/48fps 插帧。
- 音轨保留、共享目录自动暂存、原片不覆盖、双客户端路径配置。

尚未做完整真机压测：

- 15 秒最大长度原片。
- 72fps、1080 短边。
- FILM + SeedVR2 写实组合。
- 竖屏与方形的所有后处理组合。
- 长视频磁盘占用、取消任务、失败恢复和并发队列策略。

建议专门的 MCP 优化会话按以下顺序继续：

1. 先调用 `h3_backend_status`，确认 9 个权重仍为 ready。
2. 读取 `git diff` 和现有测试，不要重建当前已通过的基础工作流。
3. 改善 `h3_job_status` 的失败详情、进度和媒体元数据，但保持 stdout 协议干净。
4. 为真实 ComfyUI 测试增加显式 opt-in 标记，普通单元测试不得启动重任务。
5. 设计任务取消、超时、磁盘配额和产物清理策略；删除文件前必须单独确认范围。
6. 对 72fps、1080 和写实组合进行小样压测，再决定是否向调用模型推荐这些档位。
7. 若要支持 60fps，需新增明确的重采样策略和测试，不能把 2.5 倍传给只接受整数
   multiplier 的插帧节点。

下一会话的推荐开场指令：

> 请先完整阅读 `C:\Dev\ask-ai-mcp\AGENTS.md` 和
> `C:\Dev\ask-ai-mcp\docs\H3_HANDOFF_ZH-CN.md`，以当前部署和测试证据为基线
> 继续优化 MCP；不要重新安装、替换或降级现有 ComfyUI、模型和主机组件。
