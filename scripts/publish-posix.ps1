# Assemble Linux/macOS install tree under dist/posix/mosaicWave. Then pack-posix.
# See doc/qpkg.md.

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$Stage = Join-Path $RepoRoot "dist\posix\mosaicWave"
$WebDir = Join-Path $RepoRoot "src\web"
$ServerDir = Join-Path $RepoRoot "src\server"
$PosixDir = Join-Path $RepoRoot "src\posix"

if (-not (Test-Path (Join-Path $WebDir "node_modules"))) {
    Write-Host "Installing npm packages..."
    Push-Location $WebDir
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
Write-Host "Building web export..."
Push-Location $WebDir
try {
    npm run build
    if ($LASTEXITCODE -ne 0) { throw "next build failed" }
} finally {
    Pop-Location
}
$OutDir = Join-Path $WebDir "out"
if (-not (Test-Path (Join-Path $OutDir "index.html"))) {
    throw "src/web/out/index.html missing after next build"
}

if (Test-Path $Stage) {
    Remove-Item $Stage -Recurse -Force
}
New-Item -ItemType Directory -Path (Join-Path $Stage "server") | Out-Null
New-Item -ItemType Directory -Path (Join-Path $Stage "web") | Out-Null

Copy-Item (Join-Path $ServerDir "mosaicwave") (Join-Path $Stage "server\mosaicwave") -Recurse
Get-ChildItem (Join-Path $Stage "server") -Recurse -Directory -Filter "__pycache__" |
    Remove-Item -Recurse -Force
Copy-Item (Join-Path $OutDir "*") (Join-Path $Stage "web") -Recurse -Force
Copy-Item (Join-Path $RepoRoot "src\qpkg\shared\requirements.txt") (Join-Path $Stage "requirements.txt")
Copy-Item (Join-Path $PosixDir "mosaicWave.sh") $Stage
Copy-Item (Join-Path $PosixDir "install.sh") $Stage
Copy-Item (Join-Path $PosixDir "uninstall.sh") $Stage
Copy-Item (Join-Path $PosixDir "mosaicWave.service") $Stage
Copy-Item (Join-Path $PosixDir "com.mosaicwave.app.plist") $Stage
Copy-Item (Join-Path $PosixDir "macos-Info.plist") $Stage
Copy-Item (Join-Path $PosixDir "macos-open.sh") $Stage
Copy-Item (Join-Path $PosixDir "macos-open.c") $Stage
Copy-Item (Join-Path $PosixDir "_port.sh") $Stage
Copy-Item (Join-Path $PosixDir "port-who.sh") $Stage
Copy-Item (Join-Path $PosixDir "port-kill.sh") $Stage
Copy-Item (Join-Path $PosixDir "mosaicWave.icns") $Stage
Copy-Item (Join-Path $PosixDir "mosaicWave-icon.png") $Stage

function Strip-Cr([string]$Path) {
    $text = [IO.File]::ReadAllText($Path) -replace "`r", ""
    $utf8 = New-Object System.Text.UTF8Encoding $false
    [IO.File]::WriteAllText($Path, $text, $utf8)
}
foreach ($name in @("mosaicWave.sh", "install.sh", "uninstall.sh", "mosaicWave.service", "com.mosaicwave.app.plist", "macos-Info.plist", "macos-open.sh", "macos-open.c", "_port.sh", "port-who.sh", "port-kill.sh")) {
    Strip-Cr (Join-Path $Stage $name)
}

Write-Host "POSIX staging ready: $Stage"
Write-Host "Then:  .\scripts\pack-posix.cmd"
