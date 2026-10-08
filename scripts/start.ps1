# Start FastAPI + Next.js in this PowerShell 7 session (no extra pwsh).
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "_dev.ps1")

$api = $null
$web = $null
try {
    Ensure-Api
    Ensure-Web
    Stop-ExistingDevServers -Api -Web

    Write-Host "API  http://127.0.0.1:8000/docs"
    Write-Host "Web  http://127.0.0.1:3000"
    Write-Host "Ctrl+C stops both."
    Write-Host ""

    $api = Start-Process -FilePath $script:Uvicorn -ArgumentList @(
        "mosaicwave.main:app",
        "--reload",
        "--host", "127.0.0.1",
        "--port", "8000"
    ) -WorkingDirectory $script:ServerDir -PassThru -NoNewWindow

    $npm = (Get-Command npm.cmd -ErrorAction Stop).Source
    $web = Start-Process -FilePath $npm -ArgumentList @("run", "dev") -WorkingDirectory $script:WebDir -PassThru -NoNewWindow
    $flag = Join-Path $(if ($env:PROGRAMDATA) { $env:PROGRAMDATA } else { "C:\ProgramData" }) "mosaicWave-dev\restart.flag"
    while ($true) {
        if ($web.HasExited) { break }
        if ($api.HasExited) {
            if (Test-Path -LiteralPath $flag) {
                Remove-Item -LiteralPath $flag -Force -ErrorAction SilentlyContinue
                Write-Host "Restarting API after Save folder..."
                $api = Start-Process -FilePath $script:Uvicorn -ArgumentList @(
                    "mosaicwave.main:app",
                    "--reload",
                    "--host", "127.0.0.1",
                    "--port", "8000"
                ) -WorkingDirectory $script:ServerDir -PassThru -NoNewWindow
                continue
            }
            break
        }
        Start-Sleep -Seconds 1
    }
} catch {
    Write-Host $_ -ForegroundColor Red
} finally {
    if ($web -and -not $web.HasExited) { Stop-ProcessTree $web.Id }
    if ($api -and -not $api.HasExited) { Stop-ProcessTree $api.Id }
    Wait-EnterToClose
}
