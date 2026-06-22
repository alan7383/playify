---
title: TUI dashboard & settings
description: Keyboard shortcuts and advanced features of the terminal dashboard.
---

Playify V2 features a highly robust **terminal user interface (TUI)** built with Python's Rich library. It handles much more than just logs - it's a full control center for your bot instance.

When you run `start.bat` or `start.sh`, the dashboard automatically launches in your terminal.

## Keyboard shortcuts

The dashboard is fully interactive. Use the following keys to control the bot:

| Key | Action | Description |
| :--- | :--- | :--- |
| **`L`** | **Full logs** | Switches to full-screen log mode. You can use the `Up` and `Down` arrows to scroll through the entire session history. Press `L` again to exit. |
| **`C`** | **Configuration** | Relaunches the initial setup wizard. This allows you to safely update your Discord token or Spotify keys without breaking your database. |
| **`S`** | **Settings menu** | Opens the interactive settings menu to change the bot's Discord presence (playing/listening), default volume, and UI aesthetics on the fly. |
| **`U`** | **Auto-updater** | Pulls the latest `.zip` release directly from the GitHub `main` branch, extracts it, and hot-swaps the code safely. |
| **`R`** | **Restart bot** | Restarts the background Python process without closing the terminal. Perfect if the bot crashes. |
| **`Q`** | **Quit safely** | Safely shuts down the bot, explicitly killing any zombie `FFmpeg` children processes before exiting. |

## Dashboard metrics

- **FFmpeg processes:** tracks how many audio streams are currently being transcoded. If this number is suspiciously high but no music is playing, a restart might be needed.
- **Crashes:** the dashboard specifically intercepts exit codes. If the bot crashes, it won't just close your terminal; it will catch the error, show you the code, and give you the option to hit `R` to restart immediately.
- **Memory & uptime:** real-time tracking of the exact memory footprint.
