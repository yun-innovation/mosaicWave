# Start FastAPI (reload) on http://127.0.0.1:8000 in this session (MOSAICWAVE_DEV_HOST changes the address)
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "_dev.ps1")

try {
    Ensure-Api
    Stop-ExistingDevServers -Api
    Set-Location $script:ServerDir
    Write-Host "API  http://$($script:DevHost):8000/docs"
    & $script:Uvicorn mosaicwave.main:app --reload --host $script:DevHost --port 8000
} catch {
    Write-Host $_ -ForegroundColor Red
} finally {
    Wait-EnterToClose
}
