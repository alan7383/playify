# playify-rs — Rust audio node

A standalone voice engine for Playify, built on [songbird](https://github.com/serenity-rs/songbird)
(driver-only mode). The Python bot keeps everything that makes Playify work —
discord.py, yt-dlp, SpotifyScraper, lyrics, the TUI — and delegates the
real-time audio path to this node:

```
┌────────────────────────────┐        ws://127.0.0.1:8791        ┌──────────────────────────┐
│  Python bot (discord.py)   │ ────────────────────────────────▶ │  playify-rs (songbird)   │
│  gateway, slash commands,  │   connect/play/stop/volume/...    │  UDP voice connection    │
│  yt-dlp, queue, UI, TUI    │ ◀──────────────────────────────── │  decode (symphonia)      │
│                            │   track_end / track_error events  │  Opus encode + crypto    │
└────────────────────────────┘                                   │  20 ms send loop         │
                                                                 └──────────────────────────┘
```

## What moves to Rust (and why)

| Path | Before (pure Python) | With the node |
| :--- | :--- | :--- |
| 20 ms voice send loop | discord.py thread per guild | songbird async mixer |
| Decode + resample | FFmpeg subprocess per track | symphonia in-process (HTTP/local) |
| Opus encode + E2EE (DAVE) | libopus + PyNaCl/davey via Python | native, no GIL contention |
| 24/7 idle keep-alive | looping FFmpeg `anullsrc` | zero-cost native keepalive |
| Filters (nightcore, reverb…) | FFmpeg subprocess | native DSP (`dsp.rs`), in-process |
| Seek | FFmpeg `-ss` subprocess | native (symphonia + HTTP Range) |

The only remaining FFmpeg use is live/HLS streams (Twitch, YouTube live) and
unrecognised filter names; `PLAYIFY_FORCE_FFMPEG=1` restores the old behavior
as an escape hatch. Engine actually used is reported per track
(`engine`: `direct` | `dsp` | `ffmpeg`) in play/status responses.

yt-dlp, Spotify resolution, lyrics, autoplay and every command stay in Python.

## Build

```
cd playify-rs
cargo build --release
```

Requirements: Rust 1.74+, CMake (for the bundled libopus build).
On Windows: `winget install Kitware.CMake`. If CMake complains about a
minimum-version policy, set `CMAKE_POLICY_VERSION_MINIMUM=3.5`.

The binary lands in `target/release/playify-rs[.exe]`. Optionally copy it to
the repo's `bin/` folder — the bot looks there first.

## Run

The bot auto-starts the node when `PLAYIFY_RUST_NODE` is enabled in `.env`:

```
PLAYIFY_RUST_NODE=1
# optional:
PLAYIFY_NODE_PORT=8791
PLAYIFY_NODE_SECRET=change-me
PLAYIFY_NODE_METRICS_PORT=8792         # Prometheus /metrics; 0 disables
PLAYIFY_FFMPEG=C:\path\to\ffmpeg.exe   # node-side ffmpeg (live streams only)
```

Or run it manually / under systemd: `./playify-rs` (it binds 127.0.0.1 only).

With `PLAYIFY_RUST_NODE` unset, Playify behaves exactly as before — the
whole integration is opt-in and the Python audio path remains intact.

## Protocol

JSON text frames over the local WebSocket. Requests carry `request_id`;
responses echo it with `ok`/`data`/`error`. Ops: `auth`, `ping`, `connect`
(gateway voice credentials forwarded from discord.py), `disconnect`, `play`
(`source_type`: `http` | `file` | `ffmpeg`, plus `volume`, `seek`, `filters`),
`stop`, `pause`, `resume`, `set_volume`, `status`. The node pushes
`{"op": "event", "event": "track_end" | "track_error" | "driver_*", ...}`.

`smoke_test.py` exercises the control plane without touching Discord.

## Status / roadmap

- [x] Voice connect via credential forwarding (discord.py keeps the gateway)
- [x] HTTP + local file playback without FFmpeg (symphonia)
- [x] FFmpeg fallback for filter chains, seeks into network streams, live streams
- [x] Volume, pause/resume, track events, 24/7 keepalive
- [x] Karaoke: synced-lyrics position tracking and mid-track speed change
      (filter toggle → seek_info → restart) verified live — karaoke never
      touches audio directly, so no node-side work was needed
- [x] Native filter DSP: slowed/spedup/nightcore (Catmull-Rom resampler),
      bassboost/muffled (RBJ biquads), reverb (multi-tap echo), 8d (LFO pan),
      earrape (bit crusher) — verified live, zero FFmpeg processes
- [x] Native seek, including YouTube m4a, via a seekable Range-request HTTP
      source with automatic mid-stream reconnection
- [x] Stats: `stats` WebSocket op + Prometheus endpoint on
      `http://127.0.0.1:8792/metrics` (PLAYIFY_NODE_METRICS_PORT, 0 to
      disable) — sessions, tracks playing per engine, memory, uptime.
      The TUI dashboard polls it and shows the node next to the bot stats.
