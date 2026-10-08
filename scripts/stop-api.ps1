# Stop FastAPI (port 8000 / mosaicwave uvicorn).
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "_dev.ps1")

try {
    Write-Host "Stopping mosaicWave API..."
    Stop-MosaicApi
} catch {
    Write-Host $_ -ForegroundColor Red
} finally {
    Wait-EnterToClose
}
