@echo off
setlocal EnableExtensions DisableDelayedExpansion
title Playify - Bot Launcher
color 0b

cd /d "%~dp0" || (
    echo [ERROR] Could not switch to the Playify folder.
    pause
    exit /b 1
)

set "ROOT=%CD%"
set "PATH=%ROOT%\bin;%PATH%"
set "PYTHON="
set "PYTHON_INSTALLED_BY_START=0"
set "VENV_PYTHON=%ROOT%\.venv\Scripts\python.exe"
set "PYTHON_INSTALLER=%TEMP%\playify-python-3.12.3.exe"
set "PYTHON_URL=https://www.python.org/ftp/python/3.12.3/python-3.12.3-amd64.exe"

echo ========================================
echo       Playify - Discord Music Bot
echo ========================================
echo.

if not exist "%ROOT%\playify.py" goto missing_entrypoint
if not exist "%ROOT%\requirements.txt" goto missing_requirements

call :prepare_python
if errorlevel 1 goto fail

call :ensure_ffmpeg
if errorlevel 1 goto fail

call :ensure_deps
if errorlevel 1 goto fail

if not exist "%ROOT%\.env" goto setup_env
goto run_bot

:run_bot
echo [INFO] Starting Playify...
"%PYTHON%" "%ROOT%\playify.py"

echo.
echo The bot has crashed or stopped.
pause
exit /b

:setup_env
echo [!] Configuration (.env) missing. Let's configure your bot.
echo (Hint: Right-click to paste text into this window)
echo.

set /p DISCORD="Paste your Discord Bot Token (Required): "
set /p SPOTIFY_ID="Paste your Spotify Client ID (Optional, press Enter to skip): "
set /p SPOTIFY_SECRET="Paste your Spotify Client Secret (Optional, press Enter to skip): "
set /p GENIUS_TOKEN="Paste your Genius API Token for lyrics (Optional, press Enter to skip): "

setlocal EnableDelayedExpansion
> "%ROOT%\.env" echo DISCORD_TOKEN=!DISCORD!
>> "%ROOT%\.env" echo SPOTIFY_CLIENT_ID=!SPOTIFY_ID!
>> "%ROOT%\.env" echo SPOTIFY_CLIENT_SECRET=!SPOTIFY_SECRET!
>> "%ROOT%\.env" echo GENIUS_TOKEN=!GENIUS_TOKEN!
endlocal

echo.
echo Secrets saved to the .env file successfully!
echo Setup Complete!
echo.
goto run_bot

:prepare_python
if exist "%VENV_PYTHON%" (
    "%VENV_PYTHON%" --version >nul 2>&1
    if not errorlevel 1 (
        call :is_supported_python "%VENV_PYTHON%"
        if not errorlevel 1 (
            set "PYTHON=%VENV_PYTHON%"
            goto ensure_pip
        )
    )

    echo [!] Existing local Python environment is broken or unsupported. Recreating it...
    rmdir /s /q "%ROOT%\.venv" >nul 2>&1
)

call :find_system_python
if errorlevel 1 goto install_python

call :is_supported_python "%PYTHON%"
if errorlevel 1 (
    echo [!] Found Python, but Playify needs Python 3.9 through 3.13.
    "%PYTHON%" --version
    goto install_python
)

goto create_venv

:find_system_python
set "PYTHON="
for %%V in (3.12 3.13 3.11 3.10 3.9) do (
    py -%%V -c "import sys" >nul 2>&1
    if not errorlevel 1 (
        for /f "usebackq delims=" %%P in (`py -%%V -c "import sys; print(sys.executable)"`) do set "PYTHON=%%P"
        exit /b 0
    )
)

python -c "import sys" >nul 2>&1
if not errorlevel 1 (
    for /f "usebackq delims=" %%P in (`python -c "import sys; print(sys.executable)"`) do set "PYTHON=%%P"
    exit /b 0
)

exit /b 1

:is_supported_python
"%~1" -c "import sys; v=sys.version_info[:2]; raise SystemExit(0 if (3, 9) <= v <= (3, 13) else 1)" >nul 2>&1
exit /b %ERRORLEVEL%

:install_python
echo [!] Python 3.9-3.13 was not found. Installing Python 3.12 for Playify...
echo Downloading Python 3.12.3...
curl -fL -o "%PYTHON_INSTALLER%" "%PYTHON_URL%"
if errorlevel 1 goto python_failed

echo Installing Python silently, please wait a minute...
start /wait "" "%PYTHON_INSTALLER%" /quiet InstallAllUsers=0 PrependPath=1 Include_test=0 Include_launcher=1
del "%PYTHON_INSTALLER%" >nul 2>&1

set "PYTHON_INSTALLED_BY_START=1"
set "PYTHON=%LocalAppData%\Programs\Python\Python312\python.exe"
if not exist "%PYTHON%" goto python_failed

call :is_supported_python "%PYTHON%"
if errorlevel 1 goto python_failed

goto create_venv

