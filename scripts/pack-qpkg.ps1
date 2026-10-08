# Run qbuild on dist/qpkg/mosaicWave via WSL. Staging: publish-qpkg. See doc/qpkg.md.
# No args: x86_64 and arm_64. Pass a QDK arch (or x64 / arm64) to pack only that platform.

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$Stage = Join-Path $RepoRoot "dist\qpkg\mosaicWave"
$Cfg = Join-Path $Stage "qpkg.cfg"
if (-not (Test-Path $Cfg)) {
    throw "QPKG staging missing ($Cfg). Run .\scripts\publish-qpkg.cmd first."
}

if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
    throw "wsl.exe not found. Install WSL and QDK; see doc/qpkg.md"
}

function Get-WslPath([string]$WinPath) {
    $full = [IO.Path]::GetFullPath($WinPath)
    if ($full -notmatch '^([A-Za-z]):\\(.*)$') {
        throw "Cannot map $WinPath to a WSL path"
    }
    return "/mnt/$($Matches[1].ToLower())/$($Matches[2] -replace '\\','/')"
}

function Quote-ShSingle([string]$Value) {
    return "'" + ($Value -replace "'", "'\''") + "'"
}

$Sh = Join-Path $PSScriptRoot "pack-qpkg.sh"
$wslSh = Get-WslPath $Sh

$parts = @(Quote-ShSingle $wslSh)
foreach ($a in $args) {
    $parts += Quote-ShSingle ([string]$a)
}
$inner = "bash " + ($parts -join " ")
if ($args.Count -eq 0) {
    Write-Host "Running qbuild via WSL (all shipped arches: x86_64 arm_64)..."
} else {
    Write-Host "Running qbuild via WSL ($($args -join ' '))..."
}
& wsl.exe bash -lc $inner
if ($LASTEXITCODE -ne 0) {
    throw "qbuild failed (exit $LASTEXITCODE). Is QDK installed in WSL? See doc/qpkg.md"
}
