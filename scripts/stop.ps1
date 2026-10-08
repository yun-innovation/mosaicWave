# Stop FastAPI + Next.js started by the mosaic start scripts.
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "_dev.ps1")

try {
    Write-Host "Stopping mosaicWave API and web..."
    Stop-MosaicApi
    Stop-MosaicWeb
} catch {
    Write-Host $_ -ForegroundColor Red
} finally {
    Wait-EnterToClose
}
