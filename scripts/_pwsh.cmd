@echo off
setlocal EnableDelayedExpansion
rem Locate PowerShell 7 (pwsh). Do not use Windows PowerShell 5.1 (powershell.exe).

set "PWSH="
if exist "%ProgramFiles%\PowerShell\7\pwsh.exe" set "PWSH=%ProgramFiles%\PowerShell\7\pwsh.exe"
if not defined PWSH if exist "%ProgramFiles%\PowerShell\7-preview\pwsh.exe" set "PWSH=%ProgramFiles%\PowerShell\7-preview\pwsh.exe"
if not defined PWSH if exist "%LocalAppData%\Programs\PowerShell\7\pwsh.exe" set "PWSH=%LocalAppData%\Programs\PowerShell\7\pwsh.exe"

if not defined PWSH (
  for /f "delims=" %%I in ('where pwsh 2^>nul') do (
    echo %%I | findstr /i "WindowsApps" >nul
    if errorlevel 1 (
      set "PWSH=%%I"
      goto :run
    )
  )
)

:run
if not defined PWSH (
  echo PowerShell 7 ^(pwsh^) was not found.
  echo Install it:  winget install Microsoft.PowerShell
  pause
  exit /b 1
)

"%PWSH%" %*
