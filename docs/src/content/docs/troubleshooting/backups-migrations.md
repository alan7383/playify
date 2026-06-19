---
title: Backups & Migrations
description: How to safely move Playify to a new server without losing data.
---

Playify is completely portable. It doesn't rely on complex external databases like PostgreSQL or Redis. Everything is stored locally.

If you are moving Playify to a new computer, a VPS, or a Docker container, you only need to copy two specific files to retain all your configurations, queues, and server states.

## Important Files to Backup

1. **`data/playify_state.db`**
   This is a robust **SQLite database**. It contains:
   - The Persistent Controller channel and message IDs for every server.
   - The current song queues and histories for all active players.
   - Specific server settings (like Allowlist channels or Kawaii mode).
   - If you lose this file, you will need to re-run `/setup controller` in all your servers.

2. **`data/settings.json`**
   This JSON file stores your TUI Dashboard settings, such as:
   - Your preferred Discord Presence (e.g., "Playing /help").
   - The default volume.
   - UI Theme configurations.

3. **`.env`** (Optional but Recommended)
   Contains your Discord, Spotify, and Genius tokens. If you don't copy this, you will simply have to re-enter them in the setup wizard on your new machine.

## How to Migrate

1. Stop the bot gracefully on your old server (Press `Q` in the TUI to ensure the SQLite database is closed cleanly and not corrupted).
2. Zip or copy the `data/` folder and your `.env` file.
3. Install Playify on your new server following the [Installation Guide](/getting-started/installation).
4. *Before* running the `start.bat` or `start.sh` script, paste the `data/` folder and `.env` file into the new Playify root directory.
5. Start the bot. It will instantly resume its previous state!
