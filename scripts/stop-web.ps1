# Stop Next.js (port 3000 / src/web).
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "_dev.ps1")

try {
    Write-Host "Stopping mosaicWave web..."
    Stop-MosaicWeb
} catch {
    Write-Host $_ -ForegroundColor Red
} finally {
    Wait-EnterToClose
}
