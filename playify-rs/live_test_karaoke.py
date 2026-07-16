"""Live test of the karaoke-critical paths through the Rust node.

Karaoke never touches audio directly: it displays synced lyrics at a
position computed from start_time + elapsed * playback_speed. What it
depends on is (1) accurate position bookkeeping, and (2) the filter-change
flow (FilterView.button_callback): compute elapsed -> set playback_speed ->
seek_info -> safe_stop -> track_end fires after_playing -> play_audio
restarts at seek_time with the new filter chain. This test replicates that
flow exactly, plus runs update_karaoke_task against the Rust backend.

    .venv\\Scripts\\python.exe playify-rs\\live_test_karaoke.py
"""

import asyncio
import os
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

os.environ["PLAYIFY_RUST_NODE"] = "1"

import src.playify.app  # noqa: F401,E402
from src.playify.core import bot, get_guild_state, init_db  # noqa: E402
from src.playify.helpers.common import (  # noqa: E402
    get_speed_multiplier_from_filters,
    safe_stop,
)
from src.playify.services.playback import play_audio, update_karaoke_task  # noqa: E402
from src.playify.services.rust_node import connect_voice, node_client  # noqa: E402

YOUTUBE_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
RESULTS = {
    "initial_play": False,
    "filter_change_flow": False,
    "position_after_seek": False,
    "karaoke_task_runs": False,
    "errors": [],
}


def pick_voice_channel(guild):
    for channel in guild.voice_channels:
        if any(not m.bot for m in channel.members):
            return channel
    return guild.voice_channels[0] if guild.voice_channels else None


async def run_test():
    await bot.wait_until_ready()
    guild = bot.guilds[0]
    guild_id = guild.id
    channel = pick_voice_channel(guild)
    print(f"[TEST] Guild '{guild.name}' | channel '{channel.name}'")

    state = get_guild_state(guild_id)
    music_player = state.music_player

    vc = await connect_voice(channel)
    music_player.voice_client = vc
    music_player.text_channel = None

    # -- 1. start playback ---------------------------------------------------
    await music_player.queue.put(
        {"url": YOUTUBE_URL, "title": "Karaoke Test", "is_single": True}
    )
    music_player.current_task = bot.loop.create_task(play_audio(guild_id))
    for _ in range(60):
        await asyncio.sleep(0.5)
        if vc.is_playing():
            break
    RESULTS["initial_play"] = vc.is_playing()
    print(f"[TEST] initial_play: {RESULTS['initial_play']}")
    await asyncio.sleep(5)  # let it play a bit so elapsed > 0

    # -- 2. replicate FilterView.button_callback exactly ----------------------
    print("[TEST] Simulating the filter button flow (nightcore mid-track)...")
    state.server_filters.add("nightcore")
    old_speed = music_player.playback_speed
    elapsed_time = 0
    if music_player.playback_started_at:
        real_elapsed_time = time.time() - music_player.playback_started_at
        elapsed_time = (real_elapsed_time * old_speed) + music_player.start_time
    music_player.playback_speed = get_speed_multiplier_from_filters(
        state.server_filters
    )
    music_player.is_seeking = True
    music_player.seek_info = elapsed_time
    await safe_stop(music_player.voice_client)
    print(
        f"[TEST] safe_stop sent at position {elapsed_time:.1f}s, "
        f"new speed {music_player.playback_speed}x. Waiting for auto-restart..."
    )

    # after_playing must consume seek_info (from the node's track_end event)
    # and restart playback at the right position with the filter applied.
    restarted = False
    for _ in range(40):
        await asyncio.sleep(0.5)
        if vc.is_playing() and music_player.seek_info is None:
            restarted = True
            break
    status = await node_client.request("status", guild_id=guild_id)
    RESULTS["filter_change_flow"] = restarted and status.get("has_track")
    print(
        f"[TEST] filter_change_flow: {RESULTS['filter_change_flow']} "
        f"(node: {status}, start_time: {music_player.start_time:.1f}s)"
    )

    # -- 3. position bookkeeping used by the karaoke display ------------------
    drift_ok = abs(music_player.start_time - elapsed_time) < 3.0
    RESULTS["position_after_seek"] = drift_ok and music_player.playback_started_at
    print(
        f"[TEST] position_after_seek: {RESULTS['position_after_seek']} "
        f"(expected ~{elapsed_time:.1f}s, got {music_player.start_time:.1f}s)"
    )

    # -- 4. run the real karaoke task against the Rust backend ----------------
    # No lyrics_message (no Discord edits); we just verify the task loops and
    # computes positions without touching missing VoiceClient internals.
    music_player.synced_lyrics = [
        {"time": 0, "text": "line 1"},
        {"time": 5_000, "text": "line 2"},
        {"time": 10_000, "text": "line 3"},
        {"time": 999_000, "text": "line end"},
    ]
    music_player.lyrics_message = None
    karaoke_task = asyncio.create_task(update_karaoke_task(guild_id))
    await asyncio.sleep(4)
    RESULTS["karaoke_task_runs"] = not karaoke_task.done()
    print(f"[TEST] karaoke_task_runs: {RESULTS['karaoke_task_runs']}")
    karaoke_task.cancel()

    # -- teardown --------------------------------------------------------------
    music_player.manual_stop = True
    await safe_stop(vc)
    await asyncio.sleep(1)
    if music_player.current_task and not music_player.current_task.done():
        music_player.current_task.cancel()
    await vc.disconnect()


async def main():
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        print("FATAL: DISCORD_TOKEN missing")
        return 1
    init_db()
    bot.start_time = time.time()

    async with bot:

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
        await test_task

    print("\n===== KARAOKE PATH TEST RESULTS =====")
    checks = [k for k in RESULTS if k != "errors"]
    for key in checks:
        print(f"  {key}: {'PASS' if RESULTS[key] else 'FAIL'}")
    for err in RESULTS["errors"]:
        print(f"    - {err}")
    ok = all(RESULTS[k] for k in checks)
    print(f"===== {'ALL PASS' if ok else 'FAILED'} =====")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
