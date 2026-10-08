@echo off
"%~dp0_pwsh.cmd" -NoProfile -ExecutionPolicy Bypass -File "%~dp0publish-posix.ps1" %*
