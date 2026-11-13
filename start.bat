@echo off
cd /d "%~dp0"
python launcher.py %*
if errorlevel 1 echo.
if errorlevel 1 echo [start] uscito con errore %errorlevel%
pause
