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
    echo Python was not found! Do not worry, installing it for you automatically...
    echo Downloading Python 3.12...
    curl -# -L -o python-installer.exe https://www.python.org/ftp/python/3.12.3/python-3.12.3-amd64.exe
    echo Installing Python silently, please wait a minute...
    start /wait python-installer.exe /quiet InstallAllUsers=0 PrependPath=1 Include_test=0
    del python-installer.exe
    echo.
    echo ========================================================
    echo SUCCESS: Python has been installed automatically!
    echo To apply the changes, please close this black window 
    echo and double-click "start.bat" again!
    echo ========================================================
    echo.
    pause
    exit /b
)

echo [2/3] Installing/Updating requirements...
pip install -r requirements.txt
echo Installing playwright browsers...
playwright install

echo.
echo [3/3] Starting the bot...
echo.
python playify.py

echo.
echo The bot has crashed or stopped.
pause
