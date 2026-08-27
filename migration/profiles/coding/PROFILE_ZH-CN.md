# Ask AI MCP Coding 独立入口

该配置只注册三个按需 Coding 工具，默认关闭，不属于 Core 或 Full：

```text
<安装目录>\.venv\Scripts\ask-ai-mcp-coding.exe
```

部署时必须替换精确仓库白名单、API 可见账户 UID 与显示别名。API key 只能通过 Credential
Manager 的隐藏交互录入，不能写进模板。候选只生成到 MCP 独立状态目录，不直接修改仓库。
