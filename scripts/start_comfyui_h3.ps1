[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

$installRoot = "C:\AI\ComfyUI"
$pythonPath = Join-Path $installRoot "python_embeded\python.exe"
$mainPath = Join-Path $installRoot "ComfyUI\main.py"
$logRoot = Join-Path $installRoot "logs"
$workspaceRoot = "C:\Users\user\Documents\AskAI-Exchange\H3-Workspace"
$inputRoot = Join-Path $workspaceRoot "inputs"
$outputRoot = Join-Path $workspaceRoot "outputs"

if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    throw "ComfyUI Python was not found at $pythonPath"
}
if (-not (Test-Path -LiteralPath $mainPath -PathType Leaf)) {
    throw "ComfyUI entrypoint was not found at $mainPath"
}

$mutex = [Threading.Mutex]::new($false, "Local\AskAIMCP-ComfyUI-H3-Start")
$mutexAcquired = $false
try {
    $mutexAcquired = $mutex.WaitOne([TimeSpan]::FromSeconds(30))
    if (-not $mutexAcquired) {
        throw "Timed out waiting for another ComfyUI H3 startup attempt"
    }

    $existing = Get-CimInstance Win32_Process | Where-Object {
        $_.Name -eq "python.exe" -and
        $_.CommandLine -like "*$mainPath*" -and
        $_.CommandLine -like "*--port 8188*"
    }
    if ($existing) {
        Write-Output "ComfyUI H3 is already running on port 8188 (PID $($existing.ProcessId -join ', '))."
        return
    }

    New-Item -ItemType Directory -Path $logRoot -Force | Out-Null
    New-Item -ItemType Directory -Path $workspaceRoot -Force | Out-Null
    New-Item -ItemType Directory -Path $inputRoot -Force | Out-Null
    New-Item -ItemType Directory -Path $outputRoot -Force | Out-Null
    $arguments = @(
        "-s", $mainPath,
        "--windows-standalone-build",
        "--listen", "127.0.0.1",
        "--port", "8188",
        "--input-directory", $inputRoot,
        "--output-directory", $outputRoot,
        "--preview-method", "none",
        "--reserve-vram", "2"
    )
    $process = Start-Process `
        -FilePath $pythonPath `
        -ArgumentList $arguments `
        -WorkingDirectory (Join-Path $installRoot "ComfyUI") `
        -RedirectStandardOutput (Join-Path $logRoot "comfyui.stdout.log") `
        -RedirectStandardError (Join-Path $logRoot "comfyui.stderr.log") `
        -WindowStyle Hidden `
        -PassThru

    Write-Output "Started ComfyUI H3 on http://127.0.0.1:8188 (PID $($process.Id))."
}
finally {
    if ($mutexAcquired) {
        $mutex.ReleaseMutex()
    }
    $mutex.Dispose()
}
