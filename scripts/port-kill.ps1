# Stop whichever process is listening on mosaicWave's HTTP port.
param(
    [Parameter(Position = 0)]
    [string] $Port
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "_port.ps1")

$resolved = Get-MosaicListenPort -Explicit $Port
Write-MosaicPortRecap -Port $resolved.Port -From $resolved.From
Write-Host ""

$svc = Get-Service -Name mosaicWave -ErrorAction SilentlyContinue
if ($svc -and $svc.Status -eq "Running") {
    Write-Host "Stopping Windows service mosaicWave (otherwise WinSW restarts uvicorn)."
    Stop-Service -Name mosaicWave -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 1
}

$ids = @(Write-ListenTable -Port $resolved.Port)
foreach ($id in $ids) {
    if ($id -le 4) {
        Write-Host "Skipping system PID $id"
        continue
    }
    Write-Host "Stopping PID $id"
    & taskkill.exe /PID $id /T /F 2>$null | Out-Null
}
Start-Sleep -Seconds 1
$left = @(Get-ListenPids $resolved.Port)
if ($left.Count -gt 0) {
    Write-Host "Port $($resolved.Port) is still in use:"
    [void] (Write-ListenTable -Port $resolved.Port)
    exit 1
}
Write-Host "Port $($resolved.Port) is free."
