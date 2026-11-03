@echo off
rem Starts VendLog and opens it in your browser. Close this window to stop the server.
cd /d "%~dp0"
title VendLog

set "PY=python"
where python >nul 2>nul || set "PY=py -3"

%PY% -c "import flask" >nul 2>nul
if errorlevel 1 (
  echo Installing required packages...
  %PY% -m pip install -r requirements.txt
  if errorlevel 1 goto failed
)

%PY% app.py
if errorlevel 1 goto failed
exit /b 0

:failed
echo.
echo VendLog could not start. See the messages above.
pause
exit /b 1
