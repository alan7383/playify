<p align="center">
  <img src="https://github.com/user-attachments/assets/5c1d5fba-3a34-4ffe-bd46-ef68e1175360" alt="Playify Banner" width="900">
</p>

<h1 align="center">Playify V2</h1>

<p align="center">
  <a href="https://github.com/alan7383/playify/blob/main/LICENSE">
    <img src="https://img.shields.io/github/license/alan7383/playify?style=for-the-badge&logo=github" alt="License">
  </a>
  <img src="https://img.shields.io/badge/python-3.9+-blue?style=for-the-badge&logo=python&logoColor=white" alt="Python 3.9+">
  <img src="https://img.shields.io/badge/Discord-bot-5865F2?style=for-the-badge&logo=discord&logoColor=white" alt="Discord">
  <a href="https://alan7383.github.io/playify/">
    <img src="https://img.shields.io/badge/📖_Documentation-1B3A5C?style=for-the-badge" alt="Documentation">
  </a>
</p>

<p align="center">
  <strong>A minimalist, self-hosted Discord music bot with a powerful TUI Dashboard.</strong><br><br>
  <a href="https://alan7383.github.io/playify/"><strong>Explore the Official Documentation »</strong></a>
</p>

---

### ~ v2 update

Playify has been completely rewritten to provide a seamless, robust, and beautiful experience directly from your terminal.

* **Interactive TUI dashboard**: Monitor resources, queue length, active players, and logs in real-time through a beautiful ASCII interface.
* **Built-in auto-updater**: Keep your bot up to date directly from the dashboard. No `git pull` or manual downloads required.
* **One-click installer**: Run the setup script and let Playify automatically install Python, download FFmpeg, and set up your `.env` configuration.
* **In-app settings menu**: Configure your bot's Discord presence, default volumes, and UI customization without touching a text file.

<p align="center">
  <img src="assets/dashboard_preview.png" alt="Playify TUI Dashboard" width="900">
</p>

---

### ~ What is this

Playify is an open-source Discord music bot built for simplicity. No web UI, no paywalls, no account required -- just slash commands and music.

It supports **YouTube, YouTube Music, SoundCloud, Twitch, Spotify, Deezer, Bandcamp, Apple Music, Tidal, Amazon Music, direct audio links, and local files**.

Type `/play <url or query>` and let it run.

---

### * Features

<details open>
<summary><b>~ Sources & Playback</b></summary>

* Play from **10+ sources**: YouTube, SoundCloud, Twitch, Spotify, Apple Music, etc.
* **Direct audio links**: stream any public MP3, FLAC, WAV, or audio URL.
* **Local file playback**: upload and play your own audio/video files directly.
* **Autoplay** of similar tracks via YouTube Mix and SoundCloud Stations.
* **Loop** and **shuffle** queue controls.
* Audio **filters**: slowed, reverb, bass boost, nightcore, and more.
</details>

<details>
<summary><b>> Spotify Support</b></summary>

* Individual tracks.
* Personal and public playlists.
* Spotify-curated mixes (Release Radar, Your Mix) via SpotifyScraper, bypassing API limits.
</details>

<details>
<summary><b>+ Extras</b></summary>

* **Lyrics** fetching and display for the current track.
* **Karaoke mode** with synced lyrics.
* **24/7 mode** to keep the bot in a channel permanently.
* **Kawaii Mode** -- toggle cute kaomoji responses with `/kaomoji`.
* **Interactive queue pages**, track removal menus, and a seek interface.
</details>

---

### > Install

*For the complete installation guide (including Docker configurations), please visit the [Installation Documentation](https://alan7383.github.io/playify/getting-started/installation/).*

<details open>
<summary><b>[ Windows - Recommended ]</b></summary>

1. Download the repository as a ZIP and extract it, or clone it via git.
2. Double-click `start.bat`.
3. The Playify installer will automatically install Python, download FFmpeg, and prompt you for your Discord Token.
4. The TUI Dashboard will launch automatically.
</details>

<details>
<summary><b>[ Linux ]</b></summary>

Playify natively supports Linux with an automated setup script.

1. Clone the repository: `git clone https://github.com/alan7383/playify.git`
2. Enter the directory: `cd playify`
3. Run the bootstrapper: `bash start.sh`
4. The script will set up your virtual environment, automatically download a local copy of FFmpeg, and launch the Dashboard.
</details>

---

### # Commands

*For a detailed explanation of each feature, check out the [Commands Reference](https://alan7383.github.io/playify/playback-and-features/commands-reference/).*

| Command | Description |
| :--- | :--- |
| `/play <url/query>` | Add a song or playlist. Supports direct audio links. |
| `/search <query>` | Search and choose from the top results. |
| `/play-files <file(s)>` | Play one or more uploaded audio/video files. |
| `/queue` | Show the queue with interactive pages. |
| `/lyrics` | Fetch and display lyrics for the current song. |
| `/karaoke` | Start a karaoke session with synced lyrics. |
| `/24_7 <mode>` | Keep the bot in the channel (`normal`, `auto`, `off`). |
| `/reconnect` | Refresh the voice connection without losing your place. |

*(And many more...)*

---

### @ Troubleshooting

* **FFmpeg not found** -- The Windows `start.bat` handles this automatically. For manual setups, ensure FFmpeg 6.1.1 is in your PATH or `bin/` folder.
* **Spotify errors** -- check your `SPOTIFY_CLIENT_ID` and `SPOTIFY_CLIENT_SECRET` in `.env`.
* **Bot offline or unresponsive** -- verify your `DISCORD_TOKEN` and bot permissions in the Developer Portal.

👉 **Need more help? Check out our detailed [FAQ & Troubleshooting guide](https://alan7383.github.io/playify/troubleshooting/faq/).**

---

### + Under the hood

* **Python** & **discord.py**
* **yt-dlp** & **FFmpeg**
* **Rich** (TUI Dashboard)
* **Playwright** & **SpotifyScraper**

---

### * Contributing

Bugs, features, pull requests -- all welcome.

* **Found a bug?** Open an Issue.
* **Want a feature?** Fork the repo and open a Pull Request.
* **Like the project?** Star the repository!

---

### ~ Privacy & License

* **Self-hosted only**: all logs stay local to your machine. No telemetry is sent anywhere.
* MIT License -- do what you want with the code, just be kind.

---

<p align="center">
  made with love by <a href="https://github.com/alan7383">alan7383</a>
</p>