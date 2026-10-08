@echo off
setlocal
rem Remove the mosaicWave program. Does not delete %%PROGRAMDATA%%\mosaicWave.
rem msiexec cannot open paths that contain ".." — resolve to a full path first.
for %%I in ("%~dp0..\dist\msi\mosaicWave_0.1.0_x64.msi") do set "MSI=%%~fI"
echo Uninstall mosaicWave ^(library stays in %%PROGRAMDATA%%\mosaicWave^)
if exist "%MSI%" (
  msiexec /x "%MSI%" /qb
  exit /b %ERRORLEVEL%
)
echo MSI file not found. Uninstall from Settings - Apps, or rebuild then run this script.
exit /b 1
