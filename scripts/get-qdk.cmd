@echo off
setlocal EnableDelayedExpansion
rem Download the QDK .deb into tools\ for WSL apt install. See doc/qpkg.md.
rem Usage: get-qdk.cmd [amd64|arm64|armhf] [/force]

set "VER=2.5.3"
set "ARCH=amd64"
set "FORCE="

:parse
if "%~1"=="" goto :done_parse
if /i "%~1"=="/force" set "FORCE=1" & shift & goto :parse
if /i "%~1"=="force" set "FORCE=1" & shift & goto :parse
if /i "%~1"=="amd64" set "ARCH=amd64" & shift & goto :parse
if /i "%~1"=="arm64" set "ARCH=arm64" & shift & goto :parse
if /i "%~1"=="armhf" set "ARCH=armhf" & shift & goto :parse
echo Unknown argument: %~1
echo Usage: get-qdk.cmd [amd64^|arm64^|armhf] [/force]
exit /b 1

:done_parse

pushd "%~dp0.."
set "REPO=%CD%"
popd

set "FILE=qdk_%VER%_%ARCH%.deb"
set "URL=https://github.com/qnap-dev/QDK/releases/download/v%VER%/%FILE%"
set "DEST=%REPO%\tools\%FILE%"

if not exist "%REPO%\tools" mkdir "%REPO%\tools"

if exist "%DEST%" if not defined FORCE (
  echo Already present: %DEST%
  goto :hint
)

where curl.exe >nul 2>&1
if errorlevel 1 (
  echo curl.exe was not found. Install it or use Windows 10 1803+ / Windows 11.
  exit /b 1
)

echo Downloading %URL%
curl.exe -fL --retry 3 -o "%DEST%" "%URL%"
if errorlevel 1 (
  echo Download failed.
  if exist "%DEST%" del /q "%DEST%"
  exit /b 1
)

echo Saved %DEST%

:hint
set "WSLDEST="
for /f "delims=" %%I in ('wsl wslpath -a "%DEST%" 2^>nul') do set "WSLDEST=%%I"
echo.
echo Install in WSL ^(not in this cmd window^):
if defined WSLDEST (
  echo   sudo apt-get update
  echo   sudo apt-get install -y !WSLDEST!
) else (
  echo   sudo apt-get install -y /mnt/^<drive^>/mosaicWave/tools/%FILE%
)
echo Then: qbuild --help
exit /b 0
