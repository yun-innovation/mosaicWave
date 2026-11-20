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
rem The installer's PORT page value is only the port offered at first start. mosaicwave.serve picks the port:
rem MOSAICWAVE_PORT if set (explicit), else the port saved on the admin Server page, else this offer.
set "MOSAICWAVE_PORT_DEFAULT=8090"
for /f "tokens=2,*" %%A in ('reg query "HKLM\SOFTWARE\mosaicWave" /v Port 2^>nul') do set "MOSAICWAVE_PORT_DEFAULT=%%B"
cd /d "%ROOT%\server"
rem No --host and no --port: mosaicwave.serve reads MOSAICWAVE_HOST / MOSAICWAVE_PORT itself (explicit), then the
rem address and port saved on the admin Server page, then the defaults (doc\qpkg.md, Bind address).
"%VENV%\Scripts\python.exe" -m mosaicwave.serve
exit /b %ERRORLEVEL%
