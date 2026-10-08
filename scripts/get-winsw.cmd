@echo off
setlocal
rem Download WinSW x64 into tools\ for pack-msi. See doc/qpkg.md.
rem Usage: get-winsw.cmd [/force]

set "VER=2.12.0"
set "FORCE="
if /i "%~1"=="/force" set "FORCE=1"
if /i "%~1"=="force" set "FORCE=1"

pushd "%~dp0.."
set "REPO=%CD%"
popd

set "FILE=WinSW-x64.exe"
set "URL=https://github.com/winsw/winsw/releases/download/v%VER%/%FILE%"
set "DEST=%REPO%\tools\%FILE%"

if not exist "%REPO%\tools" mkdir "%REPO%\tools"

if exist "%DEST%" if not defined FORCE (
  echo Already present: %DEST%
  exit /b 0
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
exit /b 0
