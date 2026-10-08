@echo off
rem Shortcut: publish-msi / publish-qpkg / publish-posix. No arg = all.
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
echo Unknown target: %~1
goto :usage

:all
call "%S%publish-msi.cmd" || exit /b 1
call "%S%publish-qpkg.cmd" || exit /b 1
call "%S%publish-posix.cmd" || exit /b 1
exit /b 0

:msi
call "%S%publish-msi.cmd"
exit /b %ERRORLEVEL%

:qpkg
call "%S%publish-qpkg.cmd"
exit /b %ERRORLEVEL%

:posix
call "%S%publish-posix.cmd"
exit /b %ERRORLEVEL%

:usage
echo usage: publish.cmd [all^|msi^|qpkg^|posix]
exit /b 1
