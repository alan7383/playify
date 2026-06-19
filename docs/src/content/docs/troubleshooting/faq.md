---
title: FAQ & Troubleshooting
description: Advanced solutions to common issues.
---

## Why does the first Apple Music / Amazon Music track take a few seconds to load?

Playify relies on **Playwright** (a headless browser) to aggressively scrape metadata and streams from protected platforms like Apple Music, Tidal, and Amazon Music. 
The first time you play a track from these platforms, the bot has to spawn a hidden Chromium process in the background. Subsequent tracks will load much faster because the browser context is already warm.

## The music is lagging / stuttering

**Playify is heavily optimized and almost never lags the host machine.** 
The bot runs its FFmpeg audio processing with `IDLE_PRIORITY_CLASS` (on Windows) or `os.nice(19)` (on Linux/Mac) to ensure it yields CPU time to your other applications. 

If the audio is stuttering in Discord, it is almost **always** a network connection issue between the bot's server and Discord's voice servers.
1. Try changing the Voice Channel's region in Discord settings.
2. If the bot gets stuck in a "zombie state", use the `/reconnect` command.

## How does `/reconnect` work?

Discord's voice connection drops frequently, especially for bots running 24/7.
Playify's `/reconnect` command is special: it saves the exact millisecond (`seek_time`) of the current track, drops the connection entirely, reconnects, and resumes the audio *exactly* where it left off. It's the ultimate fix for audio desync or silence bugs without skipping the song.

## The bot says "FFmpeg is not found"

Playify normally downloads FFmpeg automatically. If it failed:
1. You can manually download `ffmpeg.exe` and place it in the `bin/` folder inside your Playify directory (create the `bin` folder if it doesn't exist).
2. [Download FFmpeg 6.1.1 here](https://www.videohelp.com/download/ffmpeg-6.1.1-full_build.7z?r=zndlFgBq) (extract and grab `ffmpeg.exe` from the `bin` folder).

## Do I need to enter my tokens every time?

Nope! The setup script saves your tokens in a `.env` file. The next time you want to start the bot, just double-click `start.bat` (or run `start.sh`). It will instantly start the bot without asking for tokens again.
