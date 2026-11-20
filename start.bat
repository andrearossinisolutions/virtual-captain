@echo off
cd /d "%~dp0"
rem --no-gui and --unlink: with a console (python). Otherwise a single window, no console (pythonw).
echo %* | findstr /i /c:"--no-gui" /c:"--unlink" >nul
if not errorlevel 1 goto console
set "PY="
for /f "delims=" %%P in ('where python 2^>nul') do if not defined PY set "PY=%%P"
if not defined PY goto console
set "PYW=%PY:python.exe=pythonw.exe%"
if not exist "%PYW%" goto console
start "" "%PYW%" launcher.py %*
exit /b 0
:console
python launcher.py %*
if errorlevel 1 echo [start] exited with error %errorlevel%
pause
