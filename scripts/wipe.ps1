# Wipe library.db and/or storage for this app's own data dir, then recreate an empty
# schema (same DDL as API startup; does not start uvicorn). No flags: wipes both.
# Default data dir: %PROGRAMDATA%\mosaicWave-dev
#   .\scripts\wipe.ps1
#   .\scripts\wipe.ps1 --db
#   .\scripts\wipe.ps1 --storage
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "_dev.ps1")

try {
    Ensure-Api
    Set-Location $script:ServerDir
    $py = Join-Path $script:VenvDir "Scripts\python.exe"
    & $py -m mosaicwave.wipe @args
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} catch {
    Write-Host $_ -ForegroundColor Red
    exit 1
}
