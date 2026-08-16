[CmdletBinding()]
param(
    [ValidateSet("core", "subagent", "h3", "full")]
    [string]$Profile,
    [string]$InstallRoot = (Join-Path $env:LOCALAPPDATA "Programs\AskAIMCP")
)

$ErrorActionPreference = "Stop"
$packageRoot = Split-Path -Parent $PSScriptRoot
$profileFile = Join-Path $packageRoot "profile.json"
$payloadRoot = Join-Path $packageRoot "payload"

if (-not (Test-Path -LiteralPath $profileFile -PathType Leaf)) {
    throw "profile.json is missing from the migration package"
}
$declaredProfile = (Get-Content -LiteralPath $profileFile -Raw | ConvertFrom-Json).profile
if ($Profile -ne $declaredProfile) {
    throw "Requested profile '$Profile' does not match package profile '$declaredProfile'"
}

$uv = Get-Command uv -ErrorAction SilentlyContinue | Select-Object -First 1
if ($null -eq $uv) {
    throw "System uv is required. Install Python 3.13 and uv, then run this script again."
}

$wheel = @(Get-ChildItem -LiteralPath $payloadRoot -Filter "*.whl" -File)
if ($wheel.Count -ne 1) {
    throw "The package must contain exactly one Ask AI MCP wheel"
}
$requirements = Join-Path $payloadRoot "requirements.lock.txt"
if (-not (Test-Path -LiteralPath $requirements -PathType Leaf)) {
    throw "requirements.lock.txt is missing"
}

$resolvedInstallRoot = [IO.Path]::GetFullPath($InstallRoot)
New-Item -ItemType Directory -Path $resolvedInstallRoot -Force | Out-Null
$venvRoot = Join-Path $resolvedInstallRoot ".venv"

& $uv.Source venv --python 3.13 $venvRoot
if ($LASTEXITCODE -ne 0) { throw "uv venv failed" }

$pythonPath = Join-Path $venvRoot "Scripts\python.exe"
& $uv.Source pip install --python $pythonPath --requirement $requirements
if ($LASTEXITCODE -ne 0) { throw "locked dependency installation failed" }
& $uv.Source pip install --python $pythonPath --no-deps $wheel[0].FullName
if ($LASTEXITCODE -ne 0) { throw "Ask AI MCP wheel installation failed" }

$entrypoint = Join-Path $venvRoot "Scripts\ask-ai-mcp-$Profile.exe"
if (-not (Test-Path -LiteralPath $entrypoint -PathType Leaf)) {
    throw "Expected profile entrypoint was not installed: $entrypoint"
}

$profileCounts = @{ core = 12; subagent = 15; h3 = 4; full = 19 }
$expectedCount = $profileCounts[$Profile]
$check = @"
import asyncio
from ask_ai_mcp.server import core_mcp, h3_mcp, mcp, subagent_mcp
servers = {'core': core_mcp, 'subagent': subagent_mcp, 'h3': h3_mcp, 'full': mcp}
server = servers['$Profile']
tools = asyncio.run(server.list_tools())
assert len(tools) == $expectedCount, [tool.name for tool in tools]
"@
& $pythonPath -c $check
if ($LASTEXITCODE -ne 0) { throw "Installed tool-surface verification failed" }

[ordered]@{
    status = "installed"
    profile = $Profile
    command = $entrypoint
    tool_count = $expectedCount
    credential_command = (Join-Path $venvRoot "Scripts\ask-ai-mcp-credentials.exe")
} | ConvertTo-Json -Depth 3
