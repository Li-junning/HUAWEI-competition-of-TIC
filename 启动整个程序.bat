@echo off
setlocal EnableExtensions

rem Always run relative to this file, so paths with spaces or Chinese characters work.
cd /d "%~dp0"
set "BACKEND_PY=%~dp0backend\.venv\Scripts\python.exe"

title AI Answer Verifier Launcher
echo [1/4] Checking local dependencies...

if not exist "%BACKEND_PY%" (
  echo.
  echo ERROR: backend\.venv\Scripts\python.exe was not found.
  echo Create the backend virtual environment and install the project first.
  pause
  exit /b 1
)

if not exist "%~dp0frontend\node_modules\vite\bin\vite.js" (
  echo.
  echo ERROR: frontend dependencies were not found.
  echo Run npm install inside the frontend directory first.
  pause
  exit /b 1
)

where npm.cmd >nul 2>&1
if errorlevel 1 (
  echo.
  echo ERROR: npm.cmd was not found in PATH.
  echo Install Node.js or add it to PATH, then try again.
  pause
  exit /b 1
)

pushd "%~dp0backend"
"%BACKEND_PY%" -c "from app.config import get_settings; get_settings()" >nul 2>&1
set "CONFIG_RESULT=%ERRORLEVEL%"
popd
if not "%CONFIG_RESULT%"=="0" (
  echo.
  echo ERROR: backend configuration is invalid.
  echo Check the project .env file.
  pause
  exit /b 1
)

echo [2/4] Checking ports 8000 and 5173...
call :IS_READY
if errorlevel 2 goto OLD_BACKEND
if not errorlevel 1 goto APP_READY

powershell.exe -NoProfile -Command "if (Test-NetConnection -ComputerName 127.0.0.1 -Port 8000 -InformationLevel Quiet -WarningAction SilentlyContinue) { exit 1 }"
if errorlevel 1 (set "BACKEND_RUNNING=1") else (set "BACKEND_RUNNING=")

powershell.exe -NoProfile -Command "if (Test-NetConnection -ComputerName 127.0.0.1 -Port 5173 -InformationLevel Quiet -WarningAction SilentlyContinue) { exit 1 }"
if errorlevel 1 (set "FRONTEND_RUNNING=1") else (set "FRONTEND_RUNNING=")

echo [3/4] Starting backend and frontend...
if defined BACKEND_RUNNING (
  echo Backend port 8000 is occupied; checking the existing service.
) else (
  start "AI Verifier Backend" /d "%~dp0backend" cmd.exe /k ""%BACKEND_PY%" -m uvicorn app.main:app --host 127.0.0.1 --port 8000"
)
if defined FRONTEND_RUNNING (
  echo Frontend port 5173 is occupied; checking the existing service.
) else (
  start "AI Verifier Frontend" /d "%~dp0frontend" cmd.exe /k "npm.cmd run dev -- --host 127.0.0.1 --strictPort"
)

echo [4/4] Waiting for the application...
for /L %%I in (1,1,45) do (
  call :IS_READY
  if errorlevel 2 goto OLD_BACKEND
  if not errorlevel 1 goto APP_READY
  powershell.exe -NoProfile -Command "Start-Sleep -Seconds 1" >nul 2>&1
)

echo.
echo ERROR: the application did not become ready within 45 seconds.
echo Check the Backend and Frontend windows and make sure ports 8000 and 5173 belong to this app.
pause
exit /b 1

:APP_READY
echo.
echo Application is ready:
echo http://127.0.0.1:5173/
if not defined AI_VERIFIER_NO_BROWSER start "" "http://127.0.0.1:5173/"
powershell.exe -NoProfile -Command "Start-Sleep -Seconds 2" >nul 2>&1
exit /b 0

:OLD_BACKEND
echo.
echo ERROR: the running backend does not include the knowledge API.
echo Close the old AI Verifier Backend window, then run this launcher again.
echo Refresh the application page after the backend restarts.
pause
exit /b 1

:IS_READY
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0backend\scripts\check_application_ready.ps1" >nul 2>&1
exit /b %ERRORLEVEL%
