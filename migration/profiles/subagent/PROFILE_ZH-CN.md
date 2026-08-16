# Subagent 配置

本配置注册 15 个工具：Core 十二工具，加三项本地 Qwen 来源结构化工具；不注册 H3。
Qwen 服务、模型和业务文件均不包含在迁移包中。目标机必须单独部署 loopback Qwen，
并把 `__SOURCE_INPUT_ROOTS__` 替换为明确的窄输入目录。

入口：

```text
<安装目录>\.venv\Scripts\ask-ai-mcp-subagent.exe
```