:create_venv
echo [INFO] Creating local Python environment...
"%PYTHON%" -m venv "%ROOT%\.venv"
if errorlevel 1 (
    if "%PYTHON_INSTALLED_BY_START%"=="0" (
        echo [!] Could not create a local environment with the current Python. Trying Python 3.12...
        goto install_python
    )
    goto venv_failed
)

set "PYTHON=%VENV_PYTHON%"
goto ensure_pip

:ensure_pip
"%PYTHON%" -m pip --version >nul 2>&1
if errorlevel 1 (
    echo [INFO] Preparing pip...
    "%PYTHON%" -m ensurepip --upgrade
)

"%PYTHON%" -m pip --version >nul 2>&1
if errorlevel 1 goto pip_failed

exit /b 0

:ensure_ffmpeg
ffmpeg -version >nul 2>&1
if not errorlevel 1 exit /b 0

if exist "%ROOT%\bin\ffmpeg.exe" exit /b 0
goto install_ffmpeg

:install_ffmpeg
echo [!] FFmpeg not found. Downloading it now to enable music playback...
echo This is a one-time setup step.

if not exist "%ROOT%\bin" mkdir "%ROOT%\bin"

echo Downloading FFmpeg 6.1.1...
curl -fL -o "%ROOT%\ffmpeg.7z" "https://www.videohelp.com/download/ffmpeg-6.1.1-full_build.7z?r=zndlFgBq"
if errorlevel 1 goto ffmpeg_failed

echo Extracting FFmpeg... (this might take a minute)
tar -xf "%ROOT%\ffmpeg.7z"
if errorlevel 1 goto ffmpeg_failed

echo Moving ffmpeg.exe to the bin folder...
for /r "%ROOT%" %%I in (ffmpeg.exe) do (
    if /i not "%%~fI"=="%ROOT%\bin\ffmpeg.exe" (
        copy /y "%%~fI" "%ROOT%\bin\ffmpeg.exe" >nul 2>&1
        if not errorlevel 1 goto ffmpeg_found
    )
)

:ffmpeg_found
if not exist "%ROOT%\bin\ffmpeg.exe" goto ffmpeg_failed

echo Cleaning up temporary files...
del "%ROOT%\ffmpeg.7z" >nul 2>&1
for /d %%D in ("%ROOT%\ffmpeg-6.1.1-*") do rd /s /q "%%D" >nul 2>&1

echo FFmpeg installed successfully!
echo.
exit /b 0

:ensure_deps
call :check_required_imports
if errorlevel 1 goto install_deps

"%PYTHON%" -m pip install -r "%ROOT%\requirements.txt" --quiet --disable-pip-version-check --no-warn-script-location
if errorlevel 1 goto install_deps

call :check_required_imports
if not errorlevel 1 (
    "%PYTHON%" -m playwright install >nul 2>&1
    exit /b 0
)
goto install_deps

:check_required_imports
"%PYTHON%" -c "import cachetools, discord, dotenv, lyricsgenius, nacl, psutil, requests, spotipy, syncedlyrics, yt_dlp; from playwright.async_api import async_playwright; from spotify_scraper import SpotifyClient" >nul 2>&1
exit /b %ERRORLEVEL%

:install_deps
echo [!] Dependencies missing. Installing now (this may take a minute)...
echo [INFO] Using "%PYTHON%"

"%PYTHON%" -m pip install --upgrade pip setuptools wheel --disable-pip-version-check --no-warn-script-location
if errorlevel 1 goto deps_failed

"%PYTHON%" -m pip install -r "%ROOT%\requirements.txt" --disable-pip-version-check --no-warn-script-location
if errorlevel 1 goto deps_failed

call :check_required_imports
if errorlevel 1 goto deps_failed

"%PYTHON%" -m playwright install >nul 2>&1
exit /b 0

:missing_entrypoint
echo [ERROR] playify.py was not found next to start.bat.
echo Make sure start.bat is inside the Playify folder you extracted.
goto fail

:missing_requirements
echo [ERROR] requirements.txt was not found next to start.bat.
echo Make sure the Playify folder was extracted completely.
goto fail

:python_failed
echo [ERROR] Python could not be installed automatically.
echo Please install Python 3.12 from https://www.python.org/downloads/ and run start.bat again.
exit /b 1

:venv_failed
echo [ERROR] Could not create Playify's local Python environment.
echo Please install Python 3.12 from https://www.python.org/downloads/ and run start.bat again.
exit /b 1

:pip_failed
echo [ERROR] pip could not be prepared for this Python installation.
echo Please install Python 3.12 from https://www.python.org/downloads/ and run start.bat again.
exit /b 1

:ffmpeg_failed
echo [ERROR] FFmpeg could not be installed automatically.
echo Please check your internet connection, then run start.bat again.
exit /b 1

:deps_failed
echo [ERROR] Python dependencies could not be installed.
echo The full pip output above should explain what failed.
exit /b 1

:fail
echo.
pause
exit /b 1
