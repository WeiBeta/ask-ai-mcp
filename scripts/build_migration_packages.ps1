[CmdletBinding()]
param(
    [string]$OutputRoot,
    [switch]$AllowDirty
)

$ErrorActionPreference = "Stop"
$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
if ([string]::IsNullOrWhiteSpace($OutputRoot)) {
    $OutputRoot = Join-Path $repoRoot "artifacts\migration"
}
$resolvedOutputRoot = [IO.Path]::GetFullPath($OutputRoot)

$uv = Get-Command uv -ErrorAction SilentlyContinue | Select-Object -First 1
if ($null -eq $uv) { throw "System uv is required to build migration packages" }
$git = Get-Command git -ErrorAction SilentlyContinue | Select-Object -First 1
if ($null -eq $git) { throw "Git is required to record build provenance" }

Push-Location $repoRoot
try {
    & $git.Source diff --check
    if ($LASTEXITCODE -ne 0) { throw "git diff --check failed" }
    $dirtyLines = @(& $git.Source status --porcelain)
    $isDirty = $dirtyLines.Count -gt 0
    if ($isDirty -and -not $AllowDirty) {
        throw "Working tree is dirty. Commit the release state or pass -AllowDirty for a test build."
    }
    $commit = (& $git.Source rev-parse HEAD).Trim()
    if ($LASTEXITCODE -ne 0) { throw "Unable to read Git commit" }

    $pyproject = Get-Content -LiteralPath (Join-Path $repoRoot "pyproject.toml") -Raw
    $versionMatch = [regex]::Match($pyproject, '(?m)^version\s*=\s*"([^"]+)"')
    if (-not $versionMatch.Success) { throw "Unable to read project version" }
    $version = $versionMatch.Groups[1].Value

    $buildId = "{0}-{1}" -f (Get-Date -Format "yyyyMMdd-HHmmss"), $commit.Substring(0, 12)
    $releaseRoot = Join-Path $resolvedOutputRoot $buildId
    New-Item -ItemType Directory -Path $releaseRoot -Force | Out-Null

    $temporaryRoot = Join-Path ([IO.Path]::GetTempPath()) ("ask-ai-migration-" + [guid]::NewGuid())
    New-Item -ItemType Directory -Path $temporaryRoot | Out-Null
    try {
        $wheelRoot = Join-Path $temporaryRoot "wheel"
        New-Item -ItemType Directory -Path $wheelRoot | Out-Null
        $buildSource = Join-Path $temporaryRoot "source"
        New-Item -ItemType Directory -Path $buildSource | Out-Null
        Copy-Item -LiteralPath (Join-Path $repoRoot "pyproject.toml") -Destination $buildSource
        Copy-Item -LiteralPath (Join-Path $repoRoot "src") -Destination $buildSource -Recurse
        Copy-Item -LiteralPath (Join-Path $repoRoot "migration\common\PACKAGE_README.md") `
            -Destination (Join-Path $buildSource "README.md")
        Push-Location $buildSource
        try {
            & $uv.Source build --wheel --out-dir $wheelRoot
            if ($LASTEXITCODE -ne 0) { throw "uv wheel build failed" }
        }
        finally {
            Pop-Location
        }
        $wheel = @(Get-ChildItem -LiteralPath $wheelRoot -Filter "*.whl" -File)
        if ($wheel.Count -ne 1) { throw "Expected exactly one wheel from uv build" }

        $requirements = Join-Path $temporaryRoot "requirements.lock.txt"
        & $uv.Source export --frozen --no-dev --no-emit-project --no-hashes --no-header `
            --output-file $requirements | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "uv dependency export failed" }

        $sensitivePatterns = @(
            '(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{20,}',
            '(?i)bearer\s+[A-Za-z0-9._-]{20,}',
            '(?i)(api[_-]?key|access[_-]?token|client[_-]?secret)\s*[:=]\s*["''][A-Za-z0-9._-]{12,}["'']',
            [regex]::Escape($env:USERPROFILE),
            [regex]::Escape($repoRoot)
        )

        $wheelAuditZip = Join-Path $temporaryRoot "wheel-audit.zip"
        $wheelAuditRoot = Join-Path $temporaryRoot "wheel-audit"
        Copy-Item -LiteralPath $wheel[0].FullName -Destination $wheelAuditZip
        Expand-Archive -LiteralPath $wheelAuditZip -DestinationPath $wheelAuditRoot
        foreach ($file in (Get-ChildItem -LiteralPath $wheelAuditRoot -File -Recurse)) {
            if ($file.Extension -notin @(".py", ".txt", ".json", ".toml", "")) { continue }
            $content = Get-Content -LiteralPath $file.FullName -Raw
            foreach ($pattern in $sensitivePatterns) {
                if ($content -match $pattern) {
                    throw "Sensitive value detected inside wheel: $($file.FullName)"
                }
            }
        }

        $profiles = @("core", "full")
        foreach ($profile in $profiles) {
            $stagingRoot = Join-Path $temporaryRoot ("ask-ai-mcp-" + $profile)
            $payloadRoot = Join-Path $stagingRoot "payload"
            New-Item -ItemType Directory -Path $payloadRoot -Force | Out-Null

            Copy-Item -Path (Join-Path $repoRoot "migration\common\*") -Destination $stagingRoot -Recurse
            Copy-Item -Path (Join-Path $repoRoot "migration\profiles\$profile\*") -Destination $stagingRoot -Recurse
            Copy-Item -LiteralPath $wheel[0].FullName -Destination $payloadRoot
            Copy-Item -LiteralPath $requirements -Destination $payloadRoot
            Copy-Item -LiteralPath (Join-Path $repoRoot "uv.lock") -Destination $payloadRoot
            Copy-Item -LiteralPath (Join-Path $repoRoot "pyproject.toml") -Destination $payloadRoot

            [ordered]@{
                package = "ask-ai-mcp"
                version = $version
                profile = $profile
                expected_tool_count = if ($profile -eq "core") { 12 } else { 16 }
                git_commit = $commit
                dirty_build = $isDirty
                created_at = (Get-Date).ToUniversalTime().ToString("o")
                includes_comfyui = $false
                includes_credentials = $false
                includes_local_state = $false
            } | ConvertTo-Json -Depth 3 | Set-Content -LiteralPath (Join-Path $stagingRoot "profile.json") -Encoding utf8

            $forbiddenNames = @(
                ".env", "usage.db", "config.toml", "claude_desktop_config.json"
            )
            $stagedFiles = @(Get-ChildItem -LiteralPath $stagingRoot -File -Recurse)
            foreach ($file in $stagedFiles) {
                if ($file.Name -in $forbiddenNames) {
                    throw "Forbidden personal-state filename entered package: $($file.FullName)"
                }
            }

            $textExtensions = @(".md", ".txt", ".json", ".toml", ".ps1", ".lock")
            foreach ($file in $stagedFiles) {
                if ($file.Extension -notin $textExtensions) { continue }
                $content = Get-Content -LiteralPath $file.FullName -Raw
                foreach ($pattern in $sensitivePatterns) {
                    if ($content -match $pattern) {
                        throw "Possible secret detected in staged file: $($file.FullName)"
                    }
                }
            }

            $manifestRoot = Join-Path $stagingRoot "manifests"
            New-Item -ItemType Directory -Path $manifestRoot -Force | Out-Null
            $hashLines = foreach ($file in (Get-ChildItem -LiteralPath $stagingRoot -File -Recurse | Sort-Object FullName)) {
                $relative = [IO.Path]::GetRelativePath($stagingRoot, $file.FullName).Replace("\", "/")
                "{0}  {1}" -f (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant(), $relative
            }
            $hashLines | Set-Content -LiteralPath (Join-Path $manifestRoot "files.sha256") -Encoding utf8

            $archivePath = Join-Path $releaseRoot ("ask-ai-mcp-{0}-{1}-win11.zip" -f $profile, $version)
            Compress-Archive -Path (Join-Path $stagingRoot "*") -DestinationPath $archivePath -CompressionLevel Optimal

            $verificationRoot = Join-Path $temporaryRoot ("verify-" + $profile)
            Expand-Archive -LiteralPath $archivePath -DestinationPath $verificationRoot
            $manifestLines = Get-Content -LiteralPath (Join-Path $verificationRoot "manifests\files.sha256")
            foreach ($line in $manifestLines) {
                if ($line -notmatch '^([a-f0-9]{64})  (.+)$') { throw "Invalid manifest line" }
                $actual = (Get-FileHash -LiteralPath (Join-Path $verificationRoot $Matches[2]) -Algorithm SHA256).Hash.ToLowerInvariant()
                if ($actual -ne $Matches[1]) { throw "Archive hash verification failed: $($Matches[2])" }
            }
        }
    }
    finally {
        if (Test-Path -LiteralPath $temporaryRoot) {
            Remove-Item -LiteralPath $temporaryRoot -Recurse -Force
        }
    }

    Get-ChildItem -LiteralPath $releaseRoot -File | Select-Object Name, Length,
        @{Name = "SHA256"; Expression = { (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant() }}
}
finally {
    Pop-Location
}
