@echo off
title Playify - Bot Launcher
color 0b

echo ========================================
echo       Playify - Discord Music Bot
echo ========================================
echo.

:: --- SYSTEM CHECKS (SILENT) ---

:: 1. Python
python --version >nul 2>&1 || goto install_python

:: 2. FFmpeg
ffmpeg -version >nul 2>&1 || (if not exist "ffmpeg.exe" goto install_ffmpeg)

:: 3. Dependencies
python -c "import discord, yt_dlp, spotipy" >nul 2>&1 || goto install_deps

:: 4. Configuration
if not exist ".env" goto setup_env

:run_bot
echo [INFO] Starting Playify...
python playify.py

echo.
echo The bot has crashed or stopped.
pause
exit /b


:: --- INSTALLATION / SETUP JUMPS ---

:install_python
echo [!] Python not found. Installing it for you automatically...
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

:install_ffmpeg
echo [!] FFmpeg not found. Downloading it now to enable music playback...
echo This is a one-time setup step.
echo Downloading FFmpeg 6.1.1...
curl -# -L -o ffmpeg.7z "https://www.videohelp.com/download/ffmpeg-6.1.1-full_build.7z?r=zndlFgBq"
echo Extracting FFmpeg... (this might take a minute)
tar -xf ffmpeg.7z
echo Moving ffmpeg.exe to the bot folder...
for /r %%i in (ffmpeg.exe) do (
    if not "%%~dpi"=="%~dp0" move "%%i" . >nul 2>&1
)
echo Cleaning up temporary files...
del ffmpeg.7z >nul 2>&1
for /d %%d in (ffmpeg-6.1.1-*) do rd /s /q "%%d" >nul 2>&1
echo.
echo FFmpeg installed successfully!
echo.
goto install_deps

:install_deps
echo [!] Dependencies missing. Installing now (this may take a minute)...
pip install -r requirements.txt --quiet --no-warn-script-location
python -m playwright install >nul 2>&1
if exist ".env" goto run_bot
goto setup_env

:setup_env
echo [!] Configuration (.env) missing. Let's configure your bot.
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
echo Setup Complete!
echo.
goto run_bot
