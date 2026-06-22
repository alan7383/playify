---
title: Commands reference
description: Exhaustive list of Playify's slash commands.
---

Playify uses Discord's native slash commands. Type `/` in your server to see the full list of available commands.

## Playback

| Command | Description |
| :--- | :--- |
| `/play <url/query>` | Play a link or search for a song. |
| `/search <query>` | Search and choose from the top results. |
| `/play-files <file(s)>` | Plays one or more uploaded audio or video files. |
| `/playnext <query/file>` | Add a song or a local file to play next. |
| `/previous` | Plays the previous song in the history. |

## Controls

| Command | Description |
| :--- | :--- |
| `/pause` | Pause the current playback. |
| `/resume` | Resume the playback. |
| `/skip` | Skip the current track. |
| `/stop` | Stop playback and disconnect the bot. |
| `/seek` | Interactive seek, fast-forward, or rewind menu. |
| `/jumpto` | Opens a menu to jump to a specific song in the queue. |
| `/volume` | Adjusts the music volume for everyone (0-200%). |
| `/reconnect` | Refresh the voice connection without losing your place. |

## Queue & info

| Command | Description |
| :--- | :--- |
| `/nowplaying` | Show the current song playing. |
| `/queue` | Show the current song queue and status with pages. |
| `/remove` | Open a menu to remove tracks from the queue. |
| `/shuffle` | Shuffle the current queue. |
| `/clearqueue` | Clear the current queue. |
| `/loop` | Enable/disable looping. |

## Features & modes

| Command | Description |
| :--- | :--- |
| `/autoplay` | Enable/disable autoplay of similar songs. |
| `/24_7 <mode>` | Enable or disable 24/7 mode (`normal`, `auto`, `off`). |
| `/filter` | Applies or removes audio filters in real time. |
| `/lyrics` | Get song lyrics from Genius. |
| `/karaoke` | Start a synced karaoke-style lyrics display. |

## Admin & setup

| Command | Description |
| :--- | :--- |
| `/setup controller` | Sets or updates the channel for the persistent music controller. `(ADMIN)` |
| `/setup allowlist` | Restricts bot commands to specific channels. `(ADMIN)` |
| `/kaomoji` | Enable/disable kawaii mode. `(ADMIN)` |

## Misc

| Command | Description |
| :--- | :--- |
| `/status` | Displays the bot's system status and metrics. |
| `/support` | Shows ways to support the creator of Playify. |
