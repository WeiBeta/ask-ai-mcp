# 异构 Coding Review 配置

本配置只注册 3 个只读 review 工具，不属于 Core、Subagent、Perception、H3 或 Full。
它仅供重度开发时人工启用，日常开发保持关闭。

入口：

```text
<安装目录>\.venv\Scripts\ask-ai-mcp-review.exe
```

Codex 模板已设置 `enabled = false`。Claude JSON 文件只是离线注册示例，不应直接合并
到正在使用的 `mcpServers`；需要评审时再导入，并在 Claude Desktop 的 Extensions 设置中
显式启用，任务结束后关闭。这样可避免把 review 工具声明长期加入普通会话上下文。

必须把 `__REVIEW_REPOSITORIES_JSON__` 替换为小范围仓库 ID 到绝对根目录的 JSON 映射，
并明确填写 OpenCode Go 的账户别名与订阅标识。不得授权盘符根、整个用户目录，或自动轮转
多个账户。补丁输入根也应是专用小目录；不使用补丁模式时可留空。
