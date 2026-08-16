# 运维档案入口

这个目录解决 Windows 物理机上“代码、MCP 入口、本地模型、运行环境和桌面客户端配置
分别散落，重装时难以还原依赖链”的问题。

## 两类内容

- `MODULE_REFERENCE_ZH-CN.md`：可进入 Git 的通用模块、接口、参数和调用样例说明。
- `scripts/Update-LocalSnapshot.ps1`：可进入 Git 的本机快照生成器。
- `local-only/`：严格仅保留在当前物理机，整个目录已被仓库根 `.gitignore` 排除。

`local-only/` 生成后包含：

- `MACHINE_INVENTORY_ZH-CN.md`：本机 MCP、模型、运行时、路径和相关环境变量清单；
- `gui/codex-config.toml`：Codex Desktop 当前配置原件备份；
- `gui/claude-desktop-config.json`：Claude Desktop 当前 MCP 配置原件备份；
- `gui/BACKUP-MANIFEST.md`：配置源路径、大小、修改时间和 SHA-256。

## 更新本机快照

在仓库根目录使用 PowerShell 7：

```powershell
pwsh -NoProfile -File .\operator-archive\scripts\Update-LocalSnapshot.ps1
```

脚本会覆盖 `local-only` 中的旧快照，但不会修改任何客户端源配置、模型、运行时或系统设置。
Git 提交前仍应执行 `git status --ignored`，确认这些文件显示为 ignored。
