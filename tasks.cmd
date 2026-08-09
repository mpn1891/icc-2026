@echo off
REM Windows entry point:  tasks up   (instead of  python tasks.py up)
REM
REM A .cmd file rather than a .ps1 on purpose -- PowerShell's execution policy
REM blocks unsigned local .ps1 scripts by default, and nothing about this repo
REM justifies making people run Set-ExecutionPolicy before they can start it.
REM All the logic lives in tasks.py; this only picks a Python and forwards.
setlocal
python -c "" >nul 2>nul
if errorlevel 1 goto trypy
python "%~dp0tasks.py" %*
exit /b %errorlevel%
:trypy
py -3 "%~dp0tasks.py" %*
exit /b %errorlevel%
