@echo off
title Playify - Bot Launcher
color 0b

echo ========================================
echo       Playify - Discord Music Bot
echo ========================================
echo.

if exist ".env" goto run_bot

echo [SETUP] Welcome! It looks like it's your first time (or you deleted your .env).
echo [SETUP] Let's configure your bot.
echo.

echo Verifying Python installation...
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

echo [1.5/3] Verifying FFmpeg...
ffmpeg -version >nul 2>&1
if %errorlevel% neq 0 (
    if not exist "ffmpeg.exe" (
        echo FFmpeg was not found! Downloading it now to enable music playback...
        echo This is a one-time setup step.
        echo Downloading FFmpeg 6.1.1...
        curl -# -L -o ffmpeg.7z "https://www.videohelp.com/download/ffmpeg-6.1.1-full_build.7z?r=zndlFgBq"
        echo Extracting FFmpeg... (this might take a minute)
        tar -xf ffmpeg.7z
        echo Moving ffmpeg.exe to the bot folder...
        for /r %%i in (ffmpeg.exe) do move "%%i" . >nul
        echo Cleaning up temporary files...
        del ffmpeg.7z
        for /d %%d in (ffmpeg-6.1.1-*) do rd /s /q "%%d"
        echo.
        echo FFmpeg installed successfully!
        echo.
    )
)

echo [2/3] Installing/Updating requirements...
echo.
echo Now, we need your Discord and Spotify tokens!
echo You only need to do this once.
echo (Hint: Right-click to paste text into this window)
echo.

set /p DISCORD="Paste your Discord Bot Token (Required): "
set /p SPOTIFY_ID="Paste your Spotify Client ID (Optional, press Enter to skip): "
set /p SPOTIFY_SECRET="Paste your Spotify Client Secret (Optional, press Enter to skip): "
set /p GENIUS_TOKEN="Paste your Genius API Token for lyrics (Optional, press Enter to skip): "

> .env echo DISCORD_TOKEN=%DISCORD%
>> .env echo SPOTIFY_CLIENT_ID=%SPOTIFY_ID%
>> .env echo SPOTIFY_CLIENT_SECRET=%SPOTIFY_SECRET%
>> .env echo GENIUS_TOKEN=%GENIUS_TOKEN%

echo.
echo Secrets saved to the .env file successfully!
echo.
echo Installing requirements (this may take a few minutes)...
pip install -r requirements.txt
echo Installing playwright browsers...
python -m playwright install

echo.
echo Setup Complete! You won't have to do this again.
echo.

:run_bot
python playify.py

echo.
echo The bot has crashed or stopped.
pause
