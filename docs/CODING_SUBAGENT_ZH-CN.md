# 独立 Coding Subagent

## 产品边界

`ask-ai-mcp-coding.exe` 是默认关闭、按需启用的代码候选制造模块。它固定暴露：

- `coding_backend_status`：查询账户、模型、仓库白名单和共享 Go 账本；
- `coding_submit`：冻结精确 commit 上的指定文件并异步提交候选任务；
- `coding_status`：读取任务状态和分页候选 diff。

模型只允许 `deepseek-v4-flash` 与 `glm-5.3-flash`，均请求
`reasoning_effort=max` 与 131,072 token 总生成上限。实际策略由 backend status、job manifest
和共享 usage 账本共同记录，后续按模型日志收敛。服务端不接受 generic prompt、任意模型、任意 URL、Shell、测试命令、
工作树写入、应用补丁、commit、push、重试次数或切换账户参数。

## 快照和候选

`ASK_AI_MCP_CODING_REPOSITORIES` 是仓库 ID 到精确 Git 根目录的 JSON 映射。提交时必须给出
不可变 `base_ref`、1–12 个目标文件、可选上下文文件、任务种类、约束和验收测试。Controller
从 commit 对象读取源码，不读取未提交工作树；拒绝越界路径、submodule、reparse point、密钥、
二进制、vendor、生成物和超限上下文。
Codex/Claude 的具体白名单配置片段、转义规则和重启验收见
[简中使用与运维说明书](USER_MANUAL_ZH-CN.md#46-codingreview-仓库白名单配置)。

模型只能返回指定目标文件的完整替换内容及原始 SHA-256。Controller 重新校验文件集合与哈希，
在专用 job 目录生成统一 diff。候选永不写入源仓库；Sol 负责审查、应用、测试和最终交付。

## 账户与账本

每个合法 Go 订阅配置一个非秘密 API 可见 UID 与一个显示别名：

```text
ASK_AI_MCP_OPENCODE_ACCOUNT_UID=<API 可见 UID>
ASK_AI_MCP_OPENCODE_ACCOUNT_ALIAS=<Go 账户名>
```

人工规则是一账号只登记一个 API key。Credential Manager 以 UID 定位密钥；Coding 与 Review
以同一 UID 聚合共享 5 小时、周、月窗口，避免重复额度。切换账号由管理员显式修改配置并重启
客户端，不提供 MCP 切号工具，也不会自动轮转或绕过供应商限制。

凭据默认通过隐藏交互写入；内嵌终端无法粘贴时，在用户自行打开的 PowerShell 7 中采用显式
可见输入兼容模式：

```powershell
ask-ai-mcp-credentials set --provider opencode-go --account-uid <UID>
ask-ai-mcp-credentials set --provider opencode-go --account-uid <UID> --visible-input
```

## 验收顺序

1. 普通单元测试只使用注入的假后端，不产生费用。
2. 凭据和订阅开通后，先查询后端状态并核对 UID、别名、模型与额度。
3. 对两个 Coding 模型提交同一合成 C# 小任务，确认 `max` 被服务端接受。
4. 对照 OpenCode 控制台核对 token、缓存和虚拟美元。
5. 实际仓库任务仍由 Sol 独立验证；候选成功不等于可直接合并。
6. DeepSeek 模型需先在对应 Go 工作区完成中国托管 opt-in；未完成时区域门禁返回 403。
