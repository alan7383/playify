<p align="center">
  <img src="https://github.com/user-attachments/assets/5c1d5fba-3a34-4ffe-bd46-ef68e1175360" alt="Playify Banner" width="900">
</p>

<h1 align="center">Playify V3 — Rust Edition</h1>

<p align="center">
  <a href="https://github.com/alan7383/playify/blob/main/LICENSE">
    <img src="https://img.shields.io/github/license/alan7383/playify?style=for-the-badge&logo=github" alt="License">
  </a>
  <img src="https://img.shields.io/badge/rust-1.74+-orange?style=for-the-badge&logo=rust&logoColor=white" alt="Rust">
  <img src="https://img.shields.io/badge/RAM-~19%20MB-success?style=for-the-badge" alt="19 MB RAM">
  <img src="https://img.shields.io/badge/Discord-bot-5865F2?style=for-the-badge&logo=discord&logoColor=white" alt="Discord">
</p>

<p align="center">
  <strong>The same minimalist, self-hosted Discord music bot — rewritten in Rust.<br>
  ~19 MB of RAM while playing music. Zero FFmpeg. Zero resident Python.</strong>
</p>

> [!WARNING]
> **Playify v3 is highly experimental and not yet on par with V2.**
> Bugs, missing features, and stability issues are to be expected. Please prefer using the **V2 version (Python/Node)** for daily use until V3 is fully stabilized.


---

### ~ what is this branch

This branch is the **complete Rust port of Playify**. It contains three things:

1. **`playify-rs/bot/` — Playify v3**, a full-Rust bot: one native process running the
   Discord gateway, 31 slash commands, voice with end-to-end encryption, and a
   built-in audio DSP engine. This is the endgame.
2. **`playify-rs/node/` — the audio node**, a hybrid mode where the existing Python
   bot (v2) keeps all its logic but delegates the realtime audio path to a Rust
   sidecar process. Opt-in via `PLAYIFY_RUST_NODE=1`, zero behavior change otherwise.
3. **The v2 Python bot itself**, still here and still working — plus the memory
   fixes found while porting (it went from ~600 MB to ~230 MB peak on its own).

---

### * the numbers (all measured live, same machine, same Discord server)

| | v2 Python (before) | v2 + Rust node | **v3 full Rust** |
| :--- | :--- | :--- | :--- |
| RAM idle | ~103 MB | ~90 MB + 7 MB node | **~15 MB** |
| RAM after playing songs | **~600 MB** | ~224 MB | **~19 MB** |
| FFmpeg processes | 1 per track + one per idle 24/7 server | filters only → then zero | **zero, always** |
| Voice connect (E2EE handshake) | — | — | **1.3 s** |
| /play → audio (YouTube, yt-dlp resolve) | ~3 s | ~3 s | **~2–3 s** |
| Seek into a track | FFmpeg decode-and-discard | native HTTP Range jump | **native HTTP Range jump** |
| Python processes resident | bot + up to 6 yt-dlp workers | bot + capped workers | **none** |

Where v2's ~600 MB came from (fixed on this branch even for the Python bot):
each yt-dlp pool worker imported the *entire bot* (~100 MB × one per CPU core),
and full-size yt-dlp metadata (the complete `formats` list, thumbnails,
subtitles — hundreds of KB per track) was stored in the URL cache, queue and
history. A cached track is now ~3 KB and workers are slim, capped and recycled.

---

### > install (v3 — nothing to preinstall)

**Windows** — download the repo (ZIP or git), double-click **`start-v3.bat`**.

**Linux / macOS** — `./start-v3.sh`

The launcher handles everything on a blank machine:

1. Asks for your Discord token on first run and writes `.env`.
2. Downloads the **standalone yt-dlp binary** into `bin/` (no Python needed).
3. Downloads the **prebuilt Playify v3 binary** from GitHub Releases
   (built by CI for Windows and Linux x86_64). Building from source
   (`cargo build --release` in `playify-rs/`) is only a fallback for
   developers and exotic platforms — end users never compile anything.
4. Starts the terminal dashboard (the same design as v2's TUI: status panel,
   now playing, live logs; `L` full logs, `S` save state, `Q` quit).

> Don't run v2 (`start.bat`) and v3 (`start-v3.bat`) with the same token at
> the same time — both register slash commands.

**Hybrid mode instead (keep the Python bot, Rust audio only):** set
`PLAYIFY_RUST_NODE=1` in `.env` and start v2 normally. The bot auto-spawns the
node binary and the TUI shows its status. Everything else stays identical.

---

### # what v3 supports

**31 slash commands** — `play`, `playnext`, `play-files` (up to 5 uploads),
`search` (pick from a select menu), `skip` (with position), `jumpto`,
`previous`, `stop`, `clearqueue`, `remove` (index or interactive menu),
`pause`, `resume`, `queue` (button pagination), `nowplaying`, `volume`,
`defaultvolume` (persisted per server), `seek`, `filter`, `loop`, `shuffle`,
`autoplay`, `24_7` (normal/auto/off), `reconnect`, `lyrics`, `karaoke`
(live synced lyrics), `status`, `support`, `kaomoji`, `allowlist`, `setup`,
`leave`.

**Platforms**

