# Start FastAPI (reload) on port 8000 in this session. The address is the one saved on the admin Server page, else 127.0.0.1.
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "_dev.ps1")

try {
    Ensure-Api
    Stop-ExistingDevServers -Api
    Set-Location $script:ServerDir
    Write-Host "API  port 8000 (address from the admin Server page, default 127.0.0.1; see the 'bind' line below)"
    & $script:VenvPython @(Get-ApiArgs)
} catch {
    Write-Host $_ -ForegroundColor Red
} finally {
    Wait-EnterToClose
}
