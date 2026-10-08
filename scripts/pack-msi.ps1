# Build mosaicWave_0.1.0_x64.msi from dist/msi/mosaicWave. See doc/qpkg.md.

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$Stage = Join-Path $RepoRoot "dist\msi\mosaicWave"
$Wxs = Join-Path $RepoRoot "src\msi\mosaicWave.wxs"
$WxsUi = Join-Path $RepoRoot "src\msi\WixUI_mosaicWave.wxs"
$WinSw = Join-Path $RepoRoot "tools\WinSW-x64.exe"
$OutDir = Join-Path $RepoRoot "dist\msi"
$Msi = Join-Path $OutDir "mosaicWave_0.1.0_x64.msi"

if (-not (Test-Path (Join-Path $Stage "server\mosaicwave"))) {
    throw "MSI staging missing. Run .\scripts\publish-msi.cmd first."
}
if (-not (Test-Path $Wxs)) {
    throw "WiX source missing: $Wxs"
}
if (-not (Test-Path $WxsUi)) {
    throw "WiX UI source missing: $WxsUi"
}
if (-not (Test-Path $WinSw)) {
    throw "WinSW missing ($WinSw). Run .\scripts\get-winsw.cmd first."
}

Copy-Item (Join-Path $RepoRoot "src\msi\shared\*") $Stage -Force
Copy-Item $WinSw (Join-Path $Stage "mosaicWave.exe") -Force
Get-ChildItem $Stage -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue |
    Remove-Item -Recurse -Force
if (Test-Path (Join-Path $Stage "venv")) {
    Remove-Item (Join-Path $Stage "venv") -Recurse -Force
}

$wix = Get-Command wix -ErrorAction SilentlyContinue
if (-not $wix) {
    throw @"
WiX CLI not found. Install once (PowerShell 7):

  dotnet tool install --global wix

Then open a new terminal (so %USERPROFILE%\.dotnet\tools is on PATH) and re-run .\scripts\pack-msi.cmd
"@
}

if (-not (Test-Path $OutDir)) {
    New-Item -ItemType Directory -Path $OutDir | Out-Null
}

Write-Host "Running wix build..."
$wixVer = & wix --version
$eula = @()
if ($wixVer -match "^7") {
    $eula = @("--acceptEula", "wix7")
}
$need = @("WixToolset.Util.wixext", "WixToolset.UI.wixext")
$extList = & wix @eula extension list
foreach ($ext in $need) {
    if ($LASTEXITCODE -ne 0 -or "$extList" -notmatch [regex]::Escape($ext)) {
        Write-Host "Adding WiX extension $ext..."
        & wix @eula extension add $ext
        if ($LASTEXITCODE -ne 0) {
            throw "wix extension add $ext failed (exit $LASTEXITCODE)"
        }
        $extList = & wix @eula extension list
    }
}
$wixArgs = $eula + @(
    "build", $Wxs, $WxsUi,
    "-arch", "x64",
    "-ext", "WixToolset.Util.wixext",
    "-ext", "WixToolset.UI.wixext",
    "-bindpath", "Payload=$Stage",
    "-o", $Msi
)
& wix @wixArgs
if ($LASTEXITCODE -ne 0) {
    throw "wix build failed (exit $LASTEXITCODE)"
}

Write-Host "MSI ready: $Msi"
Write-Host "Install (elevated): .\scripts\install-msi.cmd"
Write-Host "Library data stays in %PROGRAMDATA%\mosaicWave when you uninstall."
