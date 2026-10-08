@echo off
setlocal
rem Product process: same uvicorn app as QPKG / scripts\start.cmd.
set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"
if not defined ProgramData set "ProgramData=%SystemDrive%\ProgramData"
set "VENV=%ProgramData%\mosaicWave\runtime\venv"
call "%ROOT%\ensure-venv.cmd"
if errorlevel 1 exit /b 1
set "MOSAICWAVE_PLATFORM=standalone"
set "MOSAICWAVE_PROFILE=prod"
set "MOSAICWAVE_WEB_ROOT=%ROOT%\web"
set "PYTHONPATH=%ROOT%\server"
rem Do not set MOSAICWAVE_DATA_DIR. Prod default is %%PROGRAMDATA%%\mosaicWave (survives MSI uninstall).
rem Local scripts\\start.cmd uses MOSAICWAVE_PROFILE=dev → %%PROGRAMDATA%%\mosaicWave-dev.
set "MOSAICWAVE_PORT=8090"
for /f "tokens=2,*" %%A in ('reg query "HKLM\SOFTWARE\mosaicWave" /v Port 2^>nul') do set "MOSAICWAVE_PORT=%%B"
if not defined MOSAICWAVE_PORT set "MOSAICWAVE_PORT=8090"
cd /d "%ROOT%\server"
"%VENV%\Scripts\python.exe" -m uvicorn mosaicwave.main:app --host 127.0.0.1 --port %MOSAICWAVE_PORT%
exit /b %ERRORLEVEL%
