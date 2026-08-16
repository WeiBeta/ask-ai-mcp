[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string] $SourcePath,
    [Parameter(Mandatory = $true)]
    [string] $DestinationPath,
    [Parameter(Mandatory = $true)]
    [int] $StartSlide,
    [Parameter(Mandatory = $true)]
    [int] $EndSlide
)

$ErrorActionPreference = 'Stop'
$application = $null
$presentation = $null
try {
    $application = New-Object -ComObject PowerPoint.Application
    $application.AutomationSecurity = 3
    $application.DisplayAlerts = 1
    $presentation = $application.Presentations.Open($SourcePath, $true, $true, $false)
    if ($StartSlide -lt 1 -or $EndSlide -gt $presentation.Slides.Count) {
        throw 'Requested slide range is outside the presentation.'
    }
    $width = 1920
    $height = [Math]::Max(
        1,
        [Math]::Round($width * $presentation.PageSetup.SlideHeight / $presentation.PageSetup.SlideWidth)
    )
    for ($number = $StartSlide; $number -le $EndSlide; $number++) {
        $name = 'slide-{0:D4}.png' -f $number
        $target = Join-Path -Path $DestinationPath -ChildPath $name
        $presentation.Slides.Item($number).Export($target, 'PNG', $width, $height)
    }
}
finally {
    if ($null -ne $presentation) {
        $presentation.Close()
        [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($presentation)
    }
    if ($null -ne $application) {
        $application.Quit()
        [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($application)
    }
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
}
