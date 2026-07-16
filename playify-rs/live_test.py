"""Live end-to-end test of the playify-rs node against a real Discord server.

Connects with the bot token from .env, joins a voice channel (preferring one
with a human in it), streams a real MP3 through the Rust node, verifies
playback state + track_end event flow, then disconnects. Run from repo root:

    .venv\\Scripts\\python.exe playify-rs\\live_test.py
"""

import asyncio
import os
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

os.environ["PLAYIFY_RUST_NODE"] = "1"  # force the Rust backend for this test

import discord
from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env")

from src.playify.services.rust_node import (  # noqa: E402
    RustVoiceClient,
    node_client,
)

TEST_MP3 = "https://download.samplelib.com/mp3/sample-15s.mp3"
RESULTS = {"connected": False, "played": False, "track_end": False, "errors": []}

intents = discord.Intents.default()
intents.guilds = True
intents.voice_states = True
client = discord.Client(intents=intents)


def pick_voice_channel(guild: discord.Guild):
    """Prefer a channel with a human in it so the user can hear the test."""
    for channel in guild.voice_channels:
        if any(not m.bot for m in channel.members):
            return channel
    return guild.voice_channels[0] if guild.voice_channels else None


@client.event
async def on_ready():
    try:
        await run_test()
    except Exception as e:
        RESULTS["errors"].append(f"{type(e).__name__}: {e}")
        import traceback

        traceback.print_exc()
    finally:
        await client.close()


async def run_test():
    print(f"[TEST] Logged in as {client.user} ({len(client.guilds)} guild(s))")
    if not client.guilds:
        RESULTS["errors"].append("Bot is in no guild")
        return

    guild = client.guilds[0]
    channel = pick_voice_channel(guild)
    if channel is None:
        RESULTS["errors"].append(f"No voice channel in guild '{guild.name}'")
        return
    humans = [m.display_name for m in channel.members if not m.bot]
    print(f"[TEST] Guild: '{guild.name}' | Channel: '{channel.name}' | humans: {humans or 'none'}")

    print("[TEST] Connecting voice via Rust node...")
    t0 = time.perf_counter()
    vc = await channel.connect(cls=RustVoiceClient, timeout=30)
    print(f"[TEST] Voice connected in {time.perf_counter() - t0:.2f}s")
    RESULTS["connected"] = True

    track_done = asyncio.Event()

    def after(error):
        if error:
            RESULTS["errors"].append(f"after() error: {error}")
        RESULTS["track_end"] = True
        track_done.set()

    print(f"[TEST] Playing {TEST_MP3} (http source, symphonia decode, no FFmpeg)...")
    await vc.play_remote(TEST_MP3, after=after, volume=0.5)

    await asyncio.sleep(3)
    status = await node_client.request("status", guild_id=guild.id)
    print(f"[TEST] Node status after 3s: {status}")
    print(f"[TEST] vc.is_playing()={vc.is_playing()} is_paused()={vc.is_paused()}")
    if status.get("has_track") and vc.is_playing():
        RESULTS["played"] = True

    print("[TEST] Testing pause/resume/volume...")
    vc.pause()
    await asyncio.sleep(1)
    paused_ok = vc.is_paused()
    vc.resume()
    vc.source.volume = 1.0
    await asyncio.sleep(1)
    print(f"[TEST] pause registered: {paused_ok}, resumed: {vc.is_playing()}")

    print("[TEST] Waiting for track_end (15s track)...")
    try:
        await asyncio.wait_for(track_done.wait(), timeout=25)
        print("[TEST] track_end event received.")
    except asyncio.TimeoutError:
        RESULTS["errors"].append("track_end not received within 25s")

    print("[TEST] Disconnecting...")
    await vc.disconnect()
    await asyncio.sleep(1)


async def main():
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        print("FATAL: DISCORD_TOKEN missing from .env")
        return 1
    await client.start(token)

    print("\n===== LIVE TEST RESULTS =====")
    for key in ("connected", "played", "track_end"):
        print(f"  {key}: {'PASS' if RESULTS[key] else 'FAIL'}")
    if RESULTS["errors"]:
        print("  errors:")
        for err in RESULTS["errors"]:
            print(f"    - {err}")
    ok = RESULTS["connected"] and RESULTS["played"] and RESULTS["track_end"]
    print(f"===== {'ALL PASS' if ok else 'FAILED'} =====")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