| Source | How |
| :--- | :--- |
| YouTube / YT Music, SoundCloud, Twitch, Bandcamp, direct audio URLs | yt-dlp (short-lived subprocess per resolution) |
| Spotify — tracks, albums, playlists | embed-page JSON scrape → YouTube search (no API key needed) |
| Deezer — tracks, albums, playlists, page.link | public API → YouTube search |
| Apple Music / Amazon Music / Tidal | JSON-LD & OpenGraph scrape → YouTube search |
| Live streams (Twitch, YouTube live) | HLS via songbird `HlsRequest` |
| Local files | Discord CDN streaming from attachments |

**Behavior parity with v2** — controller button panel auto-posted per track
(pin it with `/setup`), auto-pause when the channel empties + resume when
someone returns, 60 s idle disconnect (unless 24/7), 24/7 radio requeueing,
autoplay via YouTube Mix / SoundCloud stations, per-guild persisted settings,
**full playback state persisted across restarts** (rejoins the channel and
resumes at the saved position), kawaii mode applied to every reply through a
central hook, channel allowlist.

---

### + under the hood (the technical part)

**Voice.** [songbird](https://github.com/serenity-rs/songbird) 0.6 with
Discord's **DAVE end-to-end encryption** — mandatory on all voice connections
since March 2026 (close code 4017 without it). The 20 ms Opus send loop,
encryption and mixing run natively; no GIL, no per-guild Python thread.

**Decoding.** [symphonia](https://github.com/pdeljanov/Symphonia) decodes
Opus/WebM, M4A/AAC, MP3, FLAC, OGG, WAV in-process. The HTTP source is a
custom **seekable Range-request reader**: symphonia's MP4 demuxer requires
seekability (YouTube serves M4A when Opus isn't available), seeks become
instant byte-range jumps instead of FFmpeg's decode-and-discard, and
mid-stream network drops reconnect automatically — replacing FFmpeg's
`-reconnect` flags.

**Filters.** All 8 filters (`slowed`, `spedup`, `nightcore`, `reverb`, `8d`,
`muffled`, `bassboost`, `earrape`) are implemented as native DSP in
`playify-rs/audio/`, **bit-faithful to their FFmpeg equivalents**: `asetrate`
is reproduced as an *absolute* rate relabel (nightcore on a 48 kHz Opus source
plays at 55125/48000 ≈ 1.148×, exactly like FFmpeg — not a naive 1.25×),
`bass=g=10` uses the RBJ low-shelf with FFmpeg's Q-factor width (f=100 Hz,
Q=0.5), and `aecho` was verified against `af_aecho.c` (raw dry signal in the
delay line, `out_gain` on the full sum). A Catmull-Rom resampler handles the
rate/pitch changes.

**Track resolution.** yt-dlp runs as a **short-lived subprocess** (`-J`,
standalone binary, auto-discovered from `bin/`, the repo venv, or
`PLAYIFY_YTDLP`). No resident interpreter: memory returns to zero after each
resolution. Playlists resolve flat (title + URL) and each entry fetches a
fresh stream URL right before it plays, so long queues never hit expired URLs.

**State.** Per-guild settings in `data/v3_settings.json`; full playback state
snapshotted to `data/v3_state.json` every 60 s and on shutdown. The Rust node
additionally exposes a **Prometheus endpoint** (`http://127.0.0.1:8792/metrics`)
with sessions, tracks by engine, RSS and uptime — the v2 TUI consumes it, and
any Grafana setup can too.

**Workspace layout**

```
playify-rs/
  audio/   playify-audio  — DSP filters, decode pipeline, seekable HTTP source
  node/    playify-rs     — WebSocket audio node for the Python bot (hybrid mode)
  bot/     playify-v3     — the full Rust bot (gateway, commands, TUI)
```

**Configuration** (all optional, in `.env`)

| Variable | Default | Purpose |
| :--- | :--- | :--- |
| `DISCORD_TOKEN` | — | required |
| `PLAYIFY_YTDLP` | auto-discovered | path to the yt-dlp binary |
| `PLAYIFY_RUST_NODE` | `0` | v2 hybrid mode: delegate audio to the node |
| `PLAYIFY_NODE_PORT` / `PLAYIFY_NODE_SECRET` | `8791` / none | node control plane |
| `PLAYIFY_NODE_METRICS_PORT` | `8792` | Prometheus endpoint (0 disables) |
| `PLAYIFY_YTDLP_WORKERS` | `min(3, cores)` | v2 only: yt-dlp pool size |

---

### @ troubleshooting

* **Voice fails with close code 4017** — your build predates DAVE support;
  update to this branch's binaries (songbird 0.6+).
* **yt-dlp errors on YouTube** — the launcher's standalone yt-dlp updates via
  `bin\yt-dlp.exe -U`.
* **Prebuilt binary won't download** — the launcher falls back to building
  from source, which needs Rust + CMake (and MSVC Build Tools on Windows).
* **Both bots answer twice** — you're running v2 and v3 with the same token;
  stop one.

---

### ~ status

v3 is feature-complete and live-tested (every audio path in this README was
verified against a real Discord server), but younger than v2 — treat it as a
release candidate. The hybrid node mode is the conservative option: v2's
battle-tested logic with Rust audio. The v2 Python bot remains fully
supported on `main`.

Remaining gaps vs v2: full i18n string coverage (the YAML loader and both
locales are wired; most v3 replies are English + kawaii decoration for now).

---

### ~ privacy & license

* **Self-hosted only** — no telemetry, everything stays on your machine.
* MIT License — do what you want with the code, just be kind.

---

<p align="center">
  made with love by <a href="https://github.com/alan7383">alan7383</a> — Rust port with Claude
</p>
