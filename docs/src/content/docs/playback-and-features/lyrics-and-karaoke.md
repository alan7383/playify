---
title: Lyrics & Karaoke
description: Fetching real-time lyrics and using the karaoke mode.
---

Playify tries to make things as simple as possible. By default, it uses **LRCLIB** to fetch lyrics and karaoke timings instantly, without needing any setup or API keys.

## Getting Lyrics (`/lyrics`)

While a song is playing, just run `/lyrics`. 
The bot will grab the lyrics and show them in a paginated embed. You can click through the pages to read along.

## Karaoke Mode (`/karaoke`)

For a more interactive vibe, use the `/karaoke` command. 
The bot will sync the lyrics with the current timestamp of the song, highlighting the current verse being sung in real time.

> [!WARNING]
> Karaoke mode edits the discord message constantly. If you spam it on a massive server, you might hit Discord's rate limits. Just a heads up.

---

## Genius API Fallback (Optional)

Sometimes LRCLIB doesn't have the lyrics for more obscure tracks. You can set up a Genius API token as a backup. If LRCLIB fails, Playify will automatically search Genius instead.

### Setting up the Token

1. Go to the [Genius API Clients Page](https://genius.com/api-clients/new) (create a free account if needed).
2. Fill out the form:
   - **App Name**: `Playify` (or whatever you prefer)
   - **App Website URL**: `https://github.com/alan7383/playify`
   - **Redirect URI**: `http://localhost`
3. Click **Save**, then **Generate Access Token**.
4. Paste this token into your `.env` file or give it to the TUI Setup Wizard.
   
   ```env
   GENIUS_TOKEN=your_token_here
   ```
