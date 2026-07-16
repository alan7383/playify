"""Memory profile of the full bot through a play/finish cycle.

Measures RSS of the whole process tree (bot + yt-dlp workers + Rust node)
at idle, during playback, and after the track ends — the scenario that
previously ballooned to ~600 MB (one full-bot import per pool worker,
full-size yt-dlp dicts in cache/history).

    .venv\\Scripts\\python.exe playify-rs\\live_test_memory.py
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
from src.playify.core import bot, get_guild_state, init_db, url_cache  # noqa: E402
from src.playify.services.playback import play_audio  # noqa: E402
from src.playify.services.rust_node import connect_voice  # noqa: E402
from src.playify.helpers.common import safe_stop  # noqa: E402

# 15s track keeps the test quick while still exercising a full end cycle.
TEST_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
MEASUREMENTS: dict[str, float] = {}
DETAILS: dict[str, str] = {}


def tree_rss_mb() -> tuple[float, str]:
    """Total RSS of this process and all descendants, plus a breakdown."""
    me = psutil.Process()
    total = me.memory_info().rss
    parts = [f"bot={total / 1048576:.0f}MB"]
    workers = 0.0
    node = 0.0
    other = 0.0
    for child in me.children(recursive=True):
        try:
            rss = child.memory_info().rss
            name = child.name().lower()
        except psutil.NoSuchProcess:
            continue
        total += rss
        if "python" in name:
            workers += rss
        elif "playify-rs" in name:
            node += rss
        else:
            other += rss
    if workers:
        parts.append(f"ydl-workers={workers / 1048576:.0f}MB")
    if node:
        parts.append(f"node={node / 1048576:.0f}MB")
    if other:
        parts.append(f"other={other / 1048576:.0f}MB")
    return total / 1048576, " ".join(parts)


def record(label: str):
    total, detail = tree_rss_mb()
    MEASUREMENTS[label] = total
    DETAILS[label] = detail
    print(f"[MEM] {label:<22} {total:7.0f} MB   ({detail})")


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

    state = get_guild_state(guild_id)
    music_player = state.music_player

    await asyncio.sleep(2)
    record("idle (connected)")

    vc = await connect_voice(channel)
    music_player.voice_client = vc
    music_player.text_channel = None

    for i in range(3):
        await music_player.queue.put(
            {"url": TEST_URL, "title": f"Mem Test {i}", "is_single": True}
        )
    music_player.current_task = bot.loop.create_task(play_audio(guild_id))
    for _ in range(60):
        await asyncio.sleep(0.5)
        if vc.is_playing():
            break
    await asyncio.sleep(3)
    record("playing (track 1)")

    # Skip through the queue to run several full resolve+play cycles.
    for n in (2, 3):
        music_player.manual_stop = False
        await safe_stop(vc)
        for _ in range(60):
            await asyncio.sleep(0.5)
            if vc.is_playing():
                break
        await asyncio.sleep(2)
        record(f"playing (track {n})")

    music_player.manual_stop = True
    await safe_stop(vc)
    await asyncio.sleep(3)
    record("after playback ended")

    cached_kb = sum(
        len(str(v)) for v in list(url_cache.values())
    ) / 1024  # rough but indicative
    print(f"[MEM] url_cache: {url_cache.currsize} entries, ~{cached_kb:.0f} KB serialized")

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
            except Exception:
                import traceback

                traceback.print_exc()
            finally:
                await bot.close()

        task = asyncio.create_task(runner())
        try:
            await bot.start(os.getenv("DISCORD_TOKEN"))
        except Exception:
            pass
        await task

    print("\n===== MEMORY PROFILE =====")
    for label, value in MEASUREMENTS.items():
        print(f"  {label:<22} {value:7.0f} MB")
    peak = max(MEASUREMENTS.values()) if MEASUREMENTS else 0
    print(f"  peak: {peak:.0f} MB (previously ~600 MB after one song)")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
