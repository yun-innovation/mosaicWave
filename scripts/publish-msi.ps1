# Assemble a Windows install tree under dist/msi/mosaicWave (gitignored).
# Then run .\scripts\pack-msi.cmd (WiX). See doc/qpkg.md.

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$Stage = Join-Path $RepoRoot "dist\msi\mosaicWave"
$WebDir = Join-Path $RepoRoot "src\web"
$ServerDir = Join-Path $RepoRoot "src\server"
$MsiDir = Join-Path $RepoRoot "src\msi"

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
Copy-Item (Join-Path $MsiDir "shared\*") $Stage -Force

Write-Host "MSI staging ready: $Stage"
Write-Host "Once:  .\scripts\get-winsw.cmd"
Write-Host "Then:  .\scripts\pack-msi.cmd"
