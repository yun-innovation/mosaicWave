@echo off
"%~dp0_pwsh.cmd" -NoProfile -ExecutionPolicy Bypass -File "%~dp0wipe.ps1" %*
if errorlevel 1 pause
