@echo off
rem Shortcut: pack-msi / pack-qpkg / pack-posix. No arg = all.
rem pack.cmd qpkg [arch ...]   pack.cmd x86_64  (all targets; arch goes to qpkg)
setlocal
set "S=%~dp0"
if "%~1"=="" goto :all
if /I "%~1"=="all" goto :all
if /I "%~1"=="msi" goto :msi
if /I "%~1"=="windows" goto :msi
if /I "%~1"=="qpkg" goto :qpkg
if /I "%~1"=="posix" goto :posix
if /I "%~1"=="linux" goto :posix
if /I "%~1"=="macos" goto :posix
if /I "%~1"=="-h" goto :usage
if /I "%~1"=="/?" goto :usage
if /I "%~1"=="--help" goto :usage
if /I "%~1"=="x86_64" goto :all_arch
if /I "%~1"=="amd64" goto :all_arch
if /I "%~1"=="x64" goto :all_arch
if /I "%~1"=="x86" goto :all_arch
if /I "%~1"=="x86_ce53xx" goto :all_arch
if /I "%~1"=="arm_64" goto :all_arch
if /I "%~1"=="arm64" goto :all_arch
if /I "%~1"=="aarch64" goto :all_arch
if /I "%~1"=="arm-x09" goto :all_arch
if /I "%~1"=="arm-x19" goto :all_arch
if /I "%~1"=="arm-x31" goto :all_arch
if /I "%~1"=="arm-x41" goto :all_arch
echo Unknown target: %~1
goto :usage

:all
call "%S%pack-msi.cmd" || exit /b 1
call "%S%pack-qpkg.cmd" || exit /b 1
call "%S%pack-posix.cmd" || exit /b 1
exit /b 0

:all_arch
call "%S%pack-msi.cmd" || exit /b 1
call "%S%pack-qpkg.cmd" %* || exit /b 1
call "%S%pack-posix.cmd" || exit /b 1
exit /b 0

:msi
call "%S%pack-msi.cmd"
exit /b %ERRORLEVEL%

:qpkg
call "%S%pack-qpkg.cmd" %2 %3 %4 %5 %6 %7 %8 %9
exit /b %ERRORLEVEL%

:posix
call "%S%pack-posix.cmd"
exit /b %ERRORLEVEL%

:usage
echo usage: pack.cmd [all^|msi^|qpkg^|posix] [qpkg-arch ...]
exit /b 1
