<p align="center">
  <img src="https://github.com/user-attachments/assets/5c1d5fba-3a34-4ffe-bd46-ef68e1175360" alt="Playify Banner" width="900">
</p>

<h1 align="center">Playify ♪</h1>

<p align="center">
  <a href="https://github.com/alan7383/playify/blob/main/LICENSE">
    <img src="https://img.shields.io/github/license/alan7383/playify?style=for-the-badge&logo=github" alt="License">
  </a>
  <img src="https://img.shields.io/badge/python-3.9+-blue?style=for-the-badge&logo=python&logoColor=white" alt="Python 3.9+">
  <img src="https://img.shields.io/badge/Discord-bot-5865F2?style=for-the-badge&logo=discord&logoColor=white" alt="Discord">
</p>

<p align="center">
  <strong>A minimalist, self-hosted Discord music bot. No ads, no premium tiers, no limits — just music.</strong>
</p>

---

### ~ what is this

Playify is an open-source Discord music bot built for simplicity. No web UI, no paywalls, no account required — just slash commands and music.

It supports **YouTube, YouTube Music, SoundCloud, Twitch, Spotify, Deezer, Bandcamp, Apple Music, Tidal, Amazon Music, direct audio links, and local files**.

Type `/play <url or query>` and let it run.

---

### * features

<details open>
<summary><b>~ sources & playback</b></summary>

* Play from **10+ sources**: YouTube • YouTube Music • SoundCloud • Twitch • Spotify • Deezer • Bandcamp • Apple Music • Tidal • Amazon Music
* **Direct audio links**: stream any public MP3, FLAC, WAV, or audio URL
* **Local file playback**: upload and play your own audio/video files directly
* **Autoplay** of similar tracks via YouTube Mix and SoundCloud Stations
* **Loop** and **shuffle** queue controls
* Audio **filters**: slowed, reverb, bass boost, nightcore, and more
</details>

<details>
<summary><b>> spotify support</b></summary>

* ✅ Individual tracks
* ✅ Personal and public playlists
* ✅ Spotify-curated mixes (*Release Radar*, *Your Mix*) via [SpotifyScraper](https://github.com/AliAkhtari78/SpotifyScraper), bypassing API limits

> Dynamic radios and mixes may vary from your Spotify app — they update constantly.
</details>

<details>
<summary><b>+ extras</b></summary>

* **Lyrics** fetching and display for the current track
* **Karaoke mode** with synced lyrics
* **24/7 mode** to keep the bot in a channel permanently
* **Kawaii Mode** — toggle cute kaomoji responses with `/kaomoji`
* Detailed **status and performance** stats with `/status`
* **Interactive queue pages**, track removal menus, and a seek interface
</details>

---

### > install

You can run Playify in two ways. Docker is recommended — it handles all dependencies automatically.

<details open>
<summary><b>🐳 Method 1: Docker (recommended)</b></summary>

```bash
git clone https://github.com/alan7383/playify.git
cd playify
cp .env.example .env
```

Edit `.env` and fill in your tokens:

```ini
DISCORD_TOKEN=your_discord_bot_token
SPOTIFY_CLIENT_ID=your_spotify_client_id
SPOTIFY_CLIENT_SECRET=your_spotify_client_secret
GENIUS_TOKEN=your_genius_api_token
```

Then start the bot:

```bash
docker compose up -d --build
```

View logs with `docker compose logs -f`.
</details>

<details>
<summary><b>🛠️ Method 2: Manual setup</b></summary>

**Requirements:**
* Python 3.9+
* FFmpeg **6.1.1** in your system PATH — [download here](https://www.videohelp.com/software?d=ffmpeg-6.1.1-full_build.7z)
* Git

```bash
git clone https://github.com/alan7383/playify.git
cd playify
pip install -r requirements.txt
playwright install
cp .env.example .env
```

Edit `.env` with your tokens, then run:

```bash
python playify.py
```

**On Windows**, you can skip all of this — just double-click **`start.bat`**. It automatically handles Python installation, FFmpeg download, dependency setup, and `.env` configuration through an interactive prompt. No command line knowledge needed.
</details>

**Inviting the bot to your server:**
* Open your Discord Developer Portal and enable the **Guilds**, **Voice States**, and **Message Content** intents.
* Generate an invite link with `Connect`, `Speak`, and `Send Messages` permissions.

---

### # commands

| Command | Description |
| :--- | :--- |
| `/play <url/query>` | Add a song or playlist. Supports direct audio links. |
| `/search <query>` | Search and choose from the top results. |
| `/play-files <file(s)>` | Play one or more uploaded audio/video files. |
| `/playnext <query/file>` | Add a song to the front of the queue. |
| `/pause` / `/resume` | Pause or resume playback. |
| `/skip` | Skip the current track. |
| `/stop` | Stop playback, clear queue, disconnect. |
| `/nowplaying` | Show current track info. |
| `/seek` | Interactive seek, fast-forward, or rewind menu. |
| `/queue` | Show the queue with interactive pages. |
| `/remove` | Open a menu to remove tracks from the queue. |
| `/shuffle` | Shuffle the queue. |
| `/clearqueue` | Clear all songs from the queue. |
| `/loop` | Toggle looping for the current track. |
| `/autoplay` | Toggle autoplay when the queue ends. |
| `/24_7 <mode>` | Keep the bot in the channel (`normal`, `auto`, `off`). |
| `/filter` | Apply real-time audio filters. |
| `/lyrics` | Fetch and display lyrics for the current song. |
| `/karaoke` | Start a karaoke session with synced lyrics. |
| `/reconnect` | Refresh the voice connection without losing your place. |
| `/status` | Show bot performance and resource usage. |
| `/kaomoji` | Toggle cute kaomoji responses. `(ADMIN)` |

---

### % public bot status

**The public bot is currently discontinued.**

Due to stricter limitations from Google and YouTube, the public instance was consistently detected and blocked. Maintaining a public bot across thousands of servers became unfeasible.

Please **self-host** using one of the methods above — it's safer and won't be interrupted.

---

### @ troubleshooting

* **FFmpeg not found** — ensure FFmpeg **6.1.1** is installed and in your PATH. On Windows, the bot looks for it in the `bin/` folder if not in PATH. Docker handles this automatically.
* **Spotify errors** — check your `SPOTIFY_CLIENT_ID` and `SPOTIFY_CLIENT_SECRET` in `.env`.
* **Bot offline or unresponsive** — verify your `DISCORD_TOKEN` and bot permissions in the Developer Portal.
* **Direct link not working** — the URL must point directly to an audio file and be publicly accessible.

---

### ~ privacy

* **Self-hosted only**: all logs stay local to your machine. No telemetry is sent anywhere.

---

### + under the hood

* **Python**
* **discord.py**
* **yt-dlp**
* **FFmpeg**
* **Playwright**
* **SpotifyScraper**

---

### * contributing

Bugs, features, pull requests — all welcome.

* **Found a bug?** Open an Issue.
* **Want a feature?** Fork the repo and open a Pull Request.
* **Like the project?** Star the repository!

---

### ~ license

MIT License — do what you want with the code, just be kind.

---

<p align="center">
  made with ☕ and love by <a href="https://github.com/alan7383">alan7383</a>
</p>