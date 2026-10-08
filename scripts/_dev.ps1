# Shared by start.ps1 / start-api.ps1 / start-web.ps1. Dot-source only.

$script:ScriptsDir = $PSScriptRoot
$script:RepoRoot = Split-Path -Parent $PSScriptRoot
$script:ServerDir = Join-Path $script:RepoRoot "src\server"
$script:WebDir = Join-Path $script:RepoRoot "src\web"
$script:VenvDir = Join-Path $script:ServerDir ".venv"
$script:Uvicorn = Join-Path $script:VenvDir "Scripts\uvicorn.exe"

function Find-Python {
    $local = Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"
    if (Test-Path $local) { return $local }
    $cmd = Get-Command py -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd -and $cmd.Source -notmatch "WindowsApps") { return $cmd.Source }
    throw "Python 3.12+ not found. Install Python 3.12 and retry."
}

function Ensure-Api {
    $env:MOSAICWAVE_PROFILE = "dev"
    $data = Join-Path ($env:PROGRAMDATA) "mosaicWave-dev"
    if (-not $env:PROGRAMDATA) { $data = "C:\ProgramData\mosaicWave-dev" }
    Write-Host "Data $data  (MOSAICWAVE_PROFILE=dev; MSI uses %PROGRAMDATA%\mosaicWave)"
    if (Test-Path $script:Uvicorn) { return }
    Write-Host "Creating venv and installing mosaicwave..."
    $python = Find-Python
    if ((Split-Path -Leaf $python) -eq "py.exe") {
        & $python -3.12 -m venv $script:VenvDir
    } else {
        & $python -m venv $script:VenvDir
    }
    Push-Location $script:ServerDir
    try {
        & (Join-Path $script:VenvDir "Scripts\pip.exe") install -e ".[dev]"
    } finally {
        Pop-Location
    }
}

function Ensure-Web {
    $modules = Join-Path $script:WebDir "node_modules"
    if (Test-Path $modules) { return }
    Write-Host "Installing npm packages..."
    Push-Location $script:WebDir
    try {
        npm install --no-audit --no-fund
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "npm install failed (often TLS intercept). Retrying with --strict-ssl false."
            npm install --no-audit --no-fund --strict-ssl false
            if ($LASTEXITCODE -ne 0) { throw "npm install failed" }
        }
    } finally {
        Pop-Location
    }
}

function Stop-ProcessTree([int] $ProcessId) {
    if ($ProcessId -gt 0) {
        & taskkill.exe /PID $ProcessId /T /F 2>$null | Out-Null
    }
}

function Get-ListenPids([int] $Port) {
    $fromNet = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique
    if ($fromNet) { return @($fromNet) }

    $pids = [System.Collections.Generic.List[int]]::new()
    $lines = & netstat.exe -ano -p tcp
    foreach ($line in $lines) {
        if ($line -notmatch "LISTENING") { continue }
        if ($line -notmatch ":$Port\s") { continue }
        if ($line -match "\s(\d+)\s*$") { $pids.Add([int]$Matches[1]) }
    }
    return @($pids | Sort-Object -Unique)
}

function Get-PidsMatchingCommand([string] $NamePattern, [string] $CommandPattern) {
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object {
            $_.Name -match $NamePattern -and
            $_.CommandLine -and
            $_.CommandLine -match $CommandPattern
        } |
        Select-Object -ExpandProperty ProcessId
}

function Stop-Pids([int[]] $ProcessIds, [string] $Label, [switch] $QuietIfNone) {
    $unique = @($ProcessIds | Where-Object { $_ -and $_ -gt 0 } | Sort-Object -Unique)
    if ($unique.Count -eq 0) {
        if (-not $QuietIfNone) {
            Write-Host "No $Label process found."
        }
        return
    }
    foreach ($id in $unique) {
        Write-Host "Stopping $Label PID $id"
        Stop-ProcessTree $id
    }
}

function Wait-PortFree([int] $Port, [int] $TimeoutMs = 5000) {
    $deadline = (Get-Date).AddMilliseconds($TimeoutMs)
    while ((Get-Date) -lt $deadline) {
        if ((Get-ListenPids $Port).Count -eq 0) { return }
        Start-Sleep -Milliseconds 200
    }
    if ((Get-ListenPids $Port).Count -gt 0) {
        throw "Port $Port is still in use after stopping the previous process."
    }
}

function Stop-MosaicApi {
    param([switch] $QuietIfNone)
    $ids = @()
    $ids += Get-ListenPids 8000
    $ids += Get-PidsMatchingCommand "^(python|uvicorn)\.exe$" "mosaicwave\.main:app"
    $venvExe = [regex]::Escape($script:VenvDir)
    $ids += Get-PidsMatchingCommand "^(python|uvicorn)\.exe$" $venvExe
    Stop-Pids $ids "API" -QuietIfNone:$QuietIfNone
}

function Stop-MosaicWeb {
    param([switch] $QuietIfNone)
    $ids = @()
    $ids += Get-ListenPids 3000
    $webDir = [regex]::Escape($script:WebDir)
    $ids += Get-PidsMatchingCommand "^node\.exe$" $webDir
    Stop-Pids $ids "web" -QuietIfNone:$QuietIfNone
}

function Stop-ExistingDevServers {
    param(
        [switch] $Api,
        [switch] $Web
    )
    if ($Api) {
        $busy = (Get-ListenPids 8000).Count -gt 0
        Stop-MosaicApi -QuietIfNone
        if ($busy) { Wait-PortFree 8000 }
    }
    if ($Web) {
        $busy = (Get-ListenPids 3000).Count -gt 0
        Stop-MosaicWeb -QuietIfNone
        if ($busy) { Wait-PortFree 3000 }
    }
}

function Wait-EnterToClose {
    Write-Host ""
    try {
        Read-Host "Press Enter to close"
    } catch {
        Start-Sleep -Seconds 1
    }
}
