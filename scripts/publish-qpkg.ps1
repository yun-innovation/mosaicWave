# Assemble a QDK tree under dist/qpkg/mosaicWave (gitignored).
# Then run .\scripts\pack-qpkg.cmd (qbuild). See doc/qpkg.md.

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$Stage = Join-Path $RepoRoot "dist\qpkg\mosaicWave"
$WebDir = Join-Path $RepoRoot "src\web"
$ServerDir = Join-Path $RepoRoot "src\server"
$QpkgDir = Join-Path $RepoRoot "src\qpkg"

function Write-UnixFile([string] $Src, [string] $Dest) {
    $text = [IO.File]::ReadAllText($Src)
    $text = $text -replace "`r`n", "`n" -replace "`r", "`n"
    $dir = Split-Path $Dest
    if (-not (Test-Path $dir)) {
        New-Item -ItemType Directory -Path $dir | Out-Null
    }
    $utf8 = New-Object System.Text.UTF8Encoding $false
    [IO.File]::WriteAllText($Dest, $text, $utf8)
}

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
New-Item -ItemType Directory -Path (Join-Path $Stage "shared\server") | Out-Null
New-Item -ItemType Directory -Path (Join-Path $Stage "shared\web") | Out-Null

Write-UnixFile (Join-Path $QpkgDir "qpkg.cfg") (Join-Path $Stage "qpkg.cfg")
Write-UnixFile (Join-Path $QpkgDir "package_routines") (Join-Path $Stage "package_routines")
Write-UnixFile (Join-Path $QpkgDir "shared\mosaicWave.sh") (Join-Path $Stage "shared\mosaicWave.sh")
Write-UnixFile (Join-Path $QpkgDir "shared\requirements.txt") (Join-Path $Stage "shared\requirements.txt")

Copy-Item (Join-Path $ServerDir "mosaicwave") (Join-Path $Stage "shared\server\mosaicwave") -Recurse
Get-ChildItem (Join-Path $Stage "shared\server") -Recurse -Directory -Filter "__pycache__" |
    Remove-Item -Recurse -Force
Copy-Item (Join-Path $OutDir "*") (Join-Path $Stage "shared\web") -Recurse -Force

$Icons = Join-Path $QpkgDir "icons"
if (Test-Path $Icons) {
    Copy-Item $Icons (Join-Path $Stage "icons") -Recurse
}

Write-Host "QPKG staging ready: $Stage"
Write-Host "Next: .\scripts\pack-qpkg.cmd  (qbuild in WSL)"
