# Playify v3 — the full-Rust bot (experimental)

One native process, no resident Python: serenity gateway + poise slash
commands + songbird voice (DAVE/E2EE) + the shared `playify-audio` DSP
engine. yt-dlp runs as a short-lived subprocess per resolution.

**Measured on a live server: 35 MB RSS total while playing with nightcore**
(v2: ~90 MB Python brain + ~112 MB warm yt-dlp workers + 14 MB node).

## Run

```
cd playify-rs
cargo build --release
./target/release/playify-v3            # reads DISCORD_TOKEN from ../.env
./target/release/playify-v3 --selftest # live end-to-end audio check
./target/release/playify-v3 --resolve <url>  # platform resolution check
```

Do not run v2 and v3 with the same token at the same time (both register
slash commands).

## Command parity (v2 -> v3)

| Status | Commands |
| :--- | :--- |
| ✅ Ported | play, playnext, play-files, search (select menu), skip (+position), jumpto, previous, stop, clearqueue, remove, pause, resume, queue, nowplaying, volume, defaultvolume, seek, filter (8 native DSP filters), loop, shuffle, autoplay (YT Mix + SoundCloud stations), 24_7 (normal/auto/off), reconnect, lyrics, karaoke (synced, LRCLIB), status, support, kaomoji, allowlist, leave |
| 🚧 Not yet | setup/controller (persistent button panel), interactive remove menu, queue pagination buttons, full i18n (en/kawaii only), queue persistence across restarts, TUI dashboard |

## Platforms

| Platform | Path |
| :--- | :--- |
| YouTube / YT Music, SoundCloud, Twitch, Bandcamp, direct links | yt-dlp subprocess |
| Spotify (track/album/playlist) | embed-page JSON scrape -> YouTube search |
| Deezer (track/album/playlist, page.link) | public API -> YouTube search |
| Apple Music / Amazon Music / Tidal | JSON-LD & og: scrape -> YouTube search |
| Local files | /play-files attachments (Discord CDN streaming) |
| Live streams (HLS) | not yet in v3 (v2 + node covers them via FFmpeg) |

Verified live: Spotify track & 50-track playlist, Deezer album,
Apple Music album, and the full selftest (join/DAVE, resolve, nightcore
DSP playback, clean leave) — all pass.

## Events / behavior parity

- Auto-pause when the bot is alone; resume when a human returns.
- Idle disconnect after 60 s (unless 24/7).
- 24/7 normal = radio requeue of finished tracks; auto = autoplay refill.
- Per-guild persisted settings (default volume, 24/7, kawaii, allowlist)
  in `data/v3_settings.json`.

v2 (Python + node) remains the production path; v3 is the endgame port.
