@echo off
setlocal EnableDelayedExpansion
title Playify v3 (Rust)
cd /d "%~dp0"

echo.
echo   ^> Playify v3 launcher (full Rust)
echo.

REM --- 1. Discord token -----------------------------------------------------
if not exist ".env" (
    echo   No .env found. Let's create one.
    set /p TOKEN="  Paste your Discord bot token: "
    (
        echo DISCORD_TOKEN=!TOKEN!
        echo PLAYIFY_RUST_NODE=0
    ) > .env
    echo   .env created.
    echo.
)

REM --- 2. yt-dlp (standalone exe, no Python needed) ---------------------------
set "YTDLP="
if exist ".venv\Scripts\yt-dlp.exe" set "YTDLP=.venv\Scripts\yt-dlp.exe"
if not defined YTDLP if exist "bin\yt-dlp.exe" set "YTDLP=bin\yt-dlp.exe"
if not defined YTDLP (
    where yt-dlp >nul 2>nul
    if not errorlevel 1 set "YTDLP=yt-dlp"
)
if not defined YTDLP (
    echo   yt-dlp not found: downloading the standalone build to bin\ ...
    if not exist "bin" mkdir bin
    powershell -NoProfile -Command "Invoke-WebRequest -Uri 'https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe' -OutFile 'bin\yt-dlp.exe'"
    if exist "bin\yt-dlp.exe" (
        set "YTDLP=bin\yt-dlp.exe"
        echo   yt-dlp installed in bin\.
    ) else (
        echo   [!] Could not download yt-dlp. Install it manually and retry.
        pause
        exit /b 1
    )
)
set "PLAYIFY_YTDLP=%CD%\%YTDLP%"

REM --- 3. Get the bot binary ----------------------------------------------------
REM Priority: local build > build from source (if repo present) > prebuilt download > bin\ copy.
set "BOT=playify-rs\target\release\playify-v3.exe"
if not exist "%BOT%" if exist "playify-rs\Cargo.toml" (
    echo   Source code detected, skipping prebuilt binary download...
) else if not exist "%BOT%" if exist "bin\playify-v3.exe" (
    set "BOT=bin\playify-v3.exe"
) else if not exist "%BOT%" (
    echo   Downloading the prebuilt Playify v3 binary...
    if not exist "bin" mkdir bin
    powershell -NoProfile -Command "try { Invoke-WebRequest -Uri 'https://github.com/alan7383/playify/releases/latest/download/playify-v3-windows-x86_64.exe' -OutFile 'bin\playify-v3.exe' } catch { exit 1 }"
    if exist "bin\playify-v3.exe" (
        set "BOT=bin\playify-v3.exe"
        echo   Prebuilt binary installed in bin\.
    ) else (
        echo   No prebuilt binary available: falling back to building from source.
    )
)
if not exist "%BOT%" (
    echo   Building playify-v3 - first build takes a few minutes...
    where cargo >nul 2>nul
    if errorlevel 1 (
        echo   [!] Rust is not installed. Installing via winget...
        winget install --id Rustlang.Rustup --silent --accept-package-agreements --accept-source-agreements
        echo   [!] Close this window and run start-v3.bat again so PATH refreshes.
        pause
        exit /b 1
    )
    where cmake >nul 2>nul
    if errorlevel 1 (
        echo   CMake missing - needed for the Opus build. Installing via winget...
        winget install --id Kitware.CMake --silent --accept-package-agreements --accept-source-agreements
        set "PATH=%ProgramFiles%\CMake\bin;%PATH%"
    )
    set "CMAKE_POLICY_VERSION_MINIMUM=3.5"
    pushd playify-rs
    cargo build --release -p playify-v3
    popd
    if not exist "%BOT%" (
        echo   [!] Build failed. Check the errors above.
        pause
        exit /b 1
    )
)

REM --- 4. Launch with the TUI dashboard ---------------------------------------
echo   Starting Playify v3 dashboard... - press Q to quit
"%BOT%" --tui
endlocal
