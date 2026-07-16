"""Live test of the native DSP engine: filters and seeks without FFmpeg.

Verifies against a real Discord server that filtered playback (nightcore,
bassboost+8d) and seeks run through the node's in-process symphonia+DSP
pipeline: the play response reports engine="dsp", the node process has zero
FFmpeg children, and playback keeps running (not just starting).

    .venv\\Scripts\\python.exe playify-rs\\live_test_dsp.py
"""

import asyncio
import os
import sys
import time
from pathlib import Path

import psutil

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

os.environ["PLAYIFY_RUST_NODE"] = "1"

import src.playify.app  # noqa: F401,E402
from src.playify.core import bot, get_guild_state, init_db  # noqa: E402
from src.playify.services.playback import play_audio  # noqa: E402
from src.playify.services.rust_node import connect_voice, node_client  # noqa: E402
from src.playify.helpers.common import safe_stop  # noqa: E402

YOUTUBE_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
RESULTS = {
    "nightcore_native": False,
    "no_ffmpeg_children": False,
    "sustained_playback": False,
    "stacked_filters_native": False,
    "seek_native": False,
    "stats_and_metrics": False,
    "errors": [],
}


def node_ffmpeg_children() -> int:
    count = 0
    for proc in psutil.process_iter(["name"]):
        if proc.info["name"] and "playify-rs" in proc.info["name"]:
            for child in proc.children(recursive=True):
                # conhost.exe etc. are normal on Windows; only FFmpeg matters.
                if "ffmpeg" in (child.name() or "").lower():
                    count += 1
    return count


def pick_voice_channel(guild):
    for channel in guild.voice_channels:
        if any(not m.bot for m in channel.members):
            return channel
    return guild.voice_channels[0] if guild.voice_channels else None


async def play_and_wait(guild_id, vc, music_player, seek=0):
    task = bot.loop.create_task(play_audio(guild_id, seek_time=seek, is_a_loop=seek > 0))
    for _ in range(60):
        await asyncio.sleep(0.5)
        if vc.is_playing():
            return True
    return False


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

    # -- 1. nightcore, fully native ------------------------------------------
    state.server_filters = {"nightcore"}
    await music_player.queue.put(
        {"url": YOUTUBE_URL, "title": "DSP Test", "is_single": True}
    )
    ok = await play_and_wait(guild_id, vc, music_player)
    status = await node_client.request("status", guild_id=guild_id)
    RESULTS["nightcore_native"] = ok and status.get("engine") == "dsp"
    print(f"[TEST] nightcore_native: {RESULTS['nightcore_native']} (status: {status})")

    ffmpeg_count = node_ffmpeg_children()
    RESULTS["no_ffmpeg_children"] = ffmpeg_count == 0
    print(
        f"[TEST] no_ffmpeg_children: {RESULTS['no_ffmpeg_children']} "
        f"(node child processes: {ffmpeg_count})"
    )

    # -- 2. keeps playing (catches pipelines that die after a few chunks) ----
    await asyncio.sleep(8)
    status = await node_client.request("status", guild_id=guild_id)
    RESULTS["sustained_playback"] = vc.is_playing() and status.get("has_track")
    print(f"[TEST] sustained_playback after 8s: {RESULTS['sustained_playback']}")

    # -- 3. stacked filters (bassboost + 8d + muffled), native ---------------
    state.server_filters = {"bassboost", "8d", "muffled"}
    music_player.is_seeking = True
    music_player.seek_info = 20.0
    await safe_stop(vc)
    for _ in range(40):
        await asyncio.sleep(0.5)
        if vc.is_playing() and music_player.seek_info is None:
            break
    status = await node_client.request("status", guild_id=guild_id)
    RESULTS["stacked_filters_native"] = (
        vc.is_playing() and status.get("engine") == "dsp"
    )
    print(
        f"[TEST] stacked_filters_native: {RESULTS['stacked_filters_native']} "
        f"(status: {status}, ffmpeg children: {node_ffmpeg_children()})"
    )

    # -- 4. plain seek without filters: also native now ----------------------
    state.server_filters = set()
    await play_audio(guild_id, seek_time=45, is_a_loop=True)
    await asyncio.sleep(3)
    status = await node_client.request("status", guild_id=guild_id)
    RESULTS["seek_native"] = vc.is_playing() and status.get("engine") == "dsp"
    print(f"[TEST] seek_native: {RESULTS['seek_native']} (status: {status})")

    # -- 5. stats op + Prometheus endpoint while a track plays ----------------
    stats = await node_client.request("stats", guild_id=guild_id)
    import urllib.request

    metrics_port = os.getenv("PLAYIFY_NODE_METRICS_PORT", "8792")
    with urllib.request.urlopen(
        f"http://127.0.0.1:{metrics_port}/metrics", timeout=2
    ) as response:
        metrics_text = response.read().decode()
    RESULTS["stats_and_metrics"] = (
        stats.get("sessions") == 1
        and stats.get("tracks_playing") == 1
        and stats.get("engines", {}).get("dsp") == 1
        and stats.get("tracks_played_total", 0) >= 3  # initial + filter restart + seek
        and 'playify_node_engine_tracks{engine="dsp"} 1' in metrics_text
        and "playify_node_memory_bytes" in metrics_text
    )
    print(f"[TEST] stats_and_metrics: {RESULTS['stats_and_metrics']} (stats: {stats})")

    # -- teardown -------------------------------------------------------------
    music_player.manual_stop = True
    await safe_stop(vc)
    await asyncio.sleep(1)
    if music_player.current_task and not music_player.current_task.done():
        music_player.current_task.cancel()
    await vc.disconnect()


async def main():
    if not os.getenv("DISCORD_TOKEN"):
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
            await bot.start(os.getenv("DISCORD_TOKEN"))
        except Exception:
            pass
        await test_task

    print("\n===== NATIVE DSP TEST RESULTS =====")
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
