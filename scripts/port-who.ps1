# Show whichever process is listening on mosaicWave's HTTP port.
param(
    [Parameter(Position = 0)]
    [string] $Port
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "_port.ps1")

$resolved = Get-MosaicListenPort -Explicit $Port
Write-MosaicPortRecap -Port $resolved.Port -From $resolved.From
Write-Host ""
[void] (Write-ListenTable -Port $resolved.Port)
