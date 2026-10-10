@echo off
setlocal
rem Install the built MSI. Run this cmd as Administrator.
rem Optional first argument is the listen port (default 8090, or the installer dialog).
rem msiexec cannot open paths that contain ".." — resolve to a full path first.
for %%I in ("%~dp0..\dist\msi\mosaicWave_0.1.1_x64.msi") do set "MSI=%%~fI"
if not exist "%MSI%" (
  echo MSI not found: %MSI%
  echo Run .\scripts\publish-msi.cmd then .\scripts\pack-msi.cmd
  exit /b 1
)
echo Installing "%MSI%"
echo Full UI: after the folder page, Web / API port ^(default 8090^) and Windows service.
if not "%~1"=="" (
  echo PORT=%~1
  msiexec /i "%MSI%" PORT="%~1" /qf
) else (
  msiexec /i "%MSI%" /qf
)
exit /b %ERRORLEVEL%
