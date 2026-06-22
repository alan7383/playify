---
title: Spotify integration
description: How Playify handles Spotify links natively without requiring API keys.
---

Playify has first-class support for Spotify. You can play **tracks, albums, and playlists** directly by pasting a Spotify URL into the `/play` command.

Unlike most Discord music bots, **Playify works with Spotify out of the box with zero configuration.**

## How it works (SpotifyScraper)

Playify achieves this by utilizing a powerful external library called [**SpotifyScraper**](https://github.com/AliAkhtari78/SpotifyScraper). 

Thanks to this library, Playify can fetch metadata for any public Spotify link entirely bypassing the official API. 

### What does this mean for you?
- **No client ID required:** you do not need to create a developer account on Spotify.
- **No client secret required:** you don't have to manage or rotate API tokens.
- **Privacy & simplicity:** just paste your link and Playify handles the rest.

*Note: Playify uses this metadata to find the exact matching audio track on YouTube or SoundCloud, ensuring high-quality playback.*

## Official API (optional for power users)

While the scraper works perfectly for 99% of use cases, Playify still supports the official Spotify API via `spotipy`. 

If you frequently load **massive playlists (1000+ songs)**, using the official API might be slightly faster.

**How to enable the official API:**
1. Go to the [Spotify Developer dashboard](https://developer.spotify.com/dashboard/) and create an app.
2. Open your Playify dashboard.
3. Press `C` to run the configuration wizard, or manually edit your `.env` file.
4. Add your `SPOTIFY_CLIENT_ID` and `SPOTIFY_CLIENT_SECRET`.

If the official API ever fails or hits a rate limit, Playify's cascade architecture will **automatically fall back to SpotifyScraper**, ensuring your music never stops!
