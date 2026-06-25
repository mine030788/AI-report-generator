@echo off
REM ============================================================
REM  luogu-report-generator one-click launcher (Windows)
REM  Double-click this file to start the web service.
REM ============================================================

chcp 65001 >nul
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
cd /d "%~dp0"

echo.
echo ==========================================================
echo   Luogu AI Report Generator - One-click Launcher
echo ==========================================================
echo.

REM 1) Check Python
where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python not found. Install Python 3.10+ and add to PATH.
    echo         Download: https://www.python.org/downloads/
    pause
    exit /b 1
)
echo [1/4] Python OK

REM 2) Install/check dependencies
echo [2/4] Installing dependencies ...
python -m pip install --quiet -r requirements.txt
if errorlevel 1 (
    echo [ERROR] Failed to install dependencies.
    pause
    exit /b 1
)
echo       Done.

REM 3) Kill any process on port 8765
echo [3/4] Cleaning port 8765 ...
setlocal enabledelayedexpansion
set PORT=8765
for /f "tokens=5" %%a in ('netstat -ano ^| findstr /R "[:][0-9]*:!PORT! "') do (
    taskkill /F /PID %%a >nul 2>&1
)
endlocal

REM 4) Start web server
echo [4/4] Starting web server ...
echo.
echo   URL: http://127.0.0.1:8765/
echo   Press Ctrl+C to stop.
echo.

REM Auto-open browser after 2 seconds (background)
start "" /min cmd /c "timeout /t 2 /nobreak >nul & start http://127.0.0.1:8765/"

python -m luogu_report_generator web --host 127.0.0.1 --port 8765

echo.
echo [Stopped] Web server has exited.
pause
