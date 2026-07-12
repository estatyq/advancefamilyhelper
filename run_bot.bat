@echo off
title Family Helper Bot & Server
echo ===================================================
echo   Family Helper Bot & Server Starting...
echo ===================================================
echo Checking Python dependencies...
python -m pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo.
    echo [WARNING] Failed to install dependencies automatically.
    echo Please make sure pip is installed and running.
    echo.
)
echo.
echo Starting bot.py...
python bot.py
if %errorlevel% neq 0 (
    echo.
    echo [ERROR] Bot crashed or failed to start.
    echo.
    pause
)
pause
