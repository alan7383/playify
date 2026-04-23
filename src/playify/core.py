"""Core configuration and shared state for Playify."""

# ==============================================================================
# 1. IMPORTS & GLOBAL CONFIGURATION
# ==============================================================================

# --- Imports ---

import discord
from discord.ext import commands
from discord import app_commands, Embed
from discord.ui import View, Button
from discord import ButtonStyle
from discord.app_commands import Choice
import asyncio
import yt_dlp
import re
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
from spotify_scraper import SpotifyClient
from spotify_scraper.core.exceptions import SpotifyScraperError
import random
from urllib.parse import urlparse, parse_qs, quote_plus
from cachetools import TTLCache
import logging
import requests
from playwright.async_api import async_playwright
from concurrent.futures import ProcessPoolExecutor
from src.i18n_translator import I18nTranslator, Locale
from typing import Optional
import json
import time
import syncedlyrics
import lyricsgenius
import psutil
import time
import datetime
import platform
import sys
import math  # Needed for the format_bytes helper
import traceback  # --- NEW --- To format exceptions
import os
import shutil
import subprocess
import shlex
import sqlite3
from pathlib import Path
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
DB_PATH = DATA_DIR / "playify_state.db"
I18N_DIR = PROJECT_ROOT / "i18n"

load_dotenv(PROJECT_ROOT / ".env")


