[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..\..")).Path
$archiveRoot = Join-Path $repoRoot "operator-archive"
$localRoot = Join-Path $archiveRoot "local-only"
$guiRoot = Join-Path $localRoot "gui"

if (-not $localRoot.StartsWith($archiveRoot, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Resolved local archive escaped operator-archive."
}

New-Item -ItemType Directory -Path $guiRoot -Force | Out-Null

function Format-Cell {
    param([AllowNull()][object]$Value)
    if ($null -eq $Value) { return "" }
    return ([string]$Value).Replace("|", "\|").Replace("`r", " ").Replace("`n", " ")
}

function Add-TableRow {
    param(
        [System.Collections.Generic.List[string]]$Lines,
        [object[]]$Values
    )
    $cells = $Values | ForEach-Object { Format-Cell $_ }
    $Lines.Add("| " + ($cells -join " | ") + " |")
}

function Get-CommandPath {
    param([string]$Name)
    $command = Get-Command $Name -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -eq $command) { return "未找到" }
    return $command.Source
}

function Protect-EnvironmentValue {
    param([string]$Name, [AllowNull()][object]$Value)
    if ($Name -match "(?i)(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL)") {
        return "<redacted>"
    }
    return [string]$Value
}

function Get-SafeEnvironmentRows {
    $names = [System.Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    foreach ($scope in @("Machine", "User", "Process")) {
        $variables = [Environment]::GetEnvironmentVariables($scope)
        foreach ($name in $variables.Keys) {
            $text = [string]$name
            if ($text -like "ASK_AI_MCP_*" -or $text -like "CUDA*" -or $text -like "UV_*" -or
                $text -in @("PYTHONUTF8", "DOCKER_HOST", "WSLENV")) {
                [void]$names.Add($text)
            }
        }
    }
    foreach ($name in ($names | Sort-Object)) {
        foreach ($scope in @("Machine", "User", "Process")) {
            $value = [Environment]::GetEnvironmentVariable($name, $scope)
            if ($null -eq $value) { continue }
            $value = Protect-EnvironmentValue $name $value
            [pscustomobject]@{ Name = $name; Scope = $scope; Value = $value }
        }
    }
}

$claudeCandidates = @(
    (Join-Path $env:APPDATA "Claude\claude_desktop_config.json"),
    (Join-Path $env:LOCALAPPDATA "Packages\Claude_pzs8sxrjxfjjc\LocalCache\Roaming\Claude\claude_desktop_config.json")
)
$codexSource = Join-Path $env:USERPROFILE ".codex\config.toml"
$claudeSource = $claudeCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1

$backups = [System.Collections.Generic.List[object]]::new()
foreach ($item in @(
    [pscustomobject]@{ Client = "Codex Desktop"; Source = $codexSource; Target = (Join-Path $guiRoot "codex-config.toml") },
    [pscustomobject]@{ Client = "Claude Desktop"; Source = $claudeSource; Target = (Join-Path $guiRoot "claude-desktop-config.json") }
)) {
    if ([string]::IsNullOrWhiteSpace([string]$item.Source) -or
        -not (Test-Path -LiteralPath $item.Source -PathType Leaf)) {
        $backups.Add([pscustomobject]@{
            Client = $item.Client; Source = [string]$item.Source; Target = "未生成";
            Size = 0; Modified = ""; SHA256 = ""; Status = "源配置未找到"
        })
        continue
    }
    Copy-Item -LiteralPath $item.Source -Destination $item.Target -Force
    $sourceInfo = Get-Item -LiteralPath $item.Source
    $backups.Add([pscustomobject]@{
        Client = $item.Client; Source = $sourceInfo.FullName; Target = $item.Target;
        Size = $sourceInfo.Length; Modified = $sourceInfo.LastWriteTime.ToString("s");
        SHA256 = (Get-FileHash -LiteralPath $item.Target -Algorithm SHA256).Hash.ToLowerInvariant();
        Status = "已复制原件"
    })
}

$manifest = [System.Collections.Generic.List[string]]::new()
$manifest.Add("# GUI 配置备份清单")
$manifest.Add("")
$manifest.Add("> 仅限当前物理机。不得加入 Git、迁移包或对外 handoff。")
$manifest.Add("")
$manifest.Add("生成时间：$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss zzz')")
$manifest.Add("")
$manifest.Add("| 客户端 | 源配置 | 本地备份 | 字节 | 源修改时间 | SHA-256 | 状态 |")
$manifest.Add("|---|---|---|---:|---|---|---|")
foreach ($backup in $backups) {
    Add-TableRow $manifest @($backup.Client, $backup.Source, $backup.Target, $backup.Size,
        $backup.Modified, $backup.SHA256, $backup.Status)
}
$manifest.Add("")
$manifest.Add("恢复前先关闭对应 GUI，并再次核对路径和版本；本脚本只备份，不执行恢复。")
$manifest | Set-Content -LiteralPath (Join-Path $guiRoot "BACKUP-MANIFEST.md") -Encoding utf8

$lines = [System.Collections.Generic.List[string]]::new()
$lines.Add("# 本机 Ask AI MCP 安装与运行环境清单")
$lines.Add("")
$lines.Add("> 严格仅限当前物理机。本文档可能包含用户名、绝对路径和环境变量值，不得加入 Git、迁移包或对外 handoff。")
$lines.Add("")
$lines.Add("生成时间：$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss zzz')")
$lines.Add("")

$lines.Add("## 1. 物理机与仓库")
$lines.Add("")
$osCaption = [Environment]::OSVersion.VersionString
$osBuild = [Environment]::OSVersion.Version.Build
$memory = "当前权限下未读取"
try {
    $osInfo = Get-CimInstance Win32_OperatingSystem -ErrorAction Stop
    $computer = Get-CimInstance Win32_ComputerSystem -ErrorAction Stop
    $osCaption = $osInfo.Caption + " " + $osInfo.Version
    $osBuild = $osInfo.BuildNumber
    $memory = "{0:N1} GiB" -f ($computer.TotalPhysicalMemory / 1GB)
} catch {
    try {
        $windows = Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion"
        $osCaption = $windows.ProductName + " " + $windows.DisplayVersion
        $osBuild = $windows.CurrentBuildNumber
    } catch {
        # Environment.OSVersion above remains a safe non-admin fallback.
    }
}
$gpu = try { (& nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader 2>&1) -join "; " } catch { "nvidia-smi 不可用" }
$commit = try { (& git -C $repoRoot rev-parse HEAD 2>$null) -join "" } catch { "未知" }
$branch = try { (& git -C $repoRoot branch --show-current 2>$null) -join "" } catch { "未知" }
$lines.Add("| 项目 | 值 |")
$lines.Add("|---|---|")
Add-TableRow $lines @("计算机名", $env:COMPUTERNAME)
Add-TableRow $lines @("Windows", $osCaption + " build " + $osBuild)
Add-TableRow $lines @("内存", $memory)
Add-TableRow $lines @("GPU", $gpu)
Add-TableRow $lines @("仓库", $repoRoot)
Add-TableRow $lines @("Git 分支", $branch)
Add-TableRow $lines @("Git 提交", $commit)
Add-TableRow $lines @("uv.lock SHA-256", (Get-FileHash (Join-Path $repoRoot "uv.lock") -Algorithm SHA256).Hash.ToLowerInvariant())
$lines.Add("")

$lines.Add("## 2. 已安装 MCP 入口")
$lines.Add("")
$lines.Add("| 入口 | 完整路径 | 字节 | 修改时间 |")
$lines.Add("|---|---|---:|---|")
$entrypoints = Get-ChildItem (Join-Path $repoRoot ".venv\Scripts") -Filter "ask-ai-mcp*.exe" -File -ErrorAction SilentlyContinue | Sort-Object Name
if ($entrypoints.Count -eq 0) {
    $lines.Add("| 未找到 | | 0 | |")
} else {
    foreach ($entry in $entrypoints) {
        Add-TableRow $lines @($entry.Name, $entry.FullName, $entry.Length, $entry.LastWriteTime.ToString("s"))
    }
}
$lines.Add("")
$lines.Add('说明：新代码中存在但本表缺少的 `.exe`，表示 `uv sync` 尚未在 GUI 释放文件占用后完成。')
$lines.Add("")

$lines.Add("## 3. GUI MCP 注册概览")
$lines.Add("")
$codexServers = @()
if (Test-Path -LiteralPath $codexSource) {
    $codexServers = Select-String -LiteralPath $codexSource -Pattern '^\[mcp_servers\.([^\]]+)\]' |
        ForEach-Object { $_.Matches[0].Groups[1].Value } |
        Where-Object { $_ -notmatch '\.' }
}
$claudeServers = @()
if (-not [string]::IsNullOrWhiteSpace([string]$claudeSource)) {
    try {
        $claudeJson = Get-Content -LiteralPath $claudeSource -Raw | ConvertFrom-Json
        if ($null -ne $claudeJson.mcpServers) {
            $claudeServers = $claudeJson.mcpServers.PSObject.Properties.Name
        }
    } catch {
        $claudeServers = @("<配置 JSON 无法解析>")
    }
}
$lines.Add("| 客户端 | 配置源 | 已发现 MCP 名称 |")
$lines.Add("|---|---|---|")
Add-TableRow $lines @("Codex Desktop", $codexSource, ($codexServers -join ", "))
Add-TableRow $lines @("Claude Desktop", $claudeSource, ($claudeServers -join ", "))
$lines.Add("")

$lines.Add("## 4. 本地模型与运行目录")
$lines.Add("")
$knownRoots = @(
    "C:\AI\Llama",
    "C:\AI\ComfyUI"
)
$lines.Add("| 分类 | 路径 | 状态 |")
$lines.Add("|---|---|---|")
Add-TableRow $lines @("Qwen3.8 27B 本地 agent", $knownRoots[0], $(if (Test-Path $knownRoots[0]) { "已安装" } else { "未安装" }))
Add-TableRow $lines @("MiniMax H3 / ComfyUI", $knownRoots[1], $(if (Test-Path $knownRoots[1]) { "已安装" } else { "未安装" }))
$acePaths = Get-ChildItem "C:\AI", (Join-Path $env:USERPROFILE "Documents\Codex\local-ai") -Directory -ErrorAction SilentlyContinue | Where-Object Name -Match "(?i)ace"
$acePathText = @($acePaths | ForEach-Object { $_.FullName }) -join "; "
Add-TableRow $lines @("ACE 1.5", $acePathText, $(if ($acePaths) { "发现候选目录，需人工确认" } else { "未发现/尚未部署" }))
$lines.Add("")
$lines.Add("### 大型模型文件")
$lines.Add("")
$lines.Add("| 文件 | GiB | 修改时间 |")
$lines.Add("|---|---:|---|")
foreach ($root in $knownRoots) {
    if (-not (Test-Path -LiteralPath $root)) { continue }
    try {
        Get-ChildItem -LiteralPath $root -Recurse -File -ErrorAction Stop |
            Where-Object { $_.Length -ge 100MB -and $_.Extension -match '^\.(gguf|safetensors|pt|pth|ckpt|bin)$' } |
            Sort-Object FullName | ForEach-Object {
                Add-TableRow $lines @($_.FullName, ("{0:N3}" -f ($_.Length / 1GB)), $_.LastWriteTime.ToString("s"))
            }
    } catch {
        Add-TableRow $lines @($root, "", "扫描失败：$($_.Exception.GetType().Name)")
    }
}
$lines.Add("")
$lines.Add("模型文件默认不计算整文件哈希，以免每次快照读取数十 GiB；发布/验收哈希仍以各模块专用文档为准。")
$lines.Add("")

$lines.Add("## 5. 运行环境与依赖入口")
$lines.Add("")
$lines.Add("| 运行时 | 命令解析路径 |")
$lines.Add("|---|---|")
foreach ($runtime in @("pwsh", "python", "uv", "git", "docker", "wsl", "nvidia-smi")) {
    Add-TableRow $lines @($runtime, (Get-CommandPath $runtime))
}
Add-TableRow $lines @("项目 Python", (Join-Path $repoRoot ".venv\Scripts\python.exe"))
Add-TableRow $lines @("依赖声明", (Join-Path $repoRoot "pyproject.toml"))
Add-TableRow $lines @("依赖锁", (Join-Path $repoRoot "uv.lock"))
Add-TableRow $lines @("H3 启动器", (Join-Path $repoRoot "scripts\start_comfyui_h3.ps1"))
$lines.Add("")

$lines.Add("## 6. MCP/模型相关环境变量")
$lines.Add("")
$lines.Add('仅记录相关变量；名称含 KEY/TOKEN/SECRET/PASSWORD/CREDENTIAL 的值自动写为 `<redacted>`。')
$lines.Add("")
$lines.Add("| 名称 | 作用域 | 值 |")
$lines.Add("|---|---|---|")
$environmentRows = @(Get-SafeEnvironmentRows)
if ($environmentRows.Count -eq 0) {
    $lines.Add("| 未发现 | | |")
} else {
    foreach ($row in $environmentRows) {
        Add-TableRow $lines @($row.Name, $row.Scope, $row.Value)
    }
}
$lines.Add("")
$lines.Add("### GUI MCP 配置内的环境变量")
$lines.Add("")
$lines.Add("| 客户端 | MCP | 名称 | 值 |")
$lines.Add("|---|---|---|---|")
$guiEnvironmentRows = [System.Collections.Generic.List[object]]::new()
if (Test-Path -LiteralPath $codexSource) {
    $currentServer = $null
    foreach ($line in Get-Content -LiteralPath $codexSource) {
        if ($line -match '^\[mcp_servers\.([^\.\]]+)\.env\]$') {
            $currentServer = $Matches[1]
            continue
        }
        if ($line -match '^\[') {
            $currentServer = $null
            continue
        }
        if ($null -ne $currentServer -and $line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*"(.*)"\s*$') {
            $guiEnvironmentRows.Add([pscustomobject]@{
                Client = "Codex Desktop"; Server = $currentServer; Name = $Matches[1];
                Value = (Protect-EnvironmentValue $Matches[1] $Matches[2])
            })
        }
    }
}
if (-not [string]::IsNullOrWhiteSpace([string]$claudeSource)) {
    try {
        $claudeJson = Get-Content -LiteralPath $claudeSource -Raw | ConvertFrom-Json
        foreach ($serverProperty in $claudeJson.mcpServers.PSObject.Properties) {
            if ($null -eq $serverProperty.Value.env) { continue }
            foreach ($envProperty in $serverProperty.Value.env.PSObject.Properties) {
                $guiEnvironmentRows.Add([pscustomobject]@{
                    Client = "Claude Desktop"; Server = $serverProperty.Name;
                    Name = $envProperty.Name;
                    Value = (Protect-EnvironmentValue $envProperty.Name $envProperty.Value)
                })
            }
        }
    } catch {
        # The backup manifest already records the source; malformed JSON is not copied into prose.
    }
}
if ($guiEnvironmentRows.Count -eq 0) {
    $lines.Add("| 未发现 | | | |")
} else {
    foreach ($row in $guiEnvironmentRows) {
        Add-TableRow $lines @($row.Client, $row.Server, $row.Name, $row.Value)
    }
}
$lines.Add("")

$lines.Add("## 7. GUI 原件备份")
$lines.Add("")
$lines.Add('详见 `gui/BACKUP-MANIFEST.md`。备份为生成时刻的原件副本；脚本不会自动恢复或修改源配置。')
$lines.Add("")
$lines.Add("## 8. 人工复核清单")
$lines.Add("")
$lines.Add("- GUI 更新或 MCP 开关变化后重新运行本脚本。")
$lines.Add("- 模型移动、换量化、换驱动、换 Python/uv/Docker 后重新运行本脚本。")
$lines.Add('- 确认 `operator-archive/local-only/` 始终处于 Git ignored 状态。')
$lines.Add("- 重装前另行备份 Windows Credential Manager；本目录不导出 API key。")

$inventoryPath = Join-Path $localRoot "MACHINE_INVENTORY_ZH-CN.md"
$lines | Set-Content -LiteralPath $inventoryPath -Encoding utf8

Write-Host "Local-only operator snapshot updated: $localRoot"
Write-Host "Inventory: $inventoryPath"
