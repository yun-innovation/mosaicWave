# Shared by port-who.ps1 / port-kill.ps1. Dot-source only.

function Get-MosaicListenPort {
    param([string] $Explicit)
    if ($Explicit) {
        if ($Explicit -notmatch '^\d+$') { throw "not a port number: $Explicit" }
        return @{ Port = [int]$Explicit; From = "script argument" }
    }
    if ($env:MOSAICWAVE_PORT -and $env:MOSAICWAVE_PORT -match '^\d+$') {
        return @{ Port = [int]$env:MOSAICWAVE_PORT; From = "MOSAICWAVE_PORT" }
    }
    $prog = if ($env:PROGRAMDATA) { $env:PROGRAMDATA } else { "C:\ProgramData" }
    foreach ($name in @("mosaicWave", "mosaicWave-dev")) {
        $file = Join-Path (Join-Path $prog $name) "web.port"
        if (Test-Path $file) {
            $text = (Get-Content -LiteralPath $file -Raw -ErrorAction SilentlyContinue)
            if ($text -and ($text.Trim() -match '^\d+$')) {
                return @{ Port = [int]$text.Trim(); From = "web.port ($file)" }
            }
        }
    }
    $reg = Get-ItemProperty -Path "HKLM:\SOFTWARE\mosaicWave" -Name Port -ErrorAction SilentlyContinue
    if ($reg -and "$($reg.Port)" -match '^\d+$') {
        return @{ Port = [int]$reg.Port; From = "HKLM\SOFTWARE\mosaicWave Port" }
    }
    return @{ Port = 8090; From = "packaged default" }
}

function Write-MosaicPortRecap {
    param([int] $Port, [string] $From)
    Write-Host @"
Listen port: $Port  ($From)

This is not always 8090. mosaicWave picks the first that applies:
  1. Argument to this script          (port-who.cmd 8100)
  2. MOSAICWAVE_PORT
  3. web.port in the data folder      (start scripts write it)
  4. Windows installer PORT           (HKLM\SOFTWARE\mosaicWave)
  5. QPKG Web_Port                    (mosaicWave.sh port)
  6. Packaged default                 8090
Local debug scripts\start.cmd uses 8000, not 8090.
"@
}

function Get-ListenPids {
    param([int] $Port)
    $fromNet = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique
    if ($fromNet) { return @($fromNet | Where-Object { $_ -gt 0 }) }

    $ids = [System.Collections.Generic.List[int]]::new()
    foreach ($line in (& netstat.exe -ano -p tcp)) {
        if ($line -notmatch "LISTENING") { continue }
        if ($line -notmatch ":$Port\s") { continue }
        if ($line -match "\s(\d+)\s*$") { $ids.Add([int]$Matches[1]) }
    }
    return @($ids | Where-Object { $_ -gt 0 } | Sort-Object -Unique)
}

function Write-ListenTable {
    param([int] $Port)
    $ids = @(Get-ListenPids $Port)
    if ($ids.Count -eq 0) {
        Write-Host "No process is listening on $Port."
        return $ids
    }
    foreach ($id in $ids) {
        $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$id" -ErrorAction SilentlyContinue
        $name = if ($proc) { $proc.Name } else { "?" }
        $cmd = if ($proc -and $proc.CommandLine) { $proc.CommandLine } else { "" }
        Write-Host ("PID {0,-8} {1} {2}" -f $id, $name, $cmd)
    }
    $svc = Get-Service -Name mosaicWave -ErrorAction SilentlyContinue
    if ($svc) {
        Write-Host "Windows service mosaicWave: $($svc.Status) (WinSW restarts uvicorn unless the service is stopped)."
    }
    return $ids
}