def init_db():
    """Initialize the SQLite database and create tables if they do not exist."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Table for general server settings
    cursor.execute(
        """
    CREATE TABLE IF NOT EXISTS guild_settings (
        guild_id INTEGER PRIMARY KEY,
        kawaii_mode BOOLEAN NOT NULL DEFAULT 0,
        controller_channel_id INTEGER,
        controller_message_id INTEGER,
        is_24_7 BOOLEAN NOT NULL DEFAULT 0,
        autoplay BOOLEAN NOT NULL DEFAULT 0,
        volume REAL NOT NULL DEFAULT 1.0
    )"""
    )

    # Table for the list of allowed channels
    cursor.execute(
        """
    CREATE TABLE IF NOT EXISTS allowlist (
        guild_id INTEGER NOT NULL,
        channel_id INTEGER NOT NULL,
        PRIMARY KEY (guild_id, channel_id)
    )"""
    )

    # Table for playback state (current song, queue, etc.)
    cursor.execute(
        """
    CREATE TABLE IF NOT EXISTS playback_state (
        guild_id INTEGER PRIMARY KEY,
        voice_channel_id INTEGER,
        current_song_json TEXT,
        queue_json TEXT,
        history_json TEXT,
        radio_playlist_json TEXT,
        loop_current BOOLEAN NOT NULL DEFAULT 0,
        playback_timestamp REAL NOT NULL DEFAULT 0
    )"""
    )

    conn.commit()
    conn.close()
    logger.info("Database initialized successfully.")


process_pool = None
process_pool_init_error = None

try:
    process_pool = ProcessPoolExecutor(max_workers=psutil.cpu_count(logical=False))
except (NotImplementedError, PermissionError, OSError) as exc:
    process_pool_init_error = exc
    try:
        process_pool = ProcessPoolExecutor(max_workers=os.cpu_count())
    except (PermissionError, OSError) as fallback_exc:
        process_pool = None
        process_pool_init_error = fallback_exc

SILENT_MESSAGES = True
IS_PUBLIC_VERSION = False

# --- Logging ---

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

if process_pool is None and process_pool_init_error is not None:
    logger.warning(
        "Process pool unavailable, falling back to the default executor: %s",
        process_pool_init_error,
    )

# --- API Tokens & Clients ---

GENIUS_TOKEN = os.getenv("GENIUS_TOKEN")

if GENIUS_TOKEN and GENIUS_TOKEN != "YOUR_GENIUS_TOKEN_HERE":
    genius = lyricsgenius.Genius(GENIUS_TOKEN, remove_section_headers=True)
    logger.info("LyricsGenius client initialized.")
else:
    genius = None
    logger.warning(
        "GENIUS_TOKEN is not set in the code. /lyrics and fallback will not work."
    )

# Official API Client (fast and prioritized)

SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
try:
    sp = spotipy.Spotify(
        auth_manager=SpotifyClientCredentials(
            client_id=SPOTIFY_CLIENT_ID, client_secret=SPOTIFY_CLIENT_SECRET
        )
    )
    logger.info("Spotipy API Client successfully initialized.")
except Exception as e:
    sp = None
    logger.error(f"Could not initialize Spotipy client: {e}")

# Scraper Client (backup plan, without Selenium)
try:
    # Using "requests" mode, more reliable on a server
    spotify_scraper_client = SpotifyClient(browser_type="requests")
    logger.info("SpotifyScraper client successfully initialized in requests mode.")
except Exception as e:
    spotify_scraper_client = None
    logger.error(f"Could not initialize SpotifyScraper: {e}")

# --- Caching ---

url_cache = TTLCache(maxsize=75000, ttl=7200)

translator = I18nTranslator(
    default_locale=Locale.EN_US, translations_dir=str(I18N_DIR)
)

# --- Bot Configuration Dictionaries ---

AVAILABLE_COOKIES = [
    "cookies_1.txt",
    "cookies_2.txt",
    "cookies_3.txt",
    "cookies_4.txt",
    "cookies_5.txt",
]

# Dictionary of available audio filters and their FFmpeg options
AUDIO_FILTERS = {
    "slowed": "asetrate=44100*0.8",
    "spedup": "asetrate=44100*1.2",
    "nightcore": "asetrate=44100*1.25,atempo=1.0",
    "reverb": "aecho=0.8:0.9:40|50|60:0.4|0.3|0.2",
    "8d": "apulsator=hz=0.08",
    "muffled": "lowpass=f=500",
    "bassboost": "bass=g=10",  # Boost bass by 10 dB
    "earrape": "acrusher=level_in=8:level_out=18:bits=8:mode=log:aa=1",  # Ear rape effect
}

# Dictionary to map filter values to their display names
FILTER_DISPLAY_NAMES = {
    "none": "None",
    "slowed": "Slowed ♪",
    "spedup": "Sped Up ♫",
    "nightcore": "Nightcore ☆",
    "reverb": "Reverb",
    "8d": "8D Audio",
    "muffled": "Muffled",
    "bassboost": "Bass Boost",
    "earrape": "Earrape",
}

# --- Discord Bot Initialization ---

# Intents for the bot
intents = discord.Intents.default()
intents.guilds = True
intents.voice_states = True
intents.message_content = True


# Create the bot
# --- Definition of our custom bot class ---
class PlayifyBot(commands.Bot):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    # Override the close() method to add our save logic
    async def close(self):
        # Execute our save function before shutting down
        await save_all_states()
        # Call the original close() method to shut down the bot normally
        await super().close()


# --- Create an instance of our custom bot ---
# Intents for the bot
intents = discord.Intents.default()
intents.guilds = True
intents.voice_states = True
intents.message_content = True

# Create the bot
bot = PlayifyBot(command_prefix="!", intents=intents)

# ==============================================================================
# 2. CORE CLASSES & STATE MANAGEMENT
# ==============================================================================


class MusicPlayer:
    def __init__(self):
        self.voice_client = None
        self.current_task = None
        self.queue = asyncio.Queue()
        self.history = []
        self.radio_playlist = []
        self.current_url = None
        self.current_info = None
        self.text_channel = None
        self.loop_current = False
        self.autoplay_enabled = False
        self.last_was_single = False
        self.start_time = 0
        self.playback_started_at = None
        self.active_filter = None
        self.seek_info = None

        # --- Attributes for lyrics, karaoke, and filters ---
        self.lyrics_task = None
        self.lyrics_message = None
        self.synced_lyrics = None
        self.is_seeking = False
        self.playback_speed = 1.0

        self.is_reconnecting = False
        self.is_current_live = False

        self.hydration_task = None
        self.hydration_lock = asyncio.Lock()

        self.suppress_next_now_playing = False

        self.is_auto_promoting = False
        self.is_cleaning = False
        self.is_resuming_after_clean = False
        self.resume_info = None
        self.is_resuming_live = False
        self.silence_task = None
        self.is_playing_silence = False
        self.is_resuming_after_silence = False
        self.volume = 1.0
        self.controller_message_id = None
        self.duration_hydration_lock = asyncio.Lock()
        self.queue_lock = asyncio.Lock()
        self.silence_management_lock = asyncio.Lock()
        self.is_paused_by_leave = False
        self.manual_stop = False

    async def hydrate_track_info(self, track_info: dict) -> dict:
        """
        Takes a track dictionary and ensures it has full metadata like title and thumbnail.
        If the track is a LazySearchItem, it resolves it.
        If it's a dict with just a URL, it fetches the full info.
        """
        from .models.lazy_search import LazySearchItem
        from .services.voice import fetch_video_info_with_retry

        if isinstance(track_info, LazySearchItem):
            if not track_info.resolved_info:
                await track_info.resolve()
            return track_info.resolved_info or {
                "title": "Resolution Failed",
                "url": "#",
            }

        if isinstance(track_info, dict):
            # Check if info is already complete (no guild_id here, so check for known placeholder values)
            title = track_info.get("title")
            loading_placeholders = {"Loading...", "Loading... (´• ω •`)"}
            if title and title not in loading_placeholders:
                return track_info

            # Info is incomplete, fetch it
            try:
                url_to_fetch = track_info.get("url")
                if url_to_fetch:
                    full_info = await fetch_video_info_with_retry(url_to_fetch)
                    # Update the original dict with new info
                    track_info.update(full_info)
                    return track_info
            except Exception as e:
                logger.error(
                    f"On-the-fly hydration for '{track_info.get('url')}' failed: {e}"
                )
                return track_info  # Return original dict on failure

        return track_info  # Return as is if type is unknown


class GuildModel:
    """Groups all data specific to a server."""

    def __init__(self, guild_id: int):
        self.guild_id: int = guild_id
        self.music_player: MusicPlayer = MusicPlayer()
        self.locale: Locale = Locale.EN_US
        self.server_filters: set[str] = set()
        self.karaoke_disclaimer_shown: bool = False
        self._24_7_mode: bool = False
        self.allowed_channels: set[int] = set()
        self.controller_channel_id: int | None = None
        self.controller_message_id: int | None = None


# Main dictionary that will store the state of all guilds
guild_states = {}


def get_guild_state(guild_id: int) -> GuildModel:
    """Retrieves or creates the state for a guild."""
    if guild_id not in guild_states:
        guild_states[guild_id] = GuildModel(guild_id)
    return guild_states[guild_id]


def get_player(guild_id: int) -> MusicPlayer:
    """Helper to quickly get the music player for a guild."""
    return get_guild_state(guild_id).music_player


def get_mode(guild_id: int) -> bool:
    """Helper to quickly check if kawaii_mode is active for a guild."""
    # This now checks the locale set in the guild's state.
    return get_guild_state(guild_id).locale == Locale.EN_X_KAWAII


# --- Core Music Player Class ---


async def save_all_states():
    """Save the complete state of all servers in the database."""
    logger.info("Attempting to save the state of all servers...")
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("DELETE FROM guild_settings")
    cursor.execute("DELETE FROM allowlist")
    cursor.execute("DELETE FROM playback_state")

    for guild_id, state in guild_states.items():
        player = state.music_player
        settings = (
            guild_id,
            state.locale == Locale.EN_X_KAWAII,
            state.controller_channel_id,
            state.controller_message_id,
            state._24_7_mode,
            player.autoplay_enabled,
            player.volume,
        )
        cursor.execute(
            "INSERT INTO guild_settings VALUES (?, ?, ?, ?, ?, ?, ?)", settings
        )

        for channel_id in state.allowed_channels:
            cursor.execute(
                "INSERT INTO allowlist VALUES (?, ?)", (guild_id, channel_id)
            )

        if not player.voice_client or not player.voice_client.is_connected():
            continue

        timestamp = 0
        if player.playback_started_at:
            timestamp = (
                player.start_time
                + (time.time() - player.playback_started_at) * player.playback_speed
            )
        elif player.start_time > 0:
            timestamp = player.start_time

        state_data = (
            guild_id,
            player.voice_client.channel.id,
            json.dumps(player.current_info) if player.current_info else None,
            json.dumps(list(player.queue._queue)) if not player.queue.empty() else None,
            json.dumps(player.history),
            json.dumps(player.radio_playlist),
            player.loop_current,
            timestamp,
        )
        cursor.execute(
            "INSERT INTO playback_state VALUES (?, ?, ?, ?, ?, ?, ?, ?)", state_data
        )

    conn.commit()
    conn.close()
    logger.info("State save completed successfully.")


async def load_states_on_startup():
    """Load the state of servers from the database on startup and attempt to resume playback."""
    from .services.playback import play_audio

    logger.info("Loading states from the database...")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM guild_settings")
    for row in cursor.fetchall():
        guild_id = row["guild_id"]
        state = get_guild_state(guild_id)
        player = state.music_player

        state.locale = Locale.EN_X_KAWAII if row["kawaii_mode"] else Locale.EN_US
        state.controller_channel_id = row["controller_channel_id"]
        state.controller_message_id = row["controller_message_id"]
        state._24_7_mode = row["is_24_7"]
        player.autoplay_enabled = row["autoplay"]
        player.volume = row["volume"]

    cursor.execute("SELECT * FROM allowlist")
    for row in cursor.fetchall():
        state = get_guild_state(row["guild_id"])
        state.allowed_channels.add(row["channel_id"])

    cursor.execute("SELECT * FROM playback_state")
    for row in cursor.fetchall():
        guild_id = row["guild_id"]
        guild = bot.get_guild(guild_id)
        if not guild:
            continue

        state = get_guild_state(guild_id)
        player = state.music_player
        try:
            player.current_info = (
                json.loads(row["current_song_json"])
                if row["current_song_json"]
                else None
            )
            player.history = (
                json.loads(row["history_json"]) if row["history_json"] else []
            )
            player.radio_playlist = (
                json.loads(row["radio_playlist_json"])
                if row["radio_playlist_json"]
                else []
            )
            player.loop_current = row["loop_current"]

            queue_items = json.loads(row["queue_json"]) if row["queue_json"] else []
            for item in queue_items:
                await player.queue.put(item)

            if row["voice_channel_id"] and player.current_info:
                channel = guild.get_channel(row["voice_channel_id"])
                if channel and isinstance(channel, discord.VoiceChannel):
                    logger.info(
                        f"[{guild_id}] Resuming: Reconnecting to voice channel '{channel.name}'..."
                    )
                    player.voice_client = await channel.connect()

                    text_channel_id = state.controller_channel_id or (
                        channel.last_message.channel.id if channel.last_message else 0
                    )
                    player.text_channel = bot.get_channel(text_channel_id)

                    timestamp = row["playback_timestamp"]
                    bot.loop.create_task(
                        play_audio(guild_id, seek_time=timestamp, is_a_loop=True)
                    )
        except Exception as e:
            logger.error(f"Failed to restore state for server {guild_id}: {e}")

    conn.close()
    logger.info("State loading completed.")
