@echo off
setlocal
cd /d "%~dp0.."
python -u antigravity_preview.py %*
exit /b %ERRORLEVEL%
