@echo off
setlocal EnableDelayedExpansion
rem Venv lives in ProgramData (Program Files is not writable for a normal user).
set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"
if not defined ProgramData set "ProgramData=%SystemDrive%\ProgramData"
set "RUNTIME=%ProgramData%\mosaicWave\runtime"
set "VENV=%RUNTIME%\venv"

if exist "%VENV%\Scripts\python.exe" (
  "%VENV%\Scripts\python.exe" -c "import uvicorn" 2>nul
  if not errorlevel 1 (
    rem Same requirements.txt as the last good install? An upgrade keeps the venv, so compare the saved copy.
    fc /b "%ROOT%\requirements.txt" "%VENV%\requirements.installed.txt" >nul 2>&1
    if not errorlevel 1 exit /b 0
    rem The venv works but requirements.txt changed (an upgrade). Install through the venv's own Python: the
    rem service account often has no py or python on PATH. A failed install (offline PC) must not stop the
    rem service, so warn and start with the venv as it is; the next start tries again.
    echo requirements.txt changed; updating the Python packages
    "%VENV%\Scripts\python.exe" -m pip install --prefer-binary -r "%ROOT%\requirements.txt"
    if errorlevel 1 (
      echo pip install failed ^(needs HTTPS to pypi.org^); starting with the packages already installed.
      exit /b 0
    )
    copy /y "%ROOT%\requirements.txt" "%VENV%\requirements.installed.txt" >nul
    exit /b 0
  )
)

if not exist "%RUNTIME%" mkdir "%RUNTIME%" 2>nul
if not exist "%RUNTIME%" (
  echo Cannot create "%RUNTIME%" ^(access denied^).
  echo Start the mosaicWave service, or run this from an elevated Command Prompt.
  exit /b 1
)

set "PYHOST="
where py >nul 2>&1
if not errorlevel 1 (
  py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" 2>nul
  if not errorlevel 1 set "PYHOST=py -3"
)
if not defined PYHOST (
  where python >nul 2>&1
  if not errorlevel 1 (
    python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" 2>nul
    if not errorlevel 1 set "PYHOST=python"
  )
)
if not defined PYHOST (
  echo mosaicWave needs Python 3.10 or newer on PATH ^(python.org, or the py launcher^).
  exit /b 1
)

echo Creating Python venv in "%VENV%"
%PYHOST% -m venv "%VENV%"
if errorlevel 1 exit /b 1
"%VENV%\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 exit /b 1
"%VENV%\Scripts\python.exe" -m pip install --prefer-binary -r "%ROOT%\requirements.txt"
if errorlevel 1 (
  echo pip install failed. The PC needs HTTPS to pypi.org.
  exit /b 1
)
copy /y "%ROOT%\requirements.txt" "%VENV%\requirements.installed.txt" >nul
exit /b 0
