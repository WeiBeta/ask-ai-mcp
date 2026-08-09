[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

function Get-CommandInfo {
    param([Parameter(Mandatory)][string]$Name)

    $command = Get-Command $Name -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -eq $command) {
        return [ordered]@{ available = $false; path = $null }
    }
    return [ordered]@{ available = $true; path = $command.Source }
}

$python = Get-CommandInfo -Name "python"
$uv = Get-CommandInfo -Name "uv"
$docker = Get-CommandInfo -Name "docker"
$wsl = Get-CommandInfo -Name "wsl"
$gpu = Get-CimInstance Win32_VideoController | Select-Object Name, AdapterRAM, DriverVersion

[ordered]@{
    collected_at = (Get-Date).ToUniversalTime().ToString("o")
    windows = [ordered]@{
        caption = (Get-CimInstance Win32_OperatingSystem).Caption
        version = [Environment]::OSVersion.VersionString
        architecture = $env:PROCESSOR_ARCHITECTURE
    }
    powershell = [ordered]@{
        edition = $PSVersionTable.PSEdition
        version = $PSVersionTable.PSVersion.ToString()
    }
    python = $python
    uv = $uv
    docker = $docker
    wsl = $wsl
    gpu = @($gpu)
} | ConvertTo-Json -Depth 6
