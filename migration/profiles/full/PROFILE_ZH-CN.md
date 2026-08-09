# Full 配置

本配置注册全部 16 个 Ask AI MCP 工具，其中 4 个为外部 H3/ComfyUI 适配工具。
迁移包不包含也不安装 ComfyUI、模型、媒体文件或 H3 工作区。

在桌面客户端中应直接调用：

```text
<安装目录>\.venv\Scripts\ask-ai-mcp-full.exe
```

目标机只有在已经具备独立 ComfyUI 服务时才应配置 `ASK_AI_MCP_H3_*` 路径。
若希望四个 H3 工具在服务未启动时自动拉起它，还必须将
`ASK_AI_MCP_H3_START_SCRIPT` 配置为目标机上经过审阅的绝对 `.ps1` 路径。
