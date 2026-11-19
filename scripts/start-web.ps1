# Start Next.js dev server on http://127.0.0.1:3000 in this session
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "_dev.ps1")

try {
    Ensure-Web
    Stop-ExistingDevServers -Web
    Set-Location $script:WebDir
    Write-Host "Web  http://$($script:DevHost):3000"
    npm run dev -- --hostname $script:DevHost
} catch {
    Write-Host $_ -ForegroundColor Red
} finally {
    Wait-EnterToClose
}
