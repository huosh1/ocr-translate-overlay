@echo off
rem Double-cliquable : contourne la politique d'execution PowerShell.
rem Passez -Korean pour installer aussi konlpy et le pack de langue coreen.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_windows.ps1" %*
echo.
pause
