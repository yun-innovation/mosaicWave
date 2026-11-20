# Start FastAPI + Next.js in this PowerShell 7 session (no extra pwsh).
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "_dev.ps1")

$api = $null
$web = $null
try {
    Ensure-Api
    Ensure-Web
    Stop-ExistingDevServers -Api -Web

    $apiPlan = (& $script:VenvPython -m mosaicwave.serve --describe --port 8000 2>&1 | Out-String).Trim()
    Write-Host "API  $apiPlan"
    Write-Host "Web  $($script:DevHost):3000 ($($script:DevHostSource))"
    Write-Host "Ctrl+C stops both."
    Write-Host ""

    $api = Start-Process -FilePath $script:VenvPython -ArgumentList (Get-ApiArgs) -WorkingDirectory $script:ServerDir -PassThru -NoNewWindow

    $npm = (Get-Command npm.cmd -ErrorAction Stop).Source
    $web = Start-Process -FilePath $npm -ArgumentList @("run", "dev", "--", "--hostname", $script:DevHost) -WorkingDirectory $script:WebDir -PassThru -NoNewWindow
    $flag = Join-Path $(if ($env:PROGRAMDATA) { $env:PROGRAMDATA } else { "C:\ProgramData" }) "mosaicWave-dev\restart.flag"
    while ($true) {
        if ($web.HasExited) { break }
        if ($api.HasExited) {
            if (Test-Path -LiteralPath $flag) {
                Remove-Item -LiteralPath $flag -Force -ErrorAction SilentlyContinue
                Write-Host "Restarting API after Save folder..."
                $api = Start-Process -FilePath $script:VenvPython -ArgumentList (Get-ApiArgs) -WorkingDirectory $script:ServerDir -PassThru -NoNewWindow
                # The API address may have changed (admin Network page): move the web dev server with it.
                $oldHost = $script:DevHost
                Resolve-DevHost
                if ($script:DevHost -ne $oldHost) {
                    Write-Host "Web  moving to $($script:DevHost):3000 ($($script:DevHostSource))"
                    Stop-ProcessTree $web.Id
                    Stop-MosaicWeb -QuietIfNone
                    Wait-PortFree 3000
                    $web = Start-Process -FilePath $npm -ArgumentList @("run", "dev", "--", "--hostname", $script:DevHost) -WorkingDirectory $script:WebDir -PassThru -NoNewWindow
                }
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
