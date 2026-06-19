---
title: TUI Dashboard & Settings
description: Keyboard shortcuts and advanced features of the Terminal Dashboard.
---

Playify V2 features a highly robust **Terminal User Interface (TUI)** built with Python's Rich library. It handles much more than just logs - it's a full control center for your bot instance.

When you run `start.bat` or `start.sh`, the dashboard automatically launches in your terminal.

## Keyboard Shortcuts

The dashboard is fully interactive. Use the following keys to control the bot:

| Key | Action | Description |
| :--- | :--- | :--- |
| **`L`** | **Full Logs** | Switches to full-screen log mode. You can use the `Up` and `Down` arrows to scroll through the entire session history. Press `L` again to exit. |
| **`C`** | **Configuration** | Relaunches the initial Setup Wizard. This allows you to safely update your Discord Token or Spotify Keys without breaking your database. |
| **`S`** | **Settings Menu** | Opens the interactive settings menu to change the bot's Discord Presence (Playing/Listening), default volume, and UI aesthetics on the fly. |
| **`U`** | **Auto-Updater** | Pulls the latest `.zip` release directly from the GitHub `main` branch, extracts it, and hot-swaps the code safely. |
| **`R`** | **Restart Bot** | Restarts the background Python process without closing the terminal. Perfect if the bot crashes. |
| **`Q`** | **Quit Safely** | Safely shuts down the bot, explicitly killing any zombie `FFmpeg` children processes before exiting. |

## Dashboard Metrics

- **FFmpeg Processes:** Tracks how many audio streams are currently being transcoded. If this number is suspiciously high but no music is playing, a restart might be needed.
- **Crashes:** The dashboard specifically intercepts exit codes. If the bot crashes, it won't just close your terminal; it will catch the error, show you the code, and give you the option to hit `R` to restart immediately.
- **Memory & Uptime:** Real-time tracking of the exact memory footprint.
