@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..") do set "PROJECT_DIR=%%~fI"

cd /d "%PROJECT_DIR%"

if not exist "logs\automated_scans" mkdir "logs\automated_scans"

for /f %%I in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd_HHmmss"') do set RUN_STAMP=%%I

python -u main.py > "logs\automated_scans\scan_%RUN_STAMP%.out.log" 2> "logs\automated_scans\scan_%RUN_STAMP%.err.log"
exit /b %ERRORLEVEL%
