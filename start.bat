@echo off
title Playify - Bot Launcher
color 0b

echo ========================================
echo       Playify - Discord Music Bot
echo ========================================
echo.

echo [1/3] Verifying Python installation...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo ERROR: Python is not installed or not added to PATH!
    echo Please install Python 3.9 or higher and check the box "Add Python to PATH".
    echo.
    pause
    exit /b
)

echo [2/3] Installing/Updating requirements...
pip install -r requirements.txt
echo Installing playwright browsers (needed for some scrapers)...
playwright install

echo.
echo [3/3] Starting the bot...
echo.
python playify.py

echo.
echo The bot has crashed or stopped.
pause
