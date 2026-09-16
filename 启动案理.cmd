@echo off
cd /d "%~dp0"

set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not exist "%PY%" set "PY=python"

netstat -ano | findstr ":8765" >nul
if errorlevel 1 (
  echo Starting Anli server ...
  start "Anli Server" "%PY%" -m anli.web
  timeout /t 4 /nobreak >nul
) else (
  echo Anli server is already running.
)

start "" "http://127.0.0.1:8765/"

echo.
echo URL   : http://127.0.0.1:8765/
echo Notice: keep the "Anli Server" window open.
echo.
pause
