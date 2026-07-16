"""Full-pipeline live test: real Playify internals through the Rust node.

Unlike live_test.py (minimal client, direct MP3), this exercises the bot's
actual machinery: guild state, the real queue, yt-dlp resolution of a
YouTube URL, play_audio()'s Rust-node branch, the after_playing chain, the
FFmpeg-on-node path (filters + seek), pause via the controller code path,
and safe_stop. Run from repo root:

    .venv\\Scripts\\python.exe playify-rs\\live_test_full.py
"""

import asyncio
import os
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

os.environ["PLAYIFY_RUST_NODE"] = "1"  # must precede rust_node import

# Import the full application: registers all commands and event handlers.
import src.playify.app  # noqa: F401,E402
from src.playify.core import bot, get_guild_state, init_db, logger  # noqa: E402
from src.playify.services.playback import play_audio  # noqa: E402
from src.playify.services.rust_node import (  # noqa: E402
    RustVoiceClient,
    connect_voice,
    node_client,
)
from src.playify.helpers.common import safe_stop  # noqa: E402

YOUTUBE_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
RESULTS: dict[str, bool | list] = {
    "voice_connect": False,
    "ytdlp_resolve_and_play": False,
    "pause_resume": False,
    "filter_seek_ffmpeg": False,
    "clean_stop": False,
    "errors": [],
}


def pick_voice_channel(guild):
    for channel in guild.voice_channels:
        if any(not m.bot for m in channel.members):
            return channel
    return guild.voice_channels[0] if guild.voice_channels else None


async def run_test():
    await bot.wait_until_ready()
    print(f"[TEST] Logged in as {bot.user} ({len(bot.guilds)} guild(s))")

    guild = bot.guilds[0]
    guild_id = guild.id
    channel = pick_voice_channel(guild)
    if channel is None:
        RESULTS["errors"].append("no voice channel found")
        return
    print(f"[TEST] Guild '{guild.name}' | voice channel '{channel.name}'")

    state = get_guild_state(guild_id)
    music_player = state.music_player

    # -- 1. voice connect through the real helper --------------------------
    vc = await connect_voice(channel)
    music_player.voice_client = vc
    music_player.text_channel = None  # no messages during the test
    RESULTS["voice_connect"] = isinstance(vc, RustVoiceClient) and vc.is_connected()
    print(f"[TEST] voice_connect: {RESULTS['voice_connect']}")

    # -- 2. real queue + yt-dlp resolution + play_audio ---------------------
    await music_player.queue.put(
        {"url": YOUTUBE_URL, "title": "Test Track", "is_single": True}
    )
    print("[TEST] Queued YouTube URL, calling play_audio() (yt-dlp resolve)...")
    t0 = time.perf_counter()
    music_player.current_task = bot.loop.create_task(play_audio(guild_id))

    for _ in range(60):  # up to 30 s for yt-dlp
        await asyncio.sleep(0.5)
        if vc.is_playing():
            break
    elapsed = time.perf_counter() - t0
    status = await node_client.request("status", guild_id=guild_id)
    RESULTS["ytdlp_resolve_and_play"] = vc.is_playing() and status.get("has_track")
    title = (music_player.current_info or {}).get("title")
    print(
        f"[TEST] ytdlp_resolve_and_play: {RESULTS['ytdlp_resolve_and_play']} "
        f"(resolved+playing in {elapsed:.1f}s, title: {title!r}, node: {status})"
    )

    # -- 3. pause/resume exactly like the controller button ----------------
    if vc.is_paused():
        vc.resume()
    else:
        vc.pause()
    await asyncio.sleep(1)
    paused = vc.is_paused()
    vc.resume()
    await asyncio.sleep(1)
    RESULTS["pause_resume"] = paused and vc.is_playing()
    print(f"[TEST] pause_resume: {RESULTS['pause_resume']}")

    # -- 4. filter + seek => node-side FFmpeg path --------------------------
    print("[TEST] Applying nightcore filter + seek to 30s (FFmpeg on node)...")
    state.server_filters = {"nightcore"}
    await play_audio(guild_id, seek_time=30, is_a_loop=True)
    await asyncio.sleep(4)
    status = await node_client.request("status", guild_id=guild_id)
    RESULTS["filter_seek_ffmpeg"] = vc.is_playing() and status.get("has_track")
    print(f"[TEST] filter_seek_ffmpeg: {RESULTS['filter_seek_ffmpeg']} (node: {status})")
    state.server_filters = set()

    # -- 5. stop + disconnect through the bot's own path --------------------
    music_player.manual_stop = True
    await safe_stop(vc)
    await asyncio.sleep(1)
    if music_player.current_task and not music_player.current_task.done():
        music_player.current_task.cancel()
    await vc.disconnect()
    await asyncio.sleep(1)
    status = await node_client.request("status", guild_id=guild_id)
    RESULTS["clean_stop"] = (not vc.is_connected()) and not status.get("connected")
    print(f"[TEST] clean_stop: {RESULTS['clean_stop']} (node: {status})")


async def main():
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        print("FATAL: DISCORD_TOKEN missing")
        return 1

    init_db()
    bot.start_time = time.time()

    async with bot:
        test_task = None

        async def runner():
            try:
                await run_test()
            except Exception as e:
                import traceback

                traceback.print_exc()
                RESULTS["errors"].append(f"{type(e).__name__}: {e}")
            finally:
                await bot.close()

        test_task = asyncio.create_task(runner())
        try:
            await bot.start(token)
        except Exception:
            pass
        if test_task:
            await test_task

    print("\n===== FULL PIPELINE TEST RESULTS =====")
    checks = [k for k in RESULTS if k != "errors"]
    for key in checks:
        print(f"  {key}: {'PASS' if RESULTS[key] else 'FAIL'}")
    if RESULTS["errors"]:
        print("  errors:")
        for err in RESULTS["errors"]:
            print(f"    - {err}")
    ok = all(RESULTS[k] for k in checks)
    print(f"===== {'ALL PASS' if ok else 'FAILED'} =====")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
