# Tar dist/posix/mosaicWave → dist/posix/mosaicWave_0.1.1_posix.tar.gz
$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$Stage = Join-Path $RepoRoot "dist\posix\mosaicWave"
if (-not (Test-Path (Join-Path $Stage "mosaicWave.sh"))) {
    throw "POSIX staging missing. Run .\scripts\publish-posix.cmd first."
}
$OutDir = Join-Path $RepoRoot "dist\posix"
$Tar = Join-Path $OutDir "mosaicWave_0.1.1_posix.tar.gz"
if (Test-Path $Tar) { Remove-Item $Tar -Force }
Push-Location $OutDir
try {
    tar -czf "mosaicWave_0.1.1_posix.tar.gz" mosaicWave
    if ($LASTEXITCODE -ne 0) { throw "tar failed" }
} finally {
    Pop-Location
}
Write-Host "Wrote $Tar"
Write-Host "On Linux/macOS: tar xf mosaicWave_0.1.1_posix.tar.gz && cd mosaicWave && sh install.sh"
