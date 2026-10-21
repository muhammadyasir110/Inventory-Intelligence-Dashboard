@echo off
title Inventory Intelligence Platform ? Public Online Tunnel
color 0a

echo ===============================================================================
echo            INVENTORY INTELLIGENCE PLATFORM ? PUBLIC ONLINE LINK
echo ===============================================================================
echo.
echo WARNING: this exposes the dashboard to the INTERNET through localhost.run.
echo Do NOT use it while the admin password is still the demo password admin123.
echo Change it first:  backend\venv\Scripts\python backend\scripts\manage_users.py set-password admin
echo Use it only for short demos; it is not a secure deployment.
echo.
choice /M "I have changed the demo password and want to continue"
if errorlevel 2 exit /b 1
echo.
echo [1/2] Ensuring local server is running on port 8000...
cd /d "%~dp0backend"
if exist "venv\Scripts\activate.bat" call venv\Scripts\activate.bat
start /b python -m uvicorn main:app --host 127.0.0.1 --port 8000 >nul 2>&1
timeout /t 2 /nobreak >nul

echo [2/2] Generating secure public HTTPS tunnel link...
echo.
echo ===============================================================================
echo Copy and share the HTTPS link shown below (e.g. https://...lhr.life)
echo Log in with the accounts you created (manage_users.py).
echo ===============================================================================
echo.

ssh -o StrictHostKeyChecking=no -R 80:localhost:8000 nokey@localhost.run
pause
