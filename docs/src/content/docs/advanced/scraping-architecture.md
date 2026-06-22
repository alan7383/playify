---
title: Scraping Architecture
description: Dropping Playwright and moving to pure HTTP extraction.
---

Playify used to rely on Playwright for scraping protected music platforms like Apple Music, Amazon Music, and Tidal. It got the job done, but it was incredibly heavy. I had to spawn a full headless Chromium browser in the background just to scrape a tracklist. 

This meant waiting for JavaScript payloads to execute, writing code to hunt down and click cookie banners, and forcing the browser to scroll down iteratively to trigger lazy-loading on large playlists. It was slow (often taking up to 15 seconds before a song would even start), consumed way too much RAM for a simple Discord bot, and was extremely fragile. If a platform changed a CSS class or updated their UI, the scraper would break.

I completely rewrote the extraction engine to use `aiohttp`. Now, I bypass the frontend entirely and talk directly to the internal APIs or grab the server-side rendered data. It's basically instant and uses zero extra memory.

Here is a breakdown of how I reverse-engineered the platforms to make this work.

## Apple Music

Apple Music was surprisingly straightforward because they use server-side rendering. When you make a standard GET request to an Apple Music URL, the server responds with a complete HTML page that already has the initial application state injected into it.

I don't need to run any JavaScript. Inside the raw HTML, there's a specific `<script type="application/json" id="serialized-server-data">` tag. I just pull that block out with a regex and parse the JSON. From there, it's just a matter of digging through the `trackLockup` objects to grab the track titles, artist names, and identifiers.

## Amazon Music

Amazon Music was much harder. They use an internal API that actively rejects simple anonymous requests. To get around this, I have to mimic a real web player session.

First, I hit `https://music.amazon.fr/config.json`. This endpoint is meant to configure the web player, but it handily gives me everything I need to bypass their protection: a `deviceId`, a `sessionId`, and a CSRF object containing security tokens and nonces. I also save the cookies from this request.

Once I have those session tokens, I craft a POST request to their internal `showHome` API. I stuff the headers with the tokens I just grabbed (especially the `x-amzn-csrf` header) and send a `deeplink` payload pointing to the playlist or album I want. The API thinks I'm a legitimate browser session and hands back a massive JSON tree containing the entire page structure, which I then recursively search to extract the tracks.

## Tidal

Tidal has a well-known public API, but it typically requires OAuth2 authentication. However, their web player lets unauthenticated users browse playlists and albums, meaning there had to be a backdoor.

After digging through the network traffic of an anonymous web session, I noticed that the web player sends a hardcoded, static public token (`txNoH4kkV41MfH25`) in its headers. 

I simply took that static token and injected it into my own `x-tidal-token` header. Now, I can query their endpoints directly. For example, hitting `https://tidal.com/v1/playlists/{id}/items` with that token returns the raw JSON metadata immediately. For playlists over 100 tracks, I just use the API's built-in `offset` and `limit` parameters to paginate through the results automatically.
