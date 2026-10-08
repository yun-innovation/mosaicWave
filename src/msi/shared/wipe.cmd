@echo off
setlocal
rem Wipe library.db and/or storage for this app's own data folder, then recreate an
rem empty schema (same DDL as the service). Does not start uvicorn. No flags: wipes both.
rem   wipe.cmd
rem   wipe.cmd --db
rem   wipe.cmd --storage
set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"
if not defined ProgramData set "ProgramData=%SystemDrive%\ProgramData"
set "VENV=%ProgramData%\mosaicWave\runtime\venv"
call "%ROOT%\ensure-venv.cmd"
if errorlevel 1 exit /b 1
set "MOSAICWAVE_PLATFORM=standalone"
set "MOSAICWAVE_PROFILE=prod"
set "PYTHONPATH=%ROOT%\server"
rem Do not set MOSAICWAVE_DATA_DIR. Prod default is %%PROGRAMDATA%%\mosaicWave (same as mosaicWave-run.cmd).
cd /d "%ROOT%\server"
"%VENV%\Scripts\python.exe" -m mosaicwave.wipe %*
exit /b %ERRORLEVEL%
