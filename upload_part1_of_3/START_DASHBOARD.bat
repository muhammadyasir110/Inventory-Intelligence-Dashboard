@echo off
title Inventory Intelligence Platform 2.1
color 0b

echo ===============================================================================
echo                INVENTORY INTELLIGENCE PLATFORM 2.1
echo ===============================================================================
echo.
echo [1/3] Initializing local environment...
cd /d "%~dp0backend"

set "PYTHON_EXE=%~dp0backend\venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" (
    echo First run: creating Python virtual environment and installing requirements...
    python -m venv venv
    if errorlevel 1 (
        echo Could not create venv. Install Python 3.10+ from python.org and tick "Add to PATH".
        pause
        exit /b 1
    )
    "%PYTHON_EXE%" -m pip install --upgrade pip >nul
    "%PYTHON_EXE%" -m pip install -r requirements.txt
)
"%PYTHON_EXE%" -c "import numpy, scipy, fastapi, sqlalchemy, pypdf, pydantic; assert fastapi.__version__ >= '0.115'" >nul 2>&1
if errorlevel 1 (
    echo Installing or updating requirements...
    "%PYTHON_EXE%" -m pip install -r requirements.txt
)

echo [2/3] Checking if server is already running...
netstat -ano | findstr :8000 >nul 2>&1
if %errorlevel% equ 0 (
    echo Server is already active on port 8000.
) else (
    echo Starting server process...
    start "IIP Backend Service" /min "%PYTHON_EXE%" -m uvicorn main:app --host 127.0.0.1 --port 8000
    timeout /t 2 /nobreak >nul
)

echo.
echo ===============================================================================
echo   Platform is LIVE and READY!
echo.
echo   Local URL:    http://127.0.0.1:8000
echo   Development/demo mode. Default login on a NEW database: admin / admin123
echo   Change it with:  backend\venv\Scripts\python backend\scripts\manage_users.py set-password admin
echo ===============================================================================
echo.
echo [3/3] Opening in your web browser now...
start http://127.0.0.1:8000
echo.
echo Backend service is running in the background.
echo You can keep or minimize this window.
pause

