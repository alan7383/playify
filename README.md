<h1 align="center">Playify ♪(｡◕‿◕｡)</h1>

<p align="center">
  <img src="https://github.com/user-attachments/assets/5c1d5fba-3a34-4ffe-bd46-ef68e1175360" alt="Playify Banner" width="900">
</p>

---

<p align="center">
  <img src="https://img.shields.io/github/license/alan7383/playify.svg" alt="GitHub license" />
  <img src="https://img.shields.io/badge/python-3.9+-blue.svg" alt="Python 3.9+" />
</p>

<div align="center">
  <blockquote>
    <h3>(ﾉ´ヮ`)ﾉ*: ･ﾟ I AM BACK!</h3>
    <p><b>I am really sorry for disappearing without saying goodbye! (｡•́︿•̀｡)</b><br>
    To be honest, I just got really tired of fighting against Google constantly blocking the bot. It was super annoying and exhausting, so I decided to stop everything overnight out of frustration.<br>
    But I've rested, and <b>I am back to maintaining the project!</b> The repo is unarchived, and I am open to contributions again. Thank you for waiting! (｡♥‿♥｡)</p>
  </blockquote>
</div>

---

## Table of Contents

* [What is Playify?](#what-is-playify)
* [Spotify Support](#spotify-support)
* [Key Features](#key-features)
* [Installation](#installation)
* [Public Bot Status](#public-bot)
* [Command Reference](#command-reference)
* [Troubleshooting](#troubleshooting)
* [Privacy & Data](#privacy--data)
* [Contributing](#contributing)
* [License](#license)

---


> **Note:** The Playify Windows Edition (standalone setup) has been discontinued and is no longer updated or supported. Please use the Docker or Manual methods below to run Playify.

<a id="what-is-playify"></a>
## ＼(＾O＾)／ What is Playify?

Playify is the ultimate minimalist Discord music bot—no ads, no premium tiers, no limits, just music and kawaii vibes!

* **No web UI**: Only simple slash commands.
* **100% free**: All features unlocked for everyone.
* **Unlimited playback**: Giant playlists, endless queues, eternal tunes!

**Supports YouTube, YouTube Music, SoundCloud, Twitch, Spotify, Deezer, Bandcamp, Apple Music, Tidal, Amazon Music, direct audio links, and local files.**
Type `/play <url or query>` and let the music flow~

<a id="spotify-support"></a>
## (＾◡＾) Spotify Support

* ✅ Individual tracks
* ✅ Personal & public playlists
* ✅ Spotify-curated mixes (e.g., *Release Radar*, *Your Mix*) via [SpotifyScraper](https://github.com/AliAkhtari78/SpotifyScraper)—bypasses API limits!

> *Note:* Dynamic Spotify radios/mixes may vary from your app—they update constantly.

<a id="key-features"></a>
## (≧◡≦) Key Features

* Play from **10+ sources**: YouTube • SoundCloud • Twitch • Spotify • Deezer • Bandcamp • Apple Music • Tidal • Amazon Music • **Direct Audio Links** • **Local Files**
* Slash commands: `/play`, `/search`, `/pause`, `/skip`, `/queue`, `/remove`, + more!
* **Play Local Files**: Directly upload and play your own audio/video files.
* **Direct Audio Links**: Stream music directly from any audio URL (MP3, FLAC, WAV, etc.)
* **Autoplay** of similar tracks (YouTube Mix, SoundCloud Stations)
* **Loop** & **shuffle** controls
* **Kawaii Mode** toggles cute kaomoji responses (`/kaomoji`)
* Audio **filters**: slowed, reverb, bass boost, nightcore, and more
* Powered by `yt-dlp`, `FFmpeg`, `asyncio`, and a dash of chaos

<a id="installation"></a>
## (＾∀＾) Installation

You can run Playify in two ways. The Docker method is recommended for most users as it's simpler and manages all dependencies for you.

### (🐳) Method 1: Docker Setup (Recommended)

This is the easiest way to get the bot running.

1.  **Clone the repository and enter it:**
    ```bash
    git clone [https://github.com/alan7383/playify.git](https://github.com/alan7383/playify.git)
    cd playify
    ```
2.  **Create your secret file:**
    Copy the example file to create your own configuration.
    ```bash
    cp .env.example .env
    ```
    Now, **edit the `.env` file** and fill in your tokens.
    ```ini
    DISCORD_TOKEN=your_discord_bot_token
    SPOTIFY_CLIENT_ID=your_spotify_client_id
    SPOTIFY_CLIENT_SECRET=your_spotify_client_secret
    GENIUS_TOKEN=your_genius_api_token
    ```
3.  **Fire it up!**
    This command will build the container and run the bot in the background.
    ```bash
    docker compose up -d --build
    ```
    To view the bot's logs, use `docker compose logs -f`.

### (🛠️) Method 2: Manual Setup

> **Windows User?** Don't know anything about code? Read the [Absolute Beginner's Windows Guide](docs/windows_beginner_guide.md) created just for you! It includes a 1-click startup script.

**Requirements:**
* Python 3.9+
* FFmpeg **6.1.1** installed & in your system's PATH  
  ⬇ [Direct download (recommended version)](https://www.videohelp.com/software?d=ffmpeg-6.1.1-full_build.7z)
* Git

**Steps:**
1.  Clone the repo:
    ```bash
    git clone [https://github.com/alan7383/playify.git](https://github.com/alan7383/playify.git)
    cd playify
    ```
2.  Install dependencies:
    ```bash
    pip install -r requirements.txt
    playwright install
    ```
3.  Copy & configure environment:
    ```bash
    cp .env.example .env
    ```
    **Edit the `.env` file** with your tokens as shown in the Docker method.
4.  Run the bot:
    ```bash
    python playify.py
    ```

### Inviting the Bot to Discord (for both methods)
* Go to your Discord Developer Portal.
* Enable the **Guilds**, **Voice States**, and **Message Content** intents for your bot.
* Generate an invite link with the `Connect`, `Speak`, and `Send Messages` permissions.
* Add the bot to your server and enjoy `/play`!

---


<a id="public-bot"></a>
## (╥_╥) Public Bot Status

**The public bot is currently discontinued.**

Unfortunately, due to stricter limitations from Google and YouTube, the public instance was detected as a bot and blocked. Maintaining a public instance that serves thousands of servers became impossible without hitting these bans constantly.

To use Playify, please **self-host** it using the [Docker](#installation) or manual methods above! It's much safer and ensures your music won't be interrupted.

---

<a id="command-reference"></a>
## (⊙‿⊙) Command Reference

| Command | Description |
| :--- | :--- |
| `/play <url/query>` | Add a song or playlist from a link/search. Supports direct audio links! |
| `/search <query>` | Searches for a song and lets you choose from the top results. |
| `/play-files <file1...>` | Play one or more uploaded audio/video files. |
| `/playnext <query/file>` | Add a song or local file to the front of the queue. |
| `/pause` | Pause playback. |
| `/resume` | Resume playback. |
| `/skip` | Skip the current track. Replays the song if loop is enabled. |
| `/stop` | Stop playback, clear queue, and disconnect. |
| `/nowplaying` | Display the current track's information. |
| `/seek` | Opens an interactive menu to seek, fast-forward, or rewind. |
| `/queue` | Show the current song queue with interactive pages. |
| `/remove` | Open a menu to remove specific songs from the queue. |
| `/shuffle` | Shuffle the queue. |
| `/clearqueue` | Clear all songs from the queue. |
| `/loop` | Toggle looping for the current track. |
| `/autoplay` | Toggle autoplay of similar songs when the queue ends. |
| `/24_7 <mode>` | Keep the bot in the channel (`normal`, `auto`, or `off`). |
| `/filter` | Apply real-time audio filters (nightcore, bassboost...). |
| `/lyrics` | Fetch and display lyrics for the current song. |
| `/karaoke` | Start a karaoke session with synced lyrics. |
| `/reconnect` | Refresh the voice connection to fix lag without losing your place. |
| `/status` | Show the bot's detailed performance and resource usage. |
| `/kaomoji` | Toggle cute kaomoji responses. `(ADMIN)` |

<a id="troubleshooting"></a>
## (｀・ω・´) Troubleshooting

* **FFmpeg not found**: Ensure FFmpeg **6.1.1** is installed & in your system's PATH. (Docker setup handles this for you!)  
  ⬇ [Download FFmpeg 6.1.1](https://www.videohelp.com/software?d=ffmpeg-6.1.1-full_build.7z)
* **Spotify errors**: Verify your API credentials in the `.env` file.
* **Bot offline/unresponsive**: Check your `DISCORD_TOKEN` and bot permissions in the Developer Portal.
* **Direct link issues**: Ensure the URL points directly to an audio file and is publicly accessible.

<a id="privacy--data"></a>
## (ﾉ◕ヮ◕)ﾉ Privacy & Data

* **Self-hosted**: All logs are local to your machine. No telemetry is sent.

<a id="contributing"></a>
## (ง＾◡＾)ง Contributing

Now that I am back, I am actively reviewing contributions!

* **Found a bug?** Open an Issue—I'm listening!
* **Want a new feature?** Fork the repo and open a Pull Request. All contributions are welcome!
* **Star the repository** if you enjoy using Playify!

<a id="license"></a>
## (＾ω＾) License

MIT License — do what you want with the code, just be kind!

<p align="center">
  Built with ☕ and love by <a href="https://github.com/alan7383">alan7383</a> (｡♥‿♥｡)
</p>
