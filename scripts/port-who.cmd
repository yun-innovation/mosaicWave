@echo off
"%~dp0_pwsh.cmd" -NoProfile -ExecutionPolicy Bypass -File "%~dp0port-who.ps1" %*
