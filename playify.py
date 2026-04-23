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
from i18n_translator import I18nTranslator, Locale
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
from dotenv import load_dotenv

load_dotenv()


def init_db():
    """Initialize the SQLite database and create tables if they do not exist."""
    conn = sqlite3.connect("playify_state.db")
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


try:
    process_pool = ProcessPoolExecutor(max_workers=psutil.cpu_count(logical=False))
except NotImplementedError:  # Some systems may not support logical=False
    process_pool = ProcessPoolExecutor(max_workers=os.cpu_count())

SILENT_MESSAGES = True
IS_PUBLIC_VERSION = False

# --- Logging ---

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

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

I18N_DIR = os.path.join(os.path.dirname(__file__), "i18n")
translator = I18nTranslator(default_locale=Locale.EN_US, translations_dir=I18N_DIR)

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
    conn = sqlite3.connect("playify_state.db")
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
    logger.info("Loading states from the database...")
    conn = sqlite3.connect("playify_state.db")
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


# --- UPDATED CLASS FOR LAZY PLAYLIST MANAGEMENT ---
class LazySearchItem:
    """
    An object representing a song from a playlist that has not yet been searched for.
    The search (resolution) on SoundCloud is only performed when the song is
    about to be played. It intelligently tries to avoid 30s previews.
    """

    def __init__(
        self,
        query_dict: dict,
        requester: discord.User,
        original_platform: str = "SoundCloud",
    ):
        self.query_dict = query_dict
        self.requester = requester
        self.resolved_info = None
        self.search_lock = asyncio.Lock()
        self.original_platform = (
            original_platform  # Remembers the origin (Spotify, etc.)
        )

        self.title = self.query_dict.get("name", "Pending resolution...")
        self.artist = self.query_dict.get("artist", "Unknown Artist")

        self.url = "#"
        self.webpage_url = "#"
        self.duration = 0
        self.thumbnail = None
        self.source_type = "lazy"

    async def resolve(self):
        """
        Performs the search and stores the full result.
        It intelligently filters out 30-second previews.
        The search is done on YouTube if IS_PUBLIC_VERSION is False, otherwise on SoundCloud.
        Only performs the search once thanks to the lock and check.
        """
        async with self.search_lock:
            if self.resolved_info:
                return self.resolved_info

            if IS_PUBLIC_VERSION:
                search_prefix = "scsearch5:"
                platform_name = "SoundCloud"
            else:
                search_prefix = "ytsearch5:"
                platform_name = "YouTube"

            search_term = f"{self.title} {self.artist}"
            logger.info(f"[LazyResolve] Resolving on {platform_name}: '{search_term}'")
            try:
                search_query = f"{search_prefix}{sanitize_query(search_term)}"

                info = await fetch_video_info_with_retry(
                    search_query, {"noplaylist": True, "extract_flat": True}
                )

                entries = info.get("entries")
                if not entries:
                    raise ValueError(f"No results found on {platform_name}.")

                best_video_info = None
                if platform_name == "SoundCloud":
                    for video in entries:
                        if video.get("duration", 0) > 40:
                            best_video_info = video
                            logger.info(
                                f"[LazyResolve] Found suitable full track: '{video.get('title')}'"
                            )
                            break

                if not best_video_info:
                    logger.info(
                        f"[LazyResolve] Using first result from {platform_name}."
                    )
                    best_video_info = entries[0]

                full_video_info = await fetch_video_info_with_retry(
                    best_video_info["url"], {"noplaylist": True}
                )

                full_video_info["requester"] = self.requester
                full_video_info["original_platform"] = self.original_platform
                self.resolved_info = full_video_info
                return self.resolved_info

            except Exception as e:
                logger.error(
                    f"[LazyResolve] Failed to resolve '{search_term}' on {platform_name}: {e}"
                )
                self.resolved_info = {"error": True, "title": search_term}
                return self.resolved_info


class AddSongModal(discord.ui.Modal):
    def __init__(self, bot: commands.Bot, guild_id: int):
        super().__init__(title=get_messages("controller.label.add_song", guild_id))
        self.bot = bot
        self.guild_id = guild_id
        self.query_input = discord.ui.TextInput(
            label=get_messages("add_song_modal.label", self.guild_id),
            placeholder=get_messages("add_song_modal.placeholder", self.guild_id),
            style=discord.TextStyle.short,
            required=True,
        )
        self.add_item(self.query_input)

    async def on_submit(self, interaction: discord.Interaction):
        # We find the /play command and execute it with the user's query
        play_command = self.bot.tree.get_command("play")
        if play_command:
            # The /play command itself will handle deferring the interaction.
            # This is now the correct way to pass the interaction along.
            await play_command.callback(interaction, query=self.query_input.value)
        else:
            await interaction.response.send_message(
                get_messages(
                    "command.error.not_found", interaction.guild_id, command_name="play"
                ),
                ephemeral=True,
            )


class JumpToSelect(discord.ui.Select):
    """The dropdown menu for jumping to a song, designed for pagination."""

    def __init__(self, tracks_on_page: list, page_offset: int, guild_id: int):
        options = []
        for i, track in enumerate(tracks_on_page):
            global_index = i + page_offset
            display_info = get_track_display_info(track)
            title = display_info.get("title", "Unknown Title")

            options.append(
                discord.SelectOption(
                    label=f"{global_index + 1}. {title}"[:100], value=str(global_index)
                )
            )

            super().__init__(
                placeholder=get_messages("jumpto.placeholder", guild_id),
                min_values=1,
                max_values=1,
                options=options,
            )

    async def callback(self, interaction: discord.Interaction):
        guild_id = interaction.guild_id
        state = get_guild_state(guild_id)
        music_player = state.music_player
        vc = music_player.voice_client

        if not vc or not (vc.is_playing() or vc.is_paused()):
            return await interaction.response.defer()

        selected_index = int(self.values[0])

        async with music_player.queue_lock:
            queue_list = list(music_player.queue._queue)
            if not 0 <= selected_index < len(queue_list):
                return await interaction.response.defer()

            tracks_to_skip = queue_list[:selected_index]
            music_player.history.extend(tracks_to_skip)
            logger.info(
                f"[{guild_id}] JumpTo: Added {len(tracks_to_skip)} skipped tracks to history."
            )

            new_queue_list = queue_list[selected_index:]

            new_queue = asyncio.Queue()
            for item in new_queue_list:
                await new_queue.put(item)
            music_player.queue = new_queue

        await interaction.response.defer()
        await interaction.delete_original_response()

        music_player.manual_stop = True
        await safe_stop(vc)


class JumpToView(View):
    """The interactive view for the /jumpto command, with pagination."""

    def __init__(self, interaction: discord.Interaction, all_tracks: list):
        super().__init__(timeout=300.0)
        self.interaction = interaction
        self.guild_id = interaction.guild_id
        self.all_tracks = all_tracks
        self.current_page = 0
        self.items_per_page = 25
        self.total_pages = (
            math.ceil(len(self.all_tracks) / self.items_per_page)
            if self.all_tracks
            else 1
        )

    async def update_view(self):
        """Asynchronously hydrates tracks for the current page and rebuilds components."""
        self.clear_items()

        start_index = self.current_page * self.items_per_page
        end_index = start_index + self.items_per_page
        tracks_on_page = self.all_tracks[start_index:end_index]

        tracks_to_hydrate = [
            t
            for t in tracks_on_page
            if isinstance(t, dict)
            and (not t.get("title") or t.get("title") == "Unknown Title")
            and not t.get("source_type") == "file"
        ]

        if tracks_to_hydrate:
            # Minor log correction
            logger.info(
                f"JumpToView: Hydrating {len(tracks_to_hydrate)} tracks for page {self.current_page + 1}"
            )
            tasks = [fetch_meta(track["url"], None) for track in tracks_to_hydrate]
            hydrated_results = await asyncio.gather(*tasks)
            hydrated_map = {res["url"]: res for res in hydrated_results if res}
            for track in tracks_on_page:
                if isinstance(track, dict) and track["url"] in hydrated_map:
                    track["title"] = hydrated_map[track["url"]].get(
                        "title", "Unknown Title"
                    )

        # We make sure to add the correct select menu.
        self.add_item(
            JumpToSelect(
                tracks_on_page, page_offset=start_index, guild_id=self.guild_id
            )
        )

        if self.total_pages > 1:
            prev_button = Button(
                label=get_messages("queue_button.previous", self.guild_id),
                style=ButtonStyle.secondary,
                disabled=(self.current_page == 0),
            )
            next_button = Button(
                label=get_messages("queue_button.next", self.guild_id),
                style=ButtonStyle.secondary,
                disabled=(self.current_page >= self.total_pages - 1),
            )

            prev_button.callback = self.prev_page
            next_button.callback = self.next_page

            self.add_item(prev_button)
            self.add_item(next_button)

    async def prev_page(self, interaction: discord.Interaction):
        await interaction.response.defer()
        if self.current_page > 0:
            self.current_page -= 1
        await self.update_view()
        await interaction.edit_original_response(view=self)

    async def next_page(self, interaction: discord.Interaction):
        await interaction.response.defer()
        if self.current_page < self.total_pages - 1:
            self.current_page += 1
        await self.update_view()
        await interaction.edit_original_response(view=self)


class MusicControllerView(View):
    def __init__(self, bot, guild_id):
        super().__init__(timeout=None)
        self.bot = bot
        self.guild_id = guild_id

        # Default emoji mapping (for normal mode)
        self.default_emojis = {
            "controller_previous": "⏮️",
            "controller_pause": "⏸️",
            "controller_resume": "▶️",
            "controller_skip": "⏭️",
            "controller_stop": "⏹️",
            "controller_add_song": "➕",
            "controller_shuffle": "🔀",
            "controller_loop": "🔁",
            "controller_autoplay": "➡️",
            "controller_vol_down": "🔉",
            "controller_vol_up": "🔊",
            "controller_lyrics": "📜",
            "controller_karaoke": "🎤",
            "controller_queue": "📜",
            "controller_jump_to_song": "⤵️",
        }
        # The update_buttons method is called to set the initial state of the buttons
        self.update_buttons()

    def update_buttons(self):
        """Dynamically updates button labels, emojis, and states."""
        music_player = get_player(self.guild_id)
        vc = music_player.voice_client
        is_playing = vc and (vc.is_playing() or vc.is_paused())
        is_paused = vc and vc.is_paused()
        is_kawaii = get_mode(self.guild_id)

        def get_label(key):
            return get_messages(key, self.guild_id)

        for child in self.children:
            if not hasattr(child, "custom_id"):
                continue

            custom_id = child.custom_id

            if custom_id == "controller_pause":
                child.label = (
                    get_label("controller.label.resume")
                    if is_paused
                    else get_label("controller.label.pause")
                )
            elif custom_id == "controller_jump_to_song":
                child.label = get_label("controller.label.jump_to")
            elif custom_id == "controller_vol_down":
                child.label = get_label("controller.label.vol_down")
            elif custom_id == "controller_vol_up":
                child.label = get_label("controller.label.vol_up")
            else:
                action = custom_id.replace("controller_", "")
                label_key = f"controller.label.{action}"
                child.label = get_label(label_key)

            if is_kawaii:
                child.emoji = None
            else:
                if custom_id == "controller_pause":
                    child.emoji = (
                        self.default_emojis["controller_resume"]
                        if is_paused
                        else self.default_emojis["controller_pause"]
                    )
                else:
                    child.emoji = self.default_emojis.get(custom_id)

        pause_button = discord.utils.get(self.children, custom_id="controller_pause")
        if pause_button:
            pause_button.style = (
                ButtonStyle.success if is_paused else ButtonStyle.secondary
            )

        loop_button = discord.utils.get(self.children, custom_id="controller_loop")
        if loop_button:
            loop_button.style = (
                ButtonStyle.success
                if music_player.loop_current
                else ButtonStyle.secondary
            )

        autoplay_button = discord.utils.get(
            self.children, custom_id="controller_autoplay"
        )
        if autoplay_button:
            autoplay_button.style = (
                ButtonStyle.success
                if music_player.autoplay_enabled
                else ButtonStyle.secondary
            )

        for child in self.children:
            if hasattr(child, "custom_id") and child.custom_id not in [
                "controller_stop",
                "controller_add_song",
            ]:
                child.disabled = not is_playing

        stop_button = discord.utils.get(self.children, custom_id="controller_stop")
        if stop_button:
            stop_button.disabled = False
        add_song_button = discord.utils.get(
            self.children, custom_id="controller_add_song"
        )
        if add_song_button:
            add_song_button.disabled = False

    @discord.ui.button(
        style=ButtonStyle.primary, custom_id="controller_previous", row=0
    )
    async def previous_button(self, interaction: discord.Interaction, button: Button):
        music_player = get_player(interaction.guild_id)
        guild_id = interaction.guild_id
        vc = interaction.guild.voice_client
        if not vc or not (vc.is_playing() or vc.is_paused()):
            return await interaction.response.defer()
        if music_player.loop_current:
            music_player.is_seeking, music_player.seek_info = True, 0
            await safe_stop(vc)
            return await interaction.response.defer()
        RESTART_THRESHOLD, current_playback_time = 5, 0
        if vc.is_playing() and music_player.playback_started_at:
            current_playback_time = music_player.start_time + (
                (time.time() - music_player.playback_started_at)
                * music_player.playback_speed
            )
        elif vc.is_paused():
            current_playback_time = music_player.start_time
        if current_playback_time > RESTART_THRESHOLD:
            music_player.is_seeking, music_player.seek_info = True, 0
            await safe_stop(vc)
            return await interaction.response.defer()

        # Using get_track_display_info for logs to avoid crashing.
        logger.warning(
            "=" * 20 + f" [DEBUG-PREVIOUS] INITIATED in Guild {guild_id} " + "=" * 20
        )
        history_before = [
            get_track_display_info(item).get("title", "N/A")
            for item in music_player.history
        ]
        queue_before = [
            get_track_display_info(item).get("title", "N/A")
            for item in list(music_player.queue._queue)
        ]
        current_song_title = (
            get_track_display_info(music_player.current_info).get("title", "N/A")
            if music_player.current_info
            else "N/A"
        )

        logger.info(
            f"[DEBUG-PREVIOUS] State BEFORE: Current Song='{current_song_title}', History Size={len(history_before)}, Queue Size={len(queue_before)}"
        )
        logger.info(f"[DEBUG-PREVIOUS] History Content: {history_before[-5:]}")

        async with music_player.queue_lock:
            if len(music_player.history) < 2:
                logger.warning("[DEBUG-PREVIOUS] Aborted: Not enough history.")
                return await interaction.response.send_message(
                    get_messages("player.history.empty", self.guild_id),
                    ephemeral=True,
                    silent=True,
                )

            rest_of_queue = list(music_player.queue._queue)
            logger.info(
                f"[DEBUG-PREVIOUS] Copied 'rest_of_queue' (size {len(rest_of_queue)})"
            )

            # The main logic remains the same, it is correct.
            current_song_popped = music_player.history.pop()
            previous_song_popped = music_player.history.pop()

            popped_current_title = get_track_display_info(current_song_popped).get(
                "title", "N/A"
            )
            popped_previous_title = get_track_display_info(previous_song_popped).get(
                "title", "N/A"
            )
            logger.info(
                f"[DEBUG-PREVIOUS] Popped: current='{popped_current_title}', previous='{popped_previous_title}'"
            )

            new_queue_items = [
                previous_song_popped,
                current_song_popped,
            ] + rest_of_queue
            logger.info(
                f"[DEBUG-PREVIOUS] Reconstructed 'new_queue_items' (new size should be {len(rest_of_queue) + 2})"
            )

            new_queue = asyncio.Queue()
            for item in new_queue_items:
                await new_queue.put(item)

            music_player.queue = new_queue

            queue_after_size = music_player.queue.qsize()
            logger.warning(
                f"[DEBUG-PREVIOUS] State AFTER: New Queue Size={queue_after_size}"
            )
            # --- CORRECTION: The size comparison was incorrect ---
            if (
                queue_after_size != len(queue_before) + 1
            ):  # We put 2 songs back in the queue and removed 1 (the next one)
                logger.error(f"[DEBUG-PREVIOUS] POTENTIAL BUG: Queue size mismatch!")

        music_player.manual_stop = True
        await safe_stop(vc)
        await interaction.response.defer()

    @discord.ui.button(style=ButtonStyle.secondary, custom_id="controller_pause", row=0)
    async def pause_button(self, interaction: discord.Interaction, button: Button):
        music_player = get_player(interaction.guild_id)
        vc = music_player.voice_client
        if not vc or not (vc.is_playing() or vc.is_paused()):
            return await interaction.response.defer()
        if vc.is_paused():
            vc.resume()
            if music_player.playback_started_at is None:
                music_player.playback_started_at = time.time()
        else:
            vc.pause()
            if music_player.playback_started_at:
                music_player.start_time += (
                    time.time() - music_player.playback_started_at
                ) * music_player.playback_speed
                music_player.playback_started_at = None
        await update_controller(self.bot, interaction.guild_id)
        await interaction.response.defer()

    @discord.ui.button(style=ButtonStyle.primary, custom_id="controller_skip", row=0)
    async def skip_button(self, interaction: discord.Interaction, button: Button):
        music_player = get_player(interaction.guild_id)
        vc = music_player.voice_client

        if not vc or not (vc.is_playing() or vc.is_paused()):
            return await interaction.response.defer()

        if music_player.lyrics_task and not music_player.lyrics_task.done():
            music_player.lyrics_task.cancel()

        if music_player.loop_current:
            await safe_stop(vc)
        else:
            music_player.manual_stop = True
            await safe_stop(vc)

        await interaction.response.defer()

    @discord.ui.button(style=ButtonStyle.danger, custom_id="controller_stop", row=0)
    async def stop_button(self, interaction: discord.Interaction, button: Button):
        guild_id = interaction.guild_id
        state = get_guild_state(guild_id)
        music_player = state.music_player

        # Defer the response immediately
        await interaction.response.defer()

        if music_player.lyrics_task and not music_player.lyrics_task.done():
            music_player.lyrics_task.cancel()

        vc = music_player.voice_client
        if vc and vc.is_connected():
            # Stop playback and kill FFmpeg
            await safe_stop(vc)

            # Cancel the main playback task
            if music_player.current_task and not music_player.current_task.done():
                music_player.current_task.cancel()

            # Disconnect from the voice channel
            await vc.disconnect()

            # Fully reset the player state for the server
            clear_audio_cache(guild_id)
            get_guild_state(guild_id).music_player = MusicPlayer()
            logger.info(
                f"[{guild_id}] Player state fully reset via controller stop button."
            )

            # Update the controller to show the idle state
            await update_controller(self.bot, guild_id)

    @discord.ui.button(
        style=ButtonStyle.success, custom_id="controller_add_song", row=0
    )
    async def add_song_button(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(
            AddSongModal(self.bot, interaction.guild_id)
        )

    @discord.ui.button(
        style=ButtonStyle.secondary, custom_id="controller_shuffle", row=1
    )
    async def shuffle_button(self, interaction: discord.Interaction, button: Button):
        music_player = get_player(interaction.guild_id)
        async with music_player.queue_lock:
            if music_player.queue.empty():
                return await interaction.response.send_message(
                    get_messages("queue_empty", self.guild_id),
                    ephemeral=True,
                    silent=True,
                )
            queue_list = list(music_player.queue._queue)
            random.shuffle(queue_list)
            new_queue = asyncio.Queue()
            for item in queue_list:
                await new_queue.put(item)
            music_player.queue = new_queue
        await update_controller(self.bot, interaction.guild_id)
        await interaction.response.defer()

    @discord.ui.button(style=ButtonStyle.secondary, custom_id="controller_loop", row=1)
    async def loop_button(self, interaction: discord.Interaction, button: Button):
        music_player = get_player(interaction.guild_id)
        music_player.loop_current = not music_player.loop_current
        await update_controller(self.bot, interaction.guild_id)
        await interaction.response.defer()

    @discord.ui.button(
        style=ButtonStyle.secondary, custom_id="controller_autoplay", row=1
    )
    async def autoplay_button(self, interaction: discord.Interaction, button: Button):
        music_player = get_player(interaction.guild_id)
        music_player.autoplay_enabled = not music_player.autoplay_enabled
        await update_controller(self.bot, interaction.guild_id)
        await interaction.response.defer()

    @discord.ui.button(
        style=ButtonStyle.secondary, custom_id="controller_vol_down", row=1
    )
    async def volume_down_button(
        self, interaction: discord.Interaction, button: Button
    ):
        music_player, vc = (
            get_player(interaction.guild_id),
            interaction.guild.voice_client,
        )
        new_volume = max(0, music_player.volume - 0.1)
        music_player.volume = new_volume
        if vc and vc.source and isinstance(vc.source, discord.PCMVolumeTransformer):
            vc.source.volume = new_volume
        await update_controller(self.bot, interaction.guild_id)
        await interaction.response.defer()

    @discord.ui.button(
        style=ButtonStyle.secondary, custom_id="controller_vol_up", row=1
    )
    async def volume_up_button(self, interaction: discord.Interaction, button: Button):
        music_player, vc = (
            get_player(interaction.guild_id),
            interaction.guild.voice_client,
        )
        new_volume = min(2.0, music_player.volume + 0.1)
        music_player.volume = new_volume
        if vc and vc.source and isinstance(vc.source, discord.PCMVolumeTransformer):
            vc.source.volume = new_volume
        await update_controller(self.bot, interaction.guild_id)
        await interaction.response.defer()

    # --- ROW 2: LYRICS/KARAOKE CONTROLS ---
    @discord.ui.button(
        style=ButtonStyle.secondary, custom_id="controller_lyrics", row=2
    )
    async def lyrics_button(self, interaction: discord.Interaction, button: Button):
        lyrics_command = self.bot.tree.get_command("lyrics")
        if lyrics_command:
            await lyrics_command.callback(interaction)
        else:
            await interaction.response.send_message(
                get_messages(
                    "command.error.not_found",
                    interaction.guild_id,
                    command_name=get_messages(
                        "controller.label.lyrics", interaction.guild_id
                    ),
                ),
                ephemeral=True,
                silent=True,
            )

    @discord.ui.button(
        style=ButtonStyle.secondary, custom_id="controller_karaoke", row=2
    )
    async def karaoke_button(self, interaction: discord.Interaction, button: Button):
        karaoke_command = self.bot.tree.get_command("karaoke")
        if karaoke_command:
            await karaoke_command.callback(interaction)
        else:
            await interaction.response.send_message(
                get_messages(
                    "command.error.not_found",
                    interaction.guild_id,
                    command_name=get_messages(
                        "controller.label.karaoke", interaction.guild_id
                    ),
                ),
                ephemeral=True,
                silent=True,
            )

    @discord.ui.button(style=ButtonStyle.primary, custom_id="controller_queue", row=2)
    async def queue_button(self, interaction: discord.Interaction, button: Button):
        queue_command = self.bot.tree.get_command("queue")
        if queue_command:
            await queue_command.callback(interaction)
        else:
            await interaction.response.send_message(
                get_messages(
                    "command.error.not_found",
                    interaction.guild_id,
                    command_name="queue",
                ),
                ephemeral=True,
                silent=True,
            )

    @discord.ui.button(
        style=ButtonStyle.secondary, custom_id="controller_jump_to_song", row=2
    )
    async def jump_to_song_button(
        self, interaction: discord.Interaction, button: Button
    ):
        music_player = get_player(interaction.guild_id)
        if music_player.queue.empty():
            await interaction.response.send_message(
                get_messages("queue_empty", interaction.guild_id),
                ephemeral=True,
                silent=True,
            )
            return

        jumpto_command = self.bot.tree.get_command("jumpto")
        if jumpto_command:
            await jumpto_command.callback(interaction)
        else:
            await interaction.response.send_message(
                get_messages(
                    "command.error.not_found",
                    interaction.guild_id,
                    command_name="jumpto",
                ),
                ephemeral=True,
                silent=True,
            )


async def create_status_embed(guild_id: int) -> Embed:
    """Creates a small embed showing the status of loop, 24/7, and autoplay modes."""
    state = get_guild_state(guild_id)
    music_player = state.music_player
    state = get_guild_state(guild_id)
    is_kawaii = state.locale == Locale.EN_X_KAWAII

    status_lines = []
    if music_player.loop_current:
        status_lines.append(get_messages("queue_status_loop", guild_id))
    if get_guild_state(guild_id)._24_7_mode:
        mode_24_7 = "Auto" if music_player.autoplay_enabled else "Normal"
        status_lines.append(get_messages("queue_status_24_7", guild_id, mode=mode_24_7))
    elif music_player.autoplay_enabled:
        status_lines.append(get_messages("queue_status_autoplay", guild_id))

    status_description = (
        "\n".join(status_lines)
        if status_lines
        else get_messages("queue_status_none", guild_id)
    )

    embed = Embed(
        title=get_messages("queue_status_title", guild_id),
        description=status_description,
        color=0xB5EAD7 if is_kawaii else discord.Color.blue(),
    )
    return embed


async def create_controller_embed(bot, guild_id):
    state = get_guild_state(guild_id)
    music_player = state.music_player
    is_kawaii = state.locale == Locale.EN_X_KAWAII
    vc = music_player.voice_client
    is_connected = vc and vc.is_connected()
    is_playing = is_connected and music_player.current_info

    if not is_playing:
        if not is_connected:
            description = get_messages("controller.idle.not_connected", guild_id)
            embed = Embed(
                title=get_messages("controller.title", guild_id),
                description=description,
                color=0x36393F,
            )
        else:
            embed = Embed(
                title=get_messages("controller.title", guild_id),
                description=get_messages("controller.idle.description", guild_id),
                color=0x36393F,
            )
        embed.set_image(url="https://i.imgur.com/vDusBWD.png")
        embed.set_footer(text=get_messages("controller.footer.idle", guild_id))
        return embed

    info = music_player.current_info
    title = info.get("title", get_messages("player.unknown_title", guild_id))
    thumbnail = info.get("thumbnail")
    requester = info.get("requester", bot.user)
    artist = info.get("uploader", get_messages("player.unknown_artist", guild_id))

    is_24_7_normal = (
        get_guild_state(guild_id)._24_7_mode and not music_player.autoplay_enabled
    )

    queue_snapshot = []
    if is_24_7_normal and music_player.radio_playlist:
        current_url = (
            music_player.current_info.get("url") if music_player.current_info else None
        )
        try:
            current_index = [t.get("url") for t in music_player.radio_playlist].index(
                current_url
            )
            queue_snapshot = (
                music_player.radio_playlist[current_index + 1 :]
                + music_player.radio_playlist[:current_index]
            )
        except (ValueError, IndexError):
            queue_snapshot = list(music_player.queue._queue)
    else:
        queue_snapshot = list(music_player.queue._queue)

    tracks_to_display = queue_snapshot[:5]

    lazy_items_to_resolve = [
        item
        for item in tracks_to_display
        if isinstance(item, LazySearchItem) and not item.resolved_info
    ]
    if lazy_items_to_resolve:
        await asyncio.gather(*[item.resolve() for item in lazy_items_to_resolve])

    tracks_to_hydrate = [
        t
        for t in tracks_to_display
        if isinstance(t, dict)
        and (not t.get("duration", 0) > 0 or "video #" in t.get("title", ""))
        and not t.get("source_type") == "file"
    ]
    if tracks_to_hydrate:
        tasks = [fetch_meta(track["url"], None) for track in tracks_to_hydrate]
        hydrated_results = await asyncio.gather(*tasks)
        hydrated_map = {res["url"]: res for res in hydrated_results if res}
        for track in tracks_to_display:
            if isinstance(track, dict) and track.get("url") in hydrated_map:
                track.update(hydrated_map[track["url"]])

    next_song_text = get_messages("controller.nothing_next.title", guild_id)

    display_info = {}

    if tracks_to_display:
        next_song = tracks_to_display[0]
        display_info = get_track_display_info(next_song)
        next_title, next_duration, next_url = (
            display_info.get("title"),
            format_duration(display_info.get("duration")),
            display_info.get("webpage_url"),
        )

        source_type = display_info.get("source_type", "default")
        format_key = f"controller.next_up.format.{source_type}"
        # Fallback to default if a specific key doesn't exist
        if translator.t(format_key, locale=state.locale.value) == format_key:
            format_key = "controller.next_up.format.default"

        next_song_text = get_messages(
            format_key, guild_id, title=next_title, url=next_url, duration=next_duration
        )

    queue_list_text = []
    if len(tracks_to_display) > 1:
        for i, item in enumerate(tracks_to_display[1:5], start=2):
            display_info = get_track_display_info(item)
            item_title = display_info.get("title", "Titre inconnu")
            item_duration = format_duration(display_info.get("duration"))

            display_title = (
                (item_title[:38] + "..") if len(item_title) > 40 else item_title
            )

            source_type = display_info.get("source_type", "default")
            line_key = f"controller.queue.line_display.{source_type}"
            if translator.t(line_key, locale=state.locale.value) == line_key:
                line_key = "controller.queue.line_display.default"

            display_line = get_messages(
                line_key,
                guild_id,
                title=display_title,
                duration=item_duration,
                url=display_info.get("webpage_url", "#"),
            )
            queue_list_text.append(
                get_messages(
                    "controller.queue_list.line_format.default",
                    guild_id,
                    i=i,
                    display_line=display_line,
                )
            )

    if len(tracks_to_display) == 1:
        queue_list_text.append(
            get_messages("controller.no_other_songs.title", guild_id)
        )
    elif not tracks_to_display:
        queue_list_text.append(get_messages("controller.queue_empty.title", guild_id))

    queue_list_text.reverse()

    description = "\n".join(queue_list_text)
    embed = Embed(
        title=get_messages("controller.title", guild_id),
        description=description,
        color=0xB5EAD7 if is_kawaii else discord.Color.blue(),
    )
    embed.add_field(
        name=get_messages("controller.next_up.title", guild_id),
        value=next_song_text,
        inline=False,
    )

    now_playing_title_display = (
        f"**[{title}]({info.get('webpage_url', info.get('url', '#'))})**"
        if info.get("source_type") != "file"
        else get_messages("queue.now_playing_format.file", guild_id, title=title)
    )
    now_playing_value = get_messages(
        "controller.now_playing.value",
        guild_id,
        now_playing_title_display=now_playing_title_display,
        artist=artist,
        requester_mention=requester.mention,
        channel_name=vc.channel.name,
    )
    embed.add_field(
        name=get_messages("controller.now_playing.title", guild_id),
        value=now_playing_value,
        inline=False,
    )

    if thumbnail:
        embed.set_thumbnail(url=thumbnail)

    status_lines = []
    if music_player.loop_current:
        status_lines.append(get_messages("queue_status_loop", guild_id))
    if get_guild_state(guild_id)._24_7_mode:
        mode_24_7 = "Auto" if music_player.autoplay_enabled else "Normal"
        status_lines.append(get_messages("queue_status_24_7", guild_id, mode=mode_24_7))
    elif music_player.autoplay_enabled:
        status_lines.append(get_messages("queue_status_autoplay", guild_id))
    if status_lines:
        embed.add_field(
            name=get_messages("queue_status_title", guild_id),
            value="\n".join(status_lines),
            inline=False,
        )

    count_for_display = (
        len(music_player.radio_playlist)
        if is_24_7_normal and music_player.radio_playlist
        else len(queue_snapshot)
    )

    dynamic_footer_info = ""
    active_filters = state.server_filters

    if active_filters:
        filter_name = next(iter(active_filters))
        display_name = get_messages(f"filter.name.{filter_name}", guild_id)
        dynamic_footer_info = get_messages(
            "controller.footer.filter", guild_id, filter_name=display_name
        )
        if is_kawaii:
            dynamic_footer_info += " ✨"

    elif music_player.current_info:
        source_type = music_player.current_info.get("source_type")

        url = music_player.current_info.get("webpage_url", "").lower()
        original_platform = music_player.current_info.get("original_platform")

        if source_type == "file":
            dynamic_footer_info = get_messages(
                "controller.footer.file_source", guild_id
            )

        elif original_platform:
            platform_mode = "kaomoji" if is_kawaii else "display"
            formatted_platform_name = original_platform.lower().replace(" ", "_")
            platform_key = f"platform.{platform_mode}.{formatted_platform_name}"
            platform_display_name = get_messages(platform_key, guild_id)
            dynamic_footer_info = get_messages(
                "controller.footer.source", guild_id, platform=platform_display_name
            )

        elif "youtube.com" in url or "youtu.be" in url:
            dynamic_footer_info = get_messages(
                "controller.footer.youtube_source", guild_id
            )
        elif "soundcloud.com" in url:
            dynamic_footer_info = get_messages(
                "controller.footer.soundcloud_source", guild_id
            )
        elif "twitch.tv" in url:
            dynamic_footer_info = get_messages(
                "controller.footer.twitch_source", guild_id
            )
        elif "bandcamp.com" in url:
            dynamic_footer_info = get_messages(
                "controller.footer.bandcamp_source", guild_id
            )
        else:
            ping_ms = round(bot.latency * 1000)
            dynamic_footer_info = get_messages(
                "controller.footer.ping", guild_id, ping_ms=ping_ms
            )

    else:
        ping_ms = round(bot.latency * 1000)
        dynamic_footer_info = get_messages(
            "controller.footer.ping", guild_id, ping_ms=ping_ms
        )

    footer_text = ""
    volume_percent = int(music_player.volume * 100)

    if count_for_display > 0 or music_player.current_info:
        if count_for_display == 0:
            footer_text = get_messages(
                "controller.footer.format_last_song",
                guild_id,
                dynamic_info=dynamic_footer_info,
                volume=volume_percent,
            )
        else:
            footer_text = get_messages(
                "controller.footer.format",
                guild_id,
                count=count_for_display,
                dynamic_info=dynamic_footer_info,
                volume=volume_percent,
            )
    else:
        footer_text = get_messages("controller.footer.idle", guild_id)

    embed.set_footer(text=footer_text)
    return embed


async def update_controller(
    bot, guild_id, interaction: Optional[discord.Interaction] = None
):
    """
    Fetches, generates, and edits/sends the controller message.
    Can now handle both background updates and direct interaction responses.
    """
    if not get_guild_state(guild_id).controller_channel_id:
        # If the controller isn't set up, we can't do anything.
        # But if we're responding to an interaction, we must complete it.
        if interaction and not interaction.response.is_done():
            # Fallback: just delete the "thinking" message if controller isn't set.
            await interaction.delete_original_response()
        return

    try:
        channel_id = get_guild_state(guild_id).controller_channel_id
        channel = bot.get_channel(channel_id)
        if not channel:
            logger.warning(
                f"Controller channel {channel_id} not found for guild {guild_id}."
            )
            return

        embed = await create_controller_embed(bot, guild_id)
        view = MusicControllerView(bot, guild_id)

        # --- NOUVELLE LOGIQUE CENTRALE ---
        if interaction:
            # Scénario 1 : On répond directement à une commande.
            # On transforme le message "réfléchit..." en nouveau contrôleur.
            await interaction.edit_original_response(
                content=None, embed=embed, view=view
            )
            message = await interaction.original_response()

            # Si un ancien message de contrôleur existe, on le supprime pour éviter les doublons.
            old_message_id = get_guild_state(guild_id).controller_message_id
            if old_message_id and old_message_id != message.id:
                try:
                    old_message = await channel.fetch_message(old_message_id)
                    await old_message.delete()
                except (discord.NotFound, discord.Forbidden):
                    pass  # Déjà parti, pas de problème.

            # On sauvegarde l'ID du nouveau message comme étant le contrôleur officiel.
            get_guild_state(guild_id).controller_message_id = message.id
        else:
            # Scénario 2 : C'est une mise à jour de fond (ex: fin de chanson).
            # On utilise la logique existante pour modifier le message persistant.
            message_id = get_guild_state(guild_id).controller_message_id
            if message_id:
                try:
                    message = await channel.fetch_message(message_id)
                    await message.edit(embed=embed, view=view)
                except (discord.NotFound, discord.Forbidden):
                    # Le message a été supprimé, on en crée un nouveau.
                    new_message = await channel.send(
                        embed=embed, view=view, silent=True
                    )
                    get_guild_state(guild_id).controller_message_id = new_message.id
            else:
                # Pas d'ID de message stocké, on en crée un nouveau.
                new_message = await channel.send(embed=embed, view=view, silent=True)
                get_guild_state(guild_id).controller_message_id = new_message.id

    except Exception as e:
        logger.error(
            f"Failed to update controller for guild {guild_id}: {e}", exc_info=True
        )


# --- Discord UI Classes (Views & Modals) ---


class SeekModal(discord.ui.Modal):
    def __init__(self, view, guild_id):
        self.view = view
        self.state = get_guild_state(guild_id)
        music_player = self.music_player = get_player(guild_id)
        super().__init__(title=get_messages("seek.modal_title", guild_id))

        self.timestamp_input = discord.ui.TextInput(
            label=get_messages("seek.modal.label", guild_id),
            placeholder=get_messages("seek.modal.placeholder", guild_id),
            required=True,
        )
        self.add_item(self.timestamp_input)

    async def on_submit(self, interaction: discord.Interaction):
        target_seconds = parse_time(self.timestamp_input.value)
        if target_seconds is None:
            await interaction.response.send_message(
                get_messages("seek.fail_invalid_time", self.view.guild_id),
                ephemeral=True,
                silent=SILENT_MESSAGES,
            )
            return

        self.music_player.is_seeking = True
        self.music_player.seek_info = target_seconds
        self.music_player.voice_client.stop()

        await self.view.update_embed(interaction, jumped=True)
        # No need for interaction.response.send_message here as update_embed already handles it.


class SeekView(View):
    REWIND_AMOUNT = 15
    FORWARD_AMOUNT = 15

    def __init__(self, interaction: discord.Interaction):
        super().__init__(timeout=300.0)  # 5 minute timeout
        self.interaction = interaction
        self.guild_id = interaction.guild.id
        self.music_player = get_player(self.guild_id)
        self.is_kawaii = get_mode(self.guild_id)
        self.message = None
        self.update_task = None

        # Apply button labels
        self.rewind_button.label = get_messages("seek.button.rewind", self.guild_id)
        self.jump_button.label = get_messages("seek.button.jump_to", self.guild_id)
        self.forward_button.label = get_messages("seek.button.forward", self.guild_id)

    async def start_update_task(self):
        """Starts the background task to update the embed."""
        if self.update_task is None or self.update_task.done():
            self.update_task = asyncio.create_task(self.updater_loop())

    async def updater_loop(self):
        """Loop that updates the message at regular intervals."""
        while not self.is_finished():
            # CORRECTION 1: Delay reduced to 2 seconds for more fluidity
            await asyncio.sleep(2)

            # CORRECTION 2: Only updates if music is currently playing
            # This handles pause/resume automatically
            if (
                self.music_player.voice_client
                and self.music_player.voice_client.is_playing()
            ):
                # We make sure the message still exists before trying to edit it
                if self.message:
                    try:
                        await self.update_embed()
                    except discord.NotFound:
                        # The message has been deleted, stop the task
                        break

    def get_current_time(self) -> int:
        """Calculates the current playback position in seconds."""
        # If the music is paused, return the last known position
        if not self.music_player.voice_client.is_playing():
            return self.music_player.start_time

        # Otherwise, calculate the live position
        if self.music_player.playback_started_at:
            elapsed = time.time() - self.music_player.playback_started_at
            return self.music_player.start_time + (
                elapsed * self.music_player.playback_speed
            )

        return self.music_player.start_time

    async def update_embed(
        self, interaction: discord.Interaction = None, jumped: bool = False
    ):
        """Updates the embed with the progress bar."""
        current_pos = int(self.get_current_time())
        # Make sure current_info is not None
        if not self.music_player.current_info:
            return

        total_duration = self.music_player.current_info.get("duration", 0)

        title = self.music_player.current_info.get(
            "title", get_messages("player.unknown_title", self.guild_id)
        )

        progress_bar = create_progress_bar(current_pos, total_duration, self.guild_id)
        time_display = (
            f"**{format_duration(current_pos)} / {format_duration(total_duration)}**"
        )

        embed = Embed(
            title=get_messages("seek.interface_title", self.guild_id),
            description=f"**{title}**\n\n{progress_bar} {time_display}",
            color=0xB5EAD7 if self.is_kawaii else discord.Color.blue(),
        )
        embed.set_footer(text=get_messages("seek.interface_footer", self.guild_id))

        # If it's a response to a button interaction
        if interaction and not interaction.response.is_done():
            await interaction.response.edit_message(embed=embed, view=self)
        # If it's an update from the background loop
        elif self.message:
            await self.message.edit(embed=embed, view=self)

    @discord.ui.button(style=ButtonStyle.primary, emoji="⏪")
    async def rewind_button(self, interaction: discord.Interaction, button: Button):
        current_time = self.get_current_time()
        target_seconds = max(0, current_time - self.REWIND_AMOUNT)

        self.music_player.is_seeking = True
        self.music_player.seek_info = target_seconds
        self.music_player.voice_client.stop()
        await self.update_embed(interaction, jumped=True)

    @discord.ui.button(style=ButtonStyle.secondary, emoji="✏️")
    async def jump_button(self, interaction: discord.Interaction, button: Button):
        modal = SeekModal(self, self.guild_id)
        await interaction.response.send_modal(modal)

    @discord.ui.button(style=ButtonStyle.primary, emoji="⏩")
    async def forward_button(self, interaction: discord.Interaction, button: Button):
        current_time = self.get_current_time()
        target_seconds = current_time + self.FORWARD_AMOUNT

        self.music_player.is_seeking = True
        self.music_player.seek_info = target_seconds
        self.music_player.voice_client.stop()
        await self.update_embed(interaction, jumped=True)

    async def on_timeout(self):
        if self.update_task:
            self.update_task.cancel()
        if self.message:
            for item in self.children:
                item.disabled = True
            try:
                await self.message.edit(view=self)
            except discord.NotFound:
                pass  # The message has already been deleted


class SearchSelect(discord.ui.Select):
    """The dropdown menu component for the /search command."""

    def __init__(self, search_results: list, guild_id: int):
        self.state = get_guild_state(guild_id)
        is_kawaii = self.state.locale == Locale.EN_X_KAWAII

        options = []
        for i, video in enumerate(search_results):
            options.append(
                discord.SelectOption(
                    label=video.get(
                        "title", get_messages("player.unknown_title", guild_id)
                    )[:100],
                    description=get_messages(
                        "search.result_description",
                        guild_id,
                        artist=video.get(
                            "uploader", get_messages("player.unknown_artist", guild_id)
                        ),
                    )[:100],
                    value=video.get("webpage_url", video.get("url")),
                    emoji="🎵",
                )
            )

        super().__init__(
            placeholder=get_messages("search.placeholder", guild_id),
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        """This is called when the user selects a song."""
        guild_id = interaction.guild_id
        state = get_guild_state(guild_id)
        is_kawaii = state.locale == Locale.EN_X_KAWAII
        state = get_guild_state(guild_id)
        music_player = state.music_player

        selected_url = self.values[0]

        self.disabled = True
        self.placeholder = get_messages("search.selection_made", guild_id)
        await interaction.response.edit_message(view=self.view)

        try:
            ydl_opts_full = {
                "format": "bestaudio/best",
                "quiet": True,
                "no_warnings": True,
                "noplaylist": True,
            }
            video_info = await fetch_video_info_with_retry(
                selected_url, ydl_opts_override=ydl_opts_full
            )

            if not video_info:
                raise Exception("Could not retrieve video information.")

            queue_item = {
                "url": video_info.get("webpage_url", video_info.get("url")),
                "title": video_info.get("title", "Unknown Title"),
                "webpage_url": video_info.get("webpage_url", video_info.get("url")),
                "thumbnail": video_info.get("thumbnail"),
                "is_single": True,
                "requester": interaction.user,
            }
            await music_player.queue.put(queue_item)

            # This line should already exist just above, but we ensure it's used correctly
            video_url = video_info.get("webpage_url", video_info.get("url"))

            if not get_guild_state(guild_id).controller_channel_id:
                embed = Embed(
                    title=get_messages("song_added", guild_id),
                    description=f"[{video_info.get('title', 'Unknown Title')}]({video_url})",
                    color=0xB5EAD7 if is_kawaii else discord.Color.blue(),
                )
                if video_info.get("thumbnail"):
                    embed.set_thumbnail(url=video_info["thumbnail"])
                if is_kawaii:
                    embed.set_footer(text="☆⌒(≧▽° )")
                await interaction.followup.send(silent=SILENT_MESSAGES, embed=embed)
            else:
                await interaction.followup.send(
                    get_messages(
                        "search.added_to_queue_ephemeral",
                        guild_id,
                        title=video_info.get("title", "Unknown Title"),
                    ),
                    ephemeral=True,
                    silent=SILENT_MESSAGES,
                )

            await interaction.followup.send(embed=embed, silent=SILENT_MESSAGES)

            if (
                not music_player.voice_client.is_playing()
                and not music_player.voice_client.is_paused()
            ):
                music_player.suppress_next_now_playing = True
                music_player.current_task = asyncio.create_task(play_audio(guild_id))

        except Exception as e:
            logger.error(f"Error adding track from /search selection: {e}")
            error_embed = Embed(
                description=get_messages("player.error.add_failed", guild_id),
                color=0xFF9AA2 if is_kawaii else discord.Color.red(),
            )
            await interaction.followup.send(
                embed=error_embed, silent=SILENT_MESSAGES, ephemeral=True
            )


class SearchView(View):
    """The view that holds the SearchSelect dropdown."""

    def __init__(self, search_results: list, guild_id: int):
        super().__init__(timeout=300.0)
        self.add_item(SearchSelect(search_results, guild_id))


class LyricsView(View):
    def __init__(self, pages: list, original_embed: Embed, guild_id: int):
        super().__init__(timeout=300.0)
        self.guild_id = guild_id
        self.pages = pages
        self.original_embed = original_embed
        self.current_page = 0

        self.previous_button.label = get_messages(
            "lyrics.button.previous", self.guild_id
        )
        self.next_button.label = get_messages("lyrics.button.next", self.guild_id)
        self.refine_button.label = get_messages("lyrics.button.refine", self.guild_id)

    def update_embed(self):
        self.original_embed.description = self.pages[self.current_page]
        self.original_embed.set_footer(
            text=get_messages(
                "lyrics.embed.footer",
                self.guild_id,
                current_page=self.current_page + 1,
                total_pages=len(self.pages),
            )
        )
        return self.original_embed

    @discord.ui.button(style=discord.ButtonStyle.grey, row=0)
    async def previous_button(self, interaction: discord.Interaction, button: Button):
        if self.current_page > 0:
            self.current_page -= 1

        self.previous_button.disabled = self.current_page == 0
        self.next_button.disabled = False

        await interaction.response.edit_message(embed=self.update_embed(), view=self)

    @discord.ui.button(style=discord.ButtonStyle.grey, row=0)
    async def next_button(self, interaction: discord.Interaction, button: Button):
        if self.current_page < len(self.pages) - 1:
            self.current_page += 1

        self.next_button.disabled = self.current_page == len(self.pages) - 1
        self.previous_button.disabled = False

        await interaction.response.edit_message(embed=self.update_embed(), view=self)

    @discord.ui.button(emoji="✏️", style=discord.ButtonStyle.secondary, row=0)
    async def refine_button(self, interaction: discord.Interaction, button: Button):
        modal = RefineLyricsModal(message_to_edit=interaction.message)
        await interaction.response.send_modal(modal)


class LyricsRetryModal(discord.ui.Modal):
    def __init__(self, original_interaction: discord.Interaction, suggested_query: str):
        guild_id = original_interaction.guild_id
        super().__init__(title=get_messages("lyrics.refine_modal.title", guild_id))
        self.original_interaction = original_interaction
        self.suggested_query = suggested_query
        self.guild_id = original_interaction.guild_id

        self.corrected_query = discord.ui.TextInput(
            label=get_messages("lyrics.refine_modal.label", self.guild_id),
            placeholder=get_messages("lyrics.refine_modal.placeholder", self.guild_id),
            default=self.suggested_query,
            style=discord.TextStyle.short,
        )
        self.add_item(self.corrected_query)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True, ephemeral=True)

        new_query = self.corrected_query.value
        logger.info(f"Retrying lyrics search with new query: '{new_query}'")

        try:
            loop = asyncio.get_running_loop()
            if not genius:
                await interaction.followup.send(
                    get_messages("api.genius.not_configured", interaction.guild_id),
                    silent=SILENT_MESSAGES,
                    ephemeral=True,
                )
                return

            song = await loop.run_in_executor(
                None, lambda: genius.search_song(new_query)
            )

            if not song:
                fail_message = get_messages(
                    "lyrics.error.not_found.description", self.guild_id, query=new_query
                )
                await interaction.followup.send(
                    fail_message.split("\n")[0], silent=SILENT_MESSAGES, ephemeral=True
                )
                return

            raw_lyrics = song.lyrics
            lines = raw_lyrics.split("\n")
            cleaned_lines = [
                line
                for line in lines
                if "contributor" not in line.lower()
                and "lyrics" not in line.lower()
                and "embed" not in line.lower()
            ]
            lyrics = "\n".join(cleaned_lines).strip()

            pages = []
            current_page_content = ""
            for line in lyrics.split("\n"):
                if len(current_page_content) + len(line) + 1 > 1500:
                    pages.append(f"```{current_page_content.strip()}```")
                    current_page_content = ""
                current_page_content += line + "\n"
            if current_page_content.strip():
                pages.append(f"```{current_page_content.strip()}```")

            base_embed = Embed(
                title=get_messages(
                    "lyrics.embed.title", self.guild_id, title=song.title
                ),
                url=song.url,
                color=discord.Color.green(),
            )

            view = LyricsView(pages=pages, original_embed=base_embed)
            initial_embed = view.update_embed()

            view.children[0].disabled = True
            if len(pages) <= 1:
                view.children[1].disabled = True

            message = await self.original_interaction.followup.send(
                silent=SILENT_MESSAGES, embed=initial_embed, view=view, wait=True
            )

            view.message = message

            await interaction.followup.send(
                get_messages("lyrics.success.found", self.guild_id),
                silent=SILENT_MESSAGES,
                ephemeral=True,
            )

        except Exception as e:
            logger.error(f"Error during lyrics retry: {e}")
            await interaction.followup.send(
                get_messages("api.lyrics.generic_fetch_error", self.guild_id),
                silent=SILENT_MESSAGES,
                ephemeral=True,
            )


class LyricsRetryView(discord.ui.View):
    # We add guild_id to the initialization
    def __init__(
        self,
        original_interaction: discord.Interaction,
        suggested_query: str,
        guild_id: int,
    ):
        super().__init__(timeout=180.0)
        self.original_interaction = original_interaction
        self.suggested_query = suggested_query

        # We get the correct label for the button
        button_label = get_messages("lyrics.refine_button", guild_id)

        # We access the button (created by the decorator) and change its label
        self.retry_button.label = button_label

    # The decorator no longer needs the label; it is defined dynamically
    @discord.ui.button(style=discord.ButtonStyle.primary)
    async def retry_button(self, interaction: discord.Interaction, button: Button):
        modal = LyricsRetryModal(
            original_interaction=self.original_interaction,
            suggested_query=self.suggested_query,
        )
        await interaction.response.send_modal(modal)


class KaraokeRetryModal(discord.ui.Modal):
    def __init__(self, original_interaction: discord.Interaction, suggested_query: str):
        super().__init__(
            title=get_messages("karaoke.refine_modal.title", self.guild_id)
        )
        self.original_interaction = original_interaction
        self.suggested_query = suggested_query
        self.guild_id = original_interaction.guild_id
        self.music_player = get_player(self.guild_id)
        self.is_kawaii = get_mode(self.guild_id)

        self.corrected_query = discord.ui.TextInput(
            label=get_messages("karaoke.refine_modal.label", self.guild_id),
            placeholder=get_messages("karaoke.refine_modal.placeholder", self.guild_id),
            default=self.suggested_query,
            style=discord.TextStyle.short,
        )
        self.add_item(self.corrected_query)


# THIS IS THE METHOD THAT WAS MISSING
async def on_submit(self, interaction: discord.Interaction):
    await interaction.response.defer(thinking=True, ephemeral=True)
    new_query = self.corrected_query.value
    logger.info(f"Retrying synced lyrics search with new query: '{new_query}'")

    loop = asyncio.get_running_loop()
    lrc = None
    try:
        lrc = await asyncio.wait_for(
            loop.run_in_executor(None, syncedlyrics.search, new_query), timeout=10.0
        )
    except (asyncio.TimeoutError, Exception) as e:
        logger.error(f"Error during karaoke retry search: {e}")

    # --- START OF FIX ---
    # We check for failure (no LRC found OR LRC found but is empty/invalid) in a single block.
    lyrics_lines = []
    if lrc:
        lyrics_lines = [
            {
                "time": int(m.group(1)) * 60000
                + int(m.group(2)) * 1000
                + int(m.group(3)),
                "text": m.group(4).strip(),
            }
            for line in lrc.splitlines()
            if (m := re.match(r"\[(\d{2}):(\d{2})\.(\d{2,3})\](.*)", line))
        ]

    if not lyrics_lines:
        # 1. DEFINE the failure message before using it.
        fail_message = get_messages(
            "karaoke.not_found_description", self.guild_id, query=new_query
        )
        # 2. SEND the correctly defined failure message.
        await interaction.followup.send(
            fail_message, silent=SILENT_MESSAGES, ephemeral=True
        )
        return
    # --- END OF FIX ---

    # Success! If we get here, lyrics_lines is valid. Start the karaoke.
    self.music_player.synced_lyrics = lyrics_lines

    clean_title, _ = get_cleaned_song_info(
        self.music_player.current_info, self.guild_id
    )
    embed = Embed(
        title=get_messages("karaoke.embed.title", self.guild_id, title=clean_title),
        description=get_messages("karaoke.embed.description", self.guild_id),
        color=0xC7CEEA if self.is_kawaii else discord.Color.blue(),
    )

    # We use the original interaction's followup to send the main message
    lyrics_message = await self.original_interaction.followup.send(
        silent=SILENT_MESSAGES, embed=embed, wait=True
    )
    self.music_player.lyrics_message = lyrics_message
    self.music_player.lyrics_task = asyncio.create_task(
        update_karaoke_task(self.guild_id)
    )

    # Notify the user who clicked the button that it worked
    success_message = get_messages("karaoke.retry.success", self.guild_id)
    await interaction.followup.send(
        success_message, silent=SILENT_MESSAGES, ephemeral=True
    )


class RefineLyricsModal(discord.ui.Modal):
    def __init__(self, message_to_edit: discord.Message):
        # Use the guild_id from the message directly to set the title
        super().__init__(
            title=get_messages("lyrics.refine_modal.title", message_to_edit.guild.id)
        )
        self.message_to_edit = message_to_edit
        self.guild_id = message_to_edit.guild.id
        self.is_kawaii = get_mode(self.guild_id)

        self.corrected_query = discord.ui.TextInput(
            label=get_messages("lyrics.refine_modal.label", self.guild_id),
            placeholder=get_messages("lyrics.refine_modal.placeholder", self.guild_id),
            style=discord.TextStyle.short,
        )
        self.add_item(self.corrected_query)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True, ephemeral=True)

        new_query = self.corrected_query.value
        logger.info(f"Refining lyrics search with new query: '{new_query}'")

        if not genius:
            await interaction.followup.send(
                get_messages("api.genius.not_configured", interaction.guild_id),
                silent=SILENT_MESSAGES,
                ephemeral=True,
            )
            return

        try:
            loop = asyncio.get_running_loop()
            song = await loop.run_in_executor(
                None, lambda: genius.search_song(new_query)
            )

            if not song:
                await interaction.followup.send(
                    get_messages(
                        "lyrics.error.refine_failed",
                        interaction.guild_id,
                        query=new_query,
                    ),
                    silent=SILENT_MESSAGES,
                    ephemeral=True,
                )
                return

            raw_lyrics = song.lyrics
            lines = raw_lyrics.split("\n")
            cleaned_lines = [
                line
                for line in lines
                if "contributor" not in line.lower()
                and "lyrics" not in line.lower()
                and "embed" not in line.lower()
            ]
            lyrics = "\n".join(cleaned_lines).strip()

            pages = []
            current_page_content = ""
            for line in lyrics.split("\n"):
                if len(current_page_content) + len(line) + 1 > 1500:
                    pages.append(f"```{current_page_content.strip()}```")
                    current_page_content = ""
                current_page_content += line + "\n"
            if current_page_content.strip():
                pages.append(f"```{current_page_content.strip()}```")

            new_embed = Embed(
                title=get_messages(
                    "lyrics.embed.title", self.guild_id, title=song.title
                ),
                url=song.url,
                color=0xB5EAD7 if self.is_kawaii else discord.Color.green(),
            )

            new_view = LyricsView(
                pages=pages, original_embed=new_embed, guild_id=self.guild_id
            )

            final_embed = new_view.update_embed()
            new_view.children[0].disabled = True
            if len(pages) <= 1:
                new_view.children[1].disabled = True

            await self.message_to_edit.edit(embed=final_embed, view=new_view)

            await interaction.followup.send(
                get_messages("lyrics.success.updated", self.guild_id),
                silent=SILENT_MESSAGES,
                ephemeral=True,
            )

        except Exception as e:
            logger.error(f"Error during lyrics refinement: {e}", exc_info=True)
            await interaction.followup.send(
                get_messages("api.lyrics.generic_fetch_error", self.guild_id),
                silent=SILENT_MESSAGES,
                ephemeral=True,
            )


class KaraokeRetryView(discord.ui.View):
    def __init__(
        self,
        original_interaction: discord.Interaction,
        suggested_query: str,
        guild_id: int,
    ):
        super().__init__(timeout=180.0)
        self.original_interaction = original_interaction
        self.suggested_query = suggested_query
        self.guild_id = guild_id

        # Set button labels from messages
        self.retry_button.label = get_messages("karaoke.retry_button", self.guild_id)
        self.genius_fallback_button.label = get_messages(
            "karaoke.genius_fallback_button", self.guild_id
        )

    @discord.ui.button(style=discord.ButtonStyle.primary)
    async def retry_button(self, interaction: discord.Interaction, button: Button):
        modal = KaraokeRetryModal(
            original_interaction=self.original_interaction,
            suggested_query=self.suggested_query,
        )
        await interaction.response.send_modal(modal)

    @discord.ui.button(style=discord.ButtonStyle.secondary)
    async def genius_fallback_button(
        self, interaction: discord.Interaction, button: Button
    ):
        # Disable buttons to show action is taken
        for child in self.children:
            child.disabled = True
        await self.original_interaction.edit_original_response(view=self)

        # Acknowledge the button click before starting the search
        await interaction.response.defer()

        # Fetch standard lyrics
        fallback_msg = get_messages("lyrics.fallback_warning", self.guild_id)
        await fetch_and_display_genius_lyrics(
            self.original_interaction, fallback_message=fallback_msg
        )


class KaraokeWarningView(View):
    def __init__(self, interaction: discord.Interaction, karaoke_coro):
        super().__init__(timeout=180.0)
        self.interaction = interaction
        self.karaoke_coro = karaoke_coro

        guild_id = interaction.guild.id
        self.continue_button.label = get_messages("karaoke.warning.button", guild_id)

    @discord.ui.button(style=discord.ButtonStyle.success)
    async def continue_button(self, interaction: discord.Interaction, button: Button):
        # We check that it's the original user who is clicking
        if interaction.user.id != self.interaction.user.id:
            await interaction.response.send_message(
                get_messages("command.error.user_only", interaction.guild_id),
                silent=SILENT_MESSAGES,
                ephemeral=True,
            )
            return

        guild_id = interaction.guild_id
        get_guild_state(guild_id).karaoke_disclaimer_shown = True
        logger.info(f"Karaoke disclaimer acknowledged for guild {guild_id}.")

        button.disabled = True
        button.label = get_messages(
            "karaoke.warning.acknowledged_button", interaction.guild_id
        )
        await interaction.response.edit_message(view=self)

        await self.karaoke_coro()


# View for the filter buttons
class FilterView(View):
    def __init__(self, interaction: discord.Interaction):
        super().__init__(timeout=None)
        self.guild_id = interaction.guild.id
        self.interaction = interaction
        self.state = get_guild_state(self.guild_id)
        for effect in AUDIO_FILTERS.keys():
            display_name = get_messages(f"filter.name.{effect}", self.guild_id)
            is_active = effect in self.state.server_filters
            style = ButtonStyle.success if is_active else ButtonStyle.secondary
            button = Button(
                label=display_name, custom_id=f"filter_{effect}", style=style
            )
            button.callback = self.button_callback
            self.add_item(button)

    async def button_callback(self, interaction: discord.Interaction):
        effect = interaction.data["custom_id"].split("_")[1]
        active_guild_filters = self.state.server_filters

        # Enable or disable the filter
        if effect in active_guild_filters:
            active_guild_filters.remove(effect)
        else:
            active_guild_filters.add(effect)

        # Update the appearance of the buttons
        for child in self.children:
            if isinstance(child, Button):
                child_effect = child.custom_id.split("_")[1]
                child.style = (
                    ButtonStyle.success
                    if child_effect in active_guild_filters
                    else ButtonStyle.secondary
                )

        await interaction.response.edit_message(view=self)

        music_player = get_player(self.guild_id)
        if music_player.voice_client and (
            music_player.voice_client.is_playing()
            or music_player.voice_client.is_paused()
        ):

            # 1. We save the CURRENT playback speed (before the change)
            old_speed = music_player.playback_speed

            # 2. We calculate the real time elapsed since playback started
            elapsed_time = 0
            if music_player.playback_started_at:
                real_elapsed_time = time.time() - music_player.playback_started_at
                # 3. We calculate the position IN the music using the OLD speed
                elapsed_time = (real_elapsed_time * old_speed) + music_player.start_time

            # 4. We update the player's speed with the NEW speed for the next playback
            music_player.playback_speed = get_speed_multiplier_from_filters(
                active_guild_filters
            )

            # We indicate that we are changing the filter to restart playback at the correct position
            music_player.is_seeking = True
            music_player.seek_info = elapsed_time
            await safe_stop(music_player.voice_client)


class QueueView(View):
    """
    A View that handles pagination for the /queue command.
    It's designed to be fast and intelligently fetches missing titles on-the-fly.
    """

    def __init__(
        self, interaction: discord.Interaction, tracks: list, items_per_page: int = 5
    ):
        super().__init__(timeout=300.0)
        self.interaction = interaction
        self.guild_id = interaction.guild_id
        self.music_player = get_player(self.guild_id)
        self.is_kawaii = get_mode(self.guild_id)

        self.tracks = tracks
        self.items_per_page = items_per_page
        self.current_page = 0
        self.total_pages = (
            math.ceil(len(self.tracks) / self.items_per_page) if self.tracks else 1
        )

        self.message = None

        self.previous_button = Button(style=ButtonStyle.secondary)
        self.next_button = Button(style=ButtonStyle.secondary)

        self.previous_button.label = get_messages(
            "queue_button.previous", self.guild_id
        )
        self.next_button.label = get_messages("queue_button.next", self.guild_id)

        self.previous_button.callback = self.previous_button_callback
        self.next_button.callback = self.next_button_callback

        self.add_item(self.previous_button)
        self.add_item(self.next_button)

    async def on_timeout(self):
        """Called when the view times out to delete the message."""
        try:
            if self.message:
                await self.message.delete()
        except discord.errors.NotFound:
            pass

    async def create_queue_embed(self) -> Embed:
        status_lines = []
        if self.music_player.loop_current:
            status_lines.append(get_messages("queue_status_loop", self.guild_id))
        if get_guild_state(self.guild_id)._24_7_mode:
            mode_24_7 = "Auto" if self.music_player.autoplay_enabled else "Normal"
            status_lines.append(
                get_messages("queue_status_24_7", self.guild_id).format(mode=mode_24_7)
            )
        elif self.music_player.autoplay_enabled:
            status_lines.append(get_messages("queue_status_autoplay", self.guild_id))
        current_volume_percent = int(self.music_player.volume * 100)
        if current_volume_percent != 100:
            status_lines.append(
                get_messages("queue_status_volume", self.guild_id).format(
                    level=current_volume_percent
                )
            )
        status_description = (
            "\n".join(status_lines)
            if status_lines
            else get_messages("queue_status_none", self.guild_id)
        )

        description_text = ""
        if len(self.tracks) == 0 and self.music_player.current_info:
            description_text = get_messages("queue_last_song", self.guild_id)
        else:
            description_text = get_messages(
                "queue_description", self.guild_id, count=len(self.tracks)
            )

        embed = Embed(
            title=get_messages("queue_title", self.guild_id),
            description=description_text,
            color=0xB5EAD7 if self.is_kawaii else discord.Color.blue(),
        )

        embed.add_field(
            name=get_messages("queue_status_title", self.guild_id),
            value=status_description,
            inline=False,
        )

        if self.music_player.current_info:
            title = self.music_player.current_info.get("title", "Unknown Title")
            now_playing_text = ""
            if self.music_player.current_info.get("source_type") == "file":
                now_playing_text = get_messages(
                    "queue.now_playing_format.file", self.guild_id, title=title
                )
            else:
                url = self.music_player.current_info.get(
                    "webpage_url", self.music_player.current_url
                )
                now_playing_text = f"[{title}]({url})"
            embed.add_field(
                name=get_messages("now_playing_in_queue", self.guild_id),
                value=now_playing_text,
                inline=False,
            )

        if self.tracks:
            start_index = self.current_page * self.items_per_page
            end_index = start_index + self.items_per_page
            tracks_on_page = self.tracks[start_index:end_index]

            # This hydration part remains the same, it is correct.
            tracks_to_hydrate = [
                track
                for track in tracks_on_page
                if isinstance(track, dict)
                and (
                    not track.get("title")
                    or track.get("title") == "Unknown Title"
                    or track.get("title")
                    == get_messages("player.loading_placeholder", self.guild_id)
                )
                and not track.get("source_type") == "file"
            ]

            if tracks_to_hydrate:
                tasks = [fetch_meta(track["url"], None) for track in tracks_to_hydrate]
                hydrated_results = await asyncio.gather(*tasks)
                hydrated_map = {res["url"]: res for res in hydrated_results if res}
                for track in tracks_on_page:
                    if isinstance(track, dict) and track.get("url") in hydrated_map:
                        new_data = hydrated_map[track["url"]]
                        track["title"] = new_data.get("title", "Unknown Title")
                        track["webpage_url"] = new_data.get("webpage_url", track["url"])

            next_songs_list = []
            current_length = 0
            limit = 1000

            for i, item in enumerate(tracks_on_page, start=start_index):
                display_info = get_track_display_info(item)
                title = display_info.get("title")
                display_line = ""

                # --- MODIFICATION START ---
                # We correct the display logic for LazySearchItem
                if display_info.get("source_type") == "lazy":
                    # Just display the title, without any extra text
                    display_line = f"`{title}`"
                elif display_info.get("source_type") == "file":
                    display_line = get_messages(
                        "controller.queue.line_display.file", self.guild_id, title=title
                    )
                else:
                    url = display_info.get("webpage_url", "#")
                    display_line = f"[{title}]({url})"
                # --- MODIFICATION END ---

                full_line = get_messages(
                    "queue.track_line.full_format",
                    self.guild_id,
                    i=i + 1,
                    display_line=display_line,
                )

                if current_length + len(full_line) > limit:
                    remaining = len(self.tracks) - (i)
                    next_songs_list.append(
                        get_messages(
                            "queue.and_more", self.guild_id, remaining=remaining
                        )
                    )
                    break

                next_songs_list.append(full_line)
                current_length += len(full_line)

            if next_songs_list:
                embed.add_field(
                    name=get_messages("queue_next", self.guild_id),
                    value="\n".join(next_songs_list),
                    inline=False,
                )

        embed.set_footer(
            text=get_messages(
                "queue_page_footer",
                self.guild_id,
                current_page=self.current_page + 1,
                total_pages=self.total_pages,
            )
        )
        return embed

    def update_button_states(self):
        self.previous_button.disabled = self.current_page == 0
        self.next_button.disabled = self.current_page >= self.total_pages - 1

    async def previous_button_callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        if self.current_page > 0:
            self.current_page -= 1
        self.update_button_states()
        new_embed = await self.create_queue_embed()

        try:
            await interaction.edit_original_response(embed=new_embed, view=self)
        except discord.errors.DiscordServerError as e:
            logger.warning(
                f"Failed to edit queue message (previous button) due to Discord API error: {e}"
            )

    async def next_button_callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        if self.current_page < self.total_pages - 1:
            self.current_page += 1
        self.update_button_states()
        new_embed = await self.create_queue_embed()

        try:
            await interaction.edit_original_response(embed=new_embed, view=self)
        except discord.errors.DiscordServerError as e:
            logger.warning(
                f"Failed to edit queue message (next button) due to Discord API error: {e}"
            )


class RemoveSelect(discord.ui.Select):
    """The dropdown menu component, now with multi-select enabled."""

    def __init__(self, tracks_on_page: list, page_offset: int, guild_id: int):
        options = []
        for i, track in enumerate(tracks_on_page):
            global_index = i + page_offset
            display_info = get_track_display_info(track)
            title = display_info.get("title", "Unknown Title")

            options.append(
                discord.SelectOption(
                    label=f"{global_index + 1}. {title}"[:100], value=str(global_index)
                )
            )

        super().__init__(
            placeholder=get_messages("remove_placeholder", guild_id),
            min_values=1,
            max_values=len(options) if options else 1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        """This is the corrected callback that properly handles the interaction response."""
        guild_id = interaction.guild_id
        state = get_guild_state(guild_id)
        is_kawaii = state.locale == Locale.EN_X_KAWAII
        state = get_guild_state(guild_id)
        music_player = state.music_player

        indices_to_remove = sorted([int(v) for v in self.values], reverse=True)

        queue_list = list(music_player.queue._queue)
        removed_titles = []

        for index in indices_to_remove:
            if 0 <= index < len(queue_list):
                removed_track = queue_list.pop(index)
                removed_display_info = get_track_display_info(removed_track)
                removed_titles.append(
                    removed_display_info.get(
                        "title", get_messages("player.a_song_fallback", guild_id)
                    )
                )

        new_queue = asyncio.Queue()
        for item in queue_list:
            await new_queue.put(item)
        music_player.queue = new_queue

        bot.loop.create_task(update_controller(bot, guild_id))

        self.view.clear_items()
        await interaction.response.edit_message(
            content=get_messages("remove_processed", guild_id),
            embed=None,
            view=self.view,
        )

        embed = Embed(
            title=get_messages(
                "remove_success_title", guild_id, count=len(removed_titles)
            ),
            description="\n".join([f"• `{title}`" for title in removed_titles]),
            color=0xB5EAD7 if is_kawaii else discord.Color.green(),
        )
        await interaction.channel.send(embed=embed, silent=SILENT_MESSAGES)


class RemoveView(View):
    """The interactive view holding the dropdown and pagination buttons."""

    def __init__(self, interaction: discord.Interaction, all_tracks: list):
        super().__init__(timeout=300.0)
        self.interaction = interaction
        self.guild_id = interaction.guild_id
        self.all_tracks = all_tracks
        self.current_page = 0
        self.items_per_page = 25
        self.total_pages = (
            math.ceil(len(self.all_tracks) / self.items_per_page)
            if self.all_tracks
            else 1
        )

    async def update_view(self):
        """Rebuilds the view with the correct dropdown and buttons for the current page."""
        self.clear_items()
        start_index = self.current_page * self.items_per_page
        end_index = start_index + self.items_per_page
        tracks_on_page = self.all_tracks[start_index:end_index]

        tracks_to_hydrate = [
            t
            for t in tracks_on_page
            if isinstance(t, dict)
            and (not t.get("title") or t.get("title") == "Unknown Title")
            and not t.get("source_type") == "file"
        ]

        if tracks_to_hydrate:
            tasks = [fetch_meta(track["url"], None) for track in tracks_to_hydrate]
            hydrated_results = await asyncio.gather(*tasks)
            hydrated_map = {res["url"]: res for res in hydrated_results if res}
            for track in tracks_on_page:
                if isinstance(track, dict) and track.get("url") in hydrated_map:
                    track["title"] = hydrated_map[track["url"]].get(
                        "title", "Unknown Title"
                    )

        # We make sure to add the correct select menu.
        self.add_item(
            RemoveSelect(
                tracks_on_page, page_offset=start_index, guild_id=self.guild_id
            )
        )

        if self.total_pages > 1:
            prev_button = Button(
                label=get_messages("remove_button.previous", self.guild_id),
                style=ButtonStyle.secondary,
                disabled=(self.current_page == 0),
            )
            next_button = Button(
                label=get_messages("remove_button.next", self.guild_id),
                style=ButtonStyle.secondary,
                disabled=(self.current_page >= self.total_pages - 1),
            )
            prev_button.callback = self.prev_page
            next_button.callback = self.next_page
            self.add_item(prev_button)
            self.add_item(next_button)

    async def prev_page(self, interaction: discord.Interaction):
        await interaction.response.defer()
        if self.current_page > 0:
            self.current_page -= 1
        await self.update_view()
        await interaction.edit_original_response(view=self)

    async def next_page(self, interaction: discord.Interaction):
        await interaction.response.defer()
        if self.current_page < self.total_pages - 1:
            self.current_page += 1
        await self.update_view()
        await interaction.edit_original_response(view=self)


# ==============================================================================
# 3. UTILITY & HELPER FUNCTIONS
# ==============================================================================


async def show_youtube_blocked_message(interaction: discord.Interaction):
    """Creates and sends the standardized 'YouTube is blocked' embed."""
    guild_id = interaction.guild.id
    embed = Embed(
        title=get_messages("error.youtube_blocked.title", guild_id),
        description=get_messages("error.youtube_blocked.description", guild_id),
        color=(
            0xFF9AA2
            if (get_guild_state(guild_id).locale == Locale.EN_X_KAWAII)
            else discord.Color.orange()
        ),
    )
    embed.add_field(
        name=get_messages("error.youtube_blocked.repo_field", guild_id),
        value=get_messages("error.youtube_blocked.repo_value", guild_id),
    )
    # Use followup.send because the interaction will always be deferred by the command
    await interaction.followup.send(embed=embed, ephemeral=True, silent=True)


def get_track_display_info(track, guild_id: int = None) -> dict:
    """
    Normalizes access to a track's information, whether it's a LazySearchItem object
    or a dictionary. Always returns a clean and safe dictionary.
    --- UPDATED VERSION ---
    This version accepts an optional guild_id for full internationalization.
    """
    if isinstance(track, LazySearchItem):
        if track.resolved_info and not track.resolved_info.get("error"):
            return {
                "title": track.resolved_info.get(
                    "title", get_messages("player.unknown_title", guild_id)
                ),
                "duration": track.resolved_info.get("duration", 0),
                "webpage_url": track.resolved_info.get("webpage_url", "#"),
                "source_type": "lazy-resolved",
            }
        else:
            title_to_display = track.title
            if title_to_display == "Pending resolution...":
                title_to_display = get_messages("player.loading_placeholder", guild_id)

            return {
                "title": title_to_display,
                "duration": 0,
                "webpage_url": "#",
                "source_type": "lazy",
            }

    elif isinstance(track, dict):
        return {
            "title": track.get("title", get_messages("player.unknown_title", guild_id)),
            "duration": track.get("duration", 0),
            "webpage_url": track.get("webpage_url", track.get("url", "#")),
            "source_type": track.get("source_type"),
        }

    return {
        "title": get_messages("player.invalid_track", guild_id),
        "duration": 0,
        "webpage_url": "#",
        "source_type": "invalid",
    }


# --- General & State Helpers ---


async def fetch_video_info_with_retry(query: str, ydl_opts_override=None):
    """
    Fetches video info using yt-dlp, with a robust retry mechanism for age-restricted content.
    This is the new universal function for all online fetching.
    """
    base_ydl_opts = {
        "format": "bestaudio[acodec=opus]/bestaudio/best",
        "quiet": True,
        "no_warnings": True,
        "no_color": True,
        "socket_timeout": 15,
        # On supprime 'extractor_args' ici car c'est ce qui déclenche l'anti-bot sur le VPS
    }
    ydl_opts = {**base_ydl_opts, **(ydl_opts_override or {})}

    try:
        # First attempt: no cookies
        logger.info(f"Fetching info for '{query[:100]}' (no cookies).")
        return await run_ydl_with_low_priority(ydl_opts, query)
    except yt_dlp.utils.DownloadError as e:
        error_str = str(e).lower()
        # Check for age restriction errors OR bot detection
        # AJOUT DE "bot" POUR FORCER L'UTILISATION DES COOKIES
        if (
            "sign in to confirm your age" in error_str
            or "age-restricted" in error_str
            or "please sign in" in error_str
            or "bot" in error_str
        ):
            logger.warning(
                f"Restriction/Bot detection for '{query[:100]}'. Retrying with cookies."
            )

            cookies_to_try = AVAILABLE_COOKIES.copy()
            random.shuffle(cookies_to_try)  # Shuffle to distribute load/bans

            for cookie_name in cookies_to_try:
                try:
                    logger.info(f"Retrying with cookie: {cookie_name}")
                    return await run_ydl_with_low_priority(
                        ydl_opts, query, specific_cookie_file=cookie_name
                    )
                except Exception as cookie_e:
                    logger.warning(
                        f"Cookie '{cookie_name}' failed: {str(cookie_e)[:150]}"
                    )
                    continue  # Try the next cookie

            # If all cookies failed, re-raise the original error
            logger.error(f"All cookies failed for restricted content: '{query[:100]}'")
            raise e
        else:
            # Not an age restriction error, re-raise it
            raise e


def get_file_duration(file_path: str) -> float:
    """Uses ffprobe to get the duration of a local file in seconds."""
    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        file_path,
    ]
    try:
        result = subprocess.run(
            command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        if result.returncode == 0:
            return float(result.stdout.strip())
        else:
            logger.error(f"ffprobe error for {file_path}: {result.stderr}")
            return 0.0
    except (FileNotFoundError, ValueError) as e:
        logger.error(f"Unable to get duration for {file_path}: {e}")
        return 0.0


def format_duration(seconds: int) -> str:
    """Formats a duration in seconds into HH:MM:SS or MM:SS."""
    if seconds is None:
        return "00:00"
    minutes, seconds = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours > 0:
        return f"{hours:d}:{minutes:02d}:{seconds:02d}"
    else:
        return f"{minutes:02d}:{seconds:02d}"


def create_progress_bar(
    current: int, total: int, guild_id: int, bar_length: int = 10
) -> str:
    """Creates a textual progress bar."""
    if total == 0:
        live_text = get_messages("player.live_indicator", guild_id)
        return f"`[▬▬▬▬▬▬▬▬▬▬▬▬]` {live_text}"
    percentage = current / total
    filled_length = int(bar_length * percentage)
    bar = "█" * filled_length + "─" * (bar_length - filled_length)
    return f"`[{bar}]`"


# Make sure the parse_time function is also present
def parse_time(time_str: str) -> int | None:
    """Converts a time string (HH:MM:SS, MM:SS, SS) into seconds."""
    parts = time_str.split(":")
    if not all(part.isdigit() for part in parts):
        return None

    parts = [int(p) for p in parts]
    seconds = 0

    if len(parts) == 3:  # HH:MM:SS
        seconds = parts[0] * 3600 + parts[1] * 60 + parts[2]
    elif len(parts) == 2:  # MM:SS
        seconds = parts[0] * 60 + parts[1]
    elif len(parts) == 1:  # SS
        seconds = parts[0]
    else:
        return None

    return seconds


def ydl_worker(ydl_opts, query, cookies_file=None):
    """
    This function runs in a separate process.
    It changes its own priority and performs the yt-dlp extraction.
    It now handles exceptions internally to avoid pickling errors.
    """
    # Change the priority of the current process
    p = psutil.Process()
    if platform.system() == "Windows":
        p.nice(psutil.IDLE_PRIORITY_CLASS)
    else:
        # A niceness value of 19 is the lowest priority
        os.nice(19)

    if cookies_file and os.path.exists(cookies_file):
        ydl_opts["cookiefile"] = cookies_file

    try:
        # Execute the heavy task
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            result = ydl.extract_info(query, download=False)
        # On success, return a dictionary indicating success and the data
        return {"status": "success", "data": result}
    except Exception as e:
        # On failure, return a dictionary indicating error and the error message string
        # This prevents trying to pickle the entire exception object.
        return {"status": "error", "message": str(e)}


async def run_ydl_with_low_priority(
    ydl_opts, query, loop=None, specific_cookie_file=None
):
    """
    Sends the yt-dlp task to the process pool.
    Uses a specific cookie file if provided.
    """
    if loop is None:
        loop = asyncio.get_running_loop()

    cookies_file_to_use = None

    # This is now the ONLY logic for cookies in this function.
    if specific_cookie_file:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        cookies_file_to_use = os.path.join(script_dir, specific_cookie_file)
        if not os.path.exists(cookies_file_to_use):
            logger.error(
                f"Specified cookie file {cookies_file_to_use} not found! Aborting cookie use for this request."
            )
            cookies_file_to_use = None

    result_dict = await loop.run_in_executor(
        process_pool, ydl_worker, ydl_opts, query, cookies_file_to_use
    )

    if result_dict.get("status") == "error":
        error_message = result_dict.get("message", "Unknown error in subprocess")
        raise yt_dlp.utils.DownloadError(error_message)

    return result_dict.get("data")


async def play_silence_loop(guild_id: int):
    """
    Plays a silent sound in a loop to maintain the voice connection.
    This version is corrected to stop cleanly, avoid FFmpeg process leaks,
    AND optimized for low CPU consumption.
    """
    state = get_guild_state(guild_id)
    music_player = state.music_player
    vc = music_player.voice_client

    if not vc or not vc.is_connected():
        return

    logger.info(
        f"[{guild_id}] Starting FFmpeg silence loop to keep connection alive (Low CPU mode)."
    )
    music_player.is_playing_silence = True

    source = "anullsrc=channel_layout=stereo:sample_rate=48000"

    # Correction and optimization of FFmpeg options
    ffmpeg_options = {
        # The -re option forces playback at normal speed, reducing CPU usage from 100% to ~1%
        "before_options": "-re -f lavfi",  # <-- CPU OPTIMIZATION
        "options": "-vn -c:a libopus -b:a 16k",
    }

    def noop_callback(error):
        if error:
            logger.error(
                f"[{guild_id}] Error in no-op callback for silence loop: {error}"
            )

    try:
        while vc.is_connected():
            if not vc.is_playing():
                vc.play(
                    discord.FFmpegPCMAudio(source, **ffmpeg_options),
                    after=noop_callback,
                )
            await asyncio.sleep(20)

    except asyncio.CancelledError:
        logger.info(f"[{guild_id}] Silence loop task cancelled, proceeding to cleanup.")
        pass
    except Exception as e:
        logger.error(f"[{guild_id}] Error in FFmpeg silence loop: {e}")
    finally:
        # The 'finally' block is synchronous. We schedule the execution of 'safe_stop'
        # on the bot's event loop to ensure proper asynchronous cleanup.
        if vc and vc.is_connected() and music_player.is_playing_silence:
            logger.info(f"[{guild_id}] Scheduling final cleanup for silence source.")
            bot.loop.create_task(safe_stop(vc))  # <-- LEAK FIX

        music_player.is_playing_silence = False


async def ensure_voice_connection(
    interaction: discord.Interaction,
) -> discord.VoiceClient | None:
    """
    Verifies and ensures the bot is connected to the user's voice channel.
    Handles connecting, reconnecting, and promoting in stage channels.
    This version includes a robust auto-recovery mechanism for "zombie" connections
    and saves the playback state if a forced disconnect is needed.
    Returns the voice client on success, None on failure.
    """
    guild_id = interaction.guild.id
    state = get_guild_state(guild_id)
    music_player = state.music_player
    state = get_guild_state(guild_id)
    is_kawaii = state.locale == Locale.EN_X_KAWAII

    member = interaction.guild.get_member(interaction.user.id)
    if not member or not member.voice or not member.voice.channel:
        embed = Embed(
            description=get_messages("error.no_voice_channel", guild_id),
            color=0xFF9AA2 if is_kawaii else discord.Color.red(),
        )
        if interaction.response.is_done():
            await interaction.followup.send(
                embed=embed, ephemeral=True, silent=SILENT_MESSAGES
            )
        else:
            await interaction.response.send_message(
                embed=embed, ephemeral=True, silent=SILENT_MESSAGES
            )
        return None

    voice_channel = member.voice.channel
    vc = interaction.guild.voice_client

    # --- ZOMBIE DETECTION & STATE SYNC ---
    # Step 1: Handle cases where the voice client object is dead or stale.
    if vc and not vc.is_connected():
        logger.warning(
            f"[{guild_id}] Stale/disconnected voice client detected. Forcing cleanup."
        )
        # The vc object is invalid, nullify it to force a fresh connection.
        music_player.voice_client = None
        vc = None

    # Step 2: Ensure the music player's internal state matches the guild's voice client.
    if vc and music_player.voice_client != vc:
        logger.info(
            f"[{guild_id}] Voice client state desynchronization detected. Resynchronizing."
        )
        music_player.voice_client = vc

    # --- CONNECTION & RECOVERY LOGIC ---
    if not vc:
        try:
            logger.info(
                f"[{guild_id}] No active voice client. Attempting to connect to '{voice_channel.name}'."
            )
            new_vc = await voice_channel.connect()
            music_player.voice_client = new_vc
            vc = new_vc
            logger.info(f"[{guild_id}] Successfully connected.")

            # If we are reconnecting after a forced cleanup, resume playback.
            if music_player.is_resuming_after_clean and music_player.resume_info:
                logger.info(
                    f"[{guild_id}] State recovery initiated. Resuming playback."
                )
                info_to_resume = music_player.resume_info["info"]
                time_to_resume = music_player.resume_info["time"]

                music_player.current_info = info_to_resume
                music_player.current_url = info_to_resume.get("url")

                bot.loop.create_task(
                    play_audio(guild_id, seek_time=time_to_resume, is_a_loop=True)
                )

                # Reset recovery flags
                music_player.is_resuming_after_clean = False
                music_player.resume_info = None

        # --- THIS IS THE CORE OF THE SELF-HEALING MECHANISM ---
        except discord.errors.ClientException as e:
            if "Already connected to a voice channel" in str(e):
                logger.error(
                    f"[{guild_id}] CRITICAL: ZOMBIE CONNECTION DETECTED. Forcing self-repair sequence."
                )

                # Save the current playback state before disconnecting.
                if music_player.voice_client and music_player.current_info:
                    current_timestamp = 0
                    if music_player.playback_started_at:
                        elapsed_time = time.time() - music_player.playback_started_at
                        current_timestamp = music_player.start_time + (
                            elapsed_time * music_player.playback_speed
                        )
                    else:
                        current_timestamp = music_player.start_time

                    music_player.resume_info = {
                        "info": music_player.current_info.copy(),
                        "time": current_timestamp,
                    }
                    music_player.is_resuming_after_clean = True
                    logger.info(
                        f"[{guild_id}] Playback state saved at {current_timestamp:.2f}s before cleanup."
                    )

                # Force disconnect the zombie client.
                try:
                    music_player.is_cleaning = True
                    await music_player.voice_client.disconnect(force=True)
                    await asyncio.sleep(
                        1
                    )  # Crucial delay to let Discord process the disconnect.
                except Exception as disconnect_error:
                    logger.error(
                        f"[{guild_id}] Error during forced disconnect: {disconnect_error}"
                    )
                finally:
                    music_player.is_cleaning = False

                # Recursively call the function. This time it will succeed.
                logger.info(f"[{guild_id}] Retrying connection after self-repair.")
                return await ensure_voice_connection(interaction)
            else:
                # Handle other client exceptions
                raise e

        except Exception as e:
            embed = Embed(
                description=get_messages("error.connection", guild_id),
                color=0xFF9AA2 if is_kawaii else discord.Color.red(),
            )
            if interaction.response.is_done():
                await interaction.followup.send(
                    embed=embed, ephemeral=True, silent=SILENT_MESSAGES
                )
            else:
                await interaction.response.send_message(
                    embed=embed, ephemeral=True, silent=SILENT_MESSAGES
                )
            logger.error(
                f"Connection error in ensure_voice_connection: {e}", exc_info=True
            )
            return None

    # --- STANDARD OPERATIONS ON A HEALTHY CLIENT ---
    elif vc.channel != voice_channel:
        logger.info(f"[{guild_id}] Moving to a new voice channel: {voice_channel.name}")
        await vc.move_to(voice_channel)
        await asyncio.sleep(0.5)

    if isinstance(vc.channel, discord.StageChannel):
        if interaction.guild.me.voice and interaction.guild.me.voice.suppress:
            logger.info(f"[{guild_id}] Bot is a spectator. Attempting to promote.")
            try:
                await interaction.guild.me.edit(suppress=False)
                await asyncio.sleep(0.5)
            except discord.Forbidden:
                logger.warning(
                    f"[{guild_id}] Promotion failed: 'Mute Members' permission missing."
                )
            except Exception as e:
                logger.error(f"[{guild_id}] Unexpected error while promoting: {e}")

    # Auto-setup the controller channel on first use if not already set.
    if not get_guild_state(guild_id).controller_channel_id:
        get_guild_state(guild_id).controller_channel_id = interaction.channel.id
        get_guild_state(guild_id).controller_message_id = (
            None  # Ensure a new message is created
        )
        logger.info(
            f"[{guild_id}] Controller channel has been auto-set to #{interaction.channel.name}"
        )

    # Final sanity check and return the healthy client.
    music_player.text_channel = interaction.channel
    music_player.voice_client = vc
    return vc


def clear_audio_cache(guild_id: int):
    """Deletes the audio cache directory for a specific guild."""
    guild_cache_path = os.path.join("audio_cache", str(guild_id))
    if os.path.exists(guild_cache_path):
        try:
            shutil.rmtree(guild_cache_path)
            logger.info(f"Audio cache for guild {guild_id} successfully cleared.")
        except Exception as e:
            logger.error(f"Error while deleting cache for guild {guild_id}: {e}")


def get_full_opts():
    """Returns standard options for fetching full metadata."""
    return {
        "format": "bestaudio/best",
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "socket_timeout": 10,
    }


async def fetch_meta(url, _):
    """Fetches metadata for a single URL, used for queue hydration."""
    try:
        # We now use the robust, cookie-aware function for all metadata fetching.
        data = await fetch_video_info_with_retry(url)

        # We make sure the duration is returned.
        return {
            "url": url,
            "title": data.get("title", "Unknown Title"),
            "webpage_url": data.get("webpage_url", url),
            "thumbnail": data.get("thumbnail"),
            "duration": data.get("duration", 0),
            "is_single": False,
        }
    except Exception as e:
        logger.warning(f"Failed to hydrate metadata for {url}: {e}")
        return None  # Return None on failure


def get_messages(key: str, guild_id: int, **kwargs) -> str:
    """
    Translates a key using the new i18n system.
    Variables are passed directly as arguments (e.g., count=5).
    """
    state = get_guild_state(guild_id)
    # The key is now passed directly, without any modification.
    return translator.t(key, locale=state.locale.value, **kwargs)


async def safe_stop(vc: discord.VoiceClient):
    """
    Stops the voice client and forcefully kills the underlying FFMPEG process
    to prevent zombie processes.
    """
    if vc and (vc.is_playing() or vc.is_paused()):
        # Force kill the FFMPEG process
        if isinstance(vc.source, discord.PCMAudio) and hasattr(vc.source, "process"):
            try:
                vc.source.process.kill()
                logger.info(
                    f"[{vc.guild.id}] Manually killed FFMPEG process via safe_stop."
                )
            except Exception as e:
                logger.error(f"[{vc.guild.id}] Error killing FFMPEG in safe_stop: {e}")

        # Also call discord.py's stop() to clean up its internal state
        vc.stop()
        # A tiny delay to ensure the OS has time to process the kill signal
        await asyncio.sleep(0.1)


def create_queue_item_from_info(info: dict, guild_id: int) -> dict:
    """
    Creates a standardized, clean queue item from a full yt-dlp info dict.
    This version correctly handles the difference between local files and online sources.
    """

    # If the source_type is 'file', we build a very specific and clean dictionary
    # to ensure no data from previous online songs can interfere.
    if info.get("source_type") == "file":
        return {
            "url": info.get("url"),  # This is the essential file path
            "title": info.get("title", get_messages("player.unknown_file", guild_id)),
            "webpage_url": None,  # A local file has no webpage URL
            "thumbnail": None,  # A local file has no thumbnail
            "is_single": False,  # When re-queuing, it's considered part of a list
            "source_type": "file",  # Critically preserve this type
            "requester": info.get("requester"),
        }

    return {
        "url": info.get(
            "webpage_url", info.get("url")
        ),  # Prioritize the user-friendly URL
        "title": info.get("title", get_messages("player.unknown_title", guild_id)),
        "webpage_url": info.get("webpage_url", info.get("url")),
        "thumbnail": info.get("thumbnail"),
        "is_single": False,  # When re-queuing, it's part of a loop, not a single add
        "source_type": info.get("source_type"),  # Preserve for other potential types
        "requester": info.get("requester"),
    }


# --- Text, Formatting & Lyrics Helpers ---


def get_cleaned_song_info(music_info: dict, guild_id: int) -> tuple[str, str]:
    """Aggressively cleans the title and artist to optimize the search."""

    title = music_info.get("title", get_messages("player.unknown_title", guild_id))
    artist = music_info.get("uploader", get_messages("player.unknown_artist", guild_id))

    # --- 1. Cleaning the artist name ---
    # ADDING "- Topic" TO THE LIST
    ARTIST_NOISE = [
        "xoxo",
        "official",
        "beats",
        "prod",
        "music",
        "records",
        "tv",
        "lyrics",
        "archive",
        "- Topic",
    ]
    clean_artist = artist
    for noise in ARTIST_NOISE:
        clean_artist = re.sub(r"(?i)" + re.escape(noise), "", clean_artist).strip()

    # --- 2. Cleaning the song title ---
    patterns_to_remove = [
        r"\[.*?\]",  # Removes content in brackets, e.g., [MV]
        r"\(.*?\)",  # Removes content in parentheses, e.g., (Official Video)
        r"\s*feat\..*",  # Removes "feat." and the rest
        r"\s*ft\..*",  # Removes "ft." and the rest
        # --- LINE ADDED BELOW ---
        r"\s*w/.*",  # Removes "w/" (with) and the rest
        # --- END OF ADDITION ---
        r"(?i)official video",  # Removes "official video" (case-insensitive)
        r"(?i)lyric video",  # Removes "lyric video" (case-insensitive)
        r"(?i)audio",  # Removes "audio" (case-insensitive)
        r"(?i)hd",  # Removes "hd" (case-insensitive)
        r"4K",  # Removes "4K"
        r"\+",  # Removes "+" symbols
    ]

    clean_title = title
    for pattern in patterns_to_remove:
        clean_title = re.sub(pattern, "", clean_title)

    # Tries to remove the artist name from the title to keep only the song name
    if clean_artist:
        clean_title = clean_title.replace(clean_artist, "")
    clean_title = clean_title.replace(artist, "").strip(" -")

    # If the title is empty after cleaning, start over from the original title without parentheses/brackets
    if not clean_title:
        clean_title = re.sub(r"\[.*?\]|\(.*?\)", "", title).strip()

    logger.info(f"Cleaned info: Title='{clean_title}', Artist='{clean_artist}'")
    return clean_title, clean_artist


def get_speed_multiplier_from_filters(active_filters: set) -> float:
    """Calculates the speed multiplier from the active filters."""
    speed = 1.0
    pitch_speed = 1.0  # Speed from asetrate (nightcore/slowed)
    tempo_speed = 1.0  # Speed from atempo

    for f in active_filters:
        if f in AUDIO_FILTERS:
            filter_value = AUDIO_FILTERS[f]
            if "atempo=" in filter_value:
                match = re.search(r"atempo=([\d\.]+)", filter_value)
                if match:
                    tempo_speed *= float(match.group(1))
            if "asetrate=" in filter_value:
                match = re.search(r"asetrate=[\d\.]+\*([\d\.]+)", filter_value)
                if match:
                    pitch_speed *= float(match.group(1))

    # The final speed is the product of the two
    speed = pitch_speed * tempo_speed
    return speed


async def fetch_and_display_genius_lyrics(
    interaction: discord.Interaction, fallback_message: str = None
):
    """Fetches, formats, and displays lyrics using ONLY the authenticated Genius API to avoid 403 errors."""
    guild_id = interaction.guild_id
    state = get_guild_state(guild_id)
    music_player = state.music_player
    is_kawaii = state.locale == Locale.EN_X_KAWAII
    loop = asyncio.get_running_loop()

    if not genius:
        return await interaction.followup.send(
            get_messages("api.genius.not_configured", interaction.guild_id),
            silent=SILENT_MESSAGES,
            ephemeral=True,
        )

    clean_title, artist_name = get_cleaned_song_info(
        music_player.current_info, guild_id
    )
    precise_query = f"{clean_title} {artist_name}"

    try:
        logger.info(f"Attempting authenticated Genius API search: '{precise_query}'")

        search_result = await loop.run_in_executor(
            None, lambda: genius.search_songs(precise_query, per_page=5)
        )

        song_info = None
        if search_result and search_result.get("hits"):
            song_info = search_result["hits"][0]["result"]

        if not song_info:
            error_title = get_messages("lyrics.error.not_found.title", guild_id)
            error_desc = get_messages(
                "lyrics.error.not_found.description", guild_id, query=precise_query
            )  # On passe la variable ici
            error_embed = Embed(
                title=error_title,
                description=error_desc,
                color=0xFF9AA2 if is_kawaii else discord.Color.red(),
            )
            view = LyricsRetryView(
                original_interaction=interaction,
                suggested_query=clean_title,
                guild_id=guild_id,
            )
            await interaction.followup.send(
                silent=SILENT_MESSAGES, embed=error_embed, view=view
            )
            return

        logger.info(
            f"Found song: {song_info['full_title']}. Fetching lyrics from URL: {song_info['url']}"
        )
        song_object = await loop.run_in_executor(
            None, lambda: genius.search_song(song_id=song_info["id"])
        )

        if not song_object or not song_object.lyrics:
            raise ValueError(f"Could not retrieve lyrics for song ID {song_info['id']}")

        lyrics = song_object.lyrics

        lines = lyrics.split("\n")
        cleaned_lines = [
            line
            for line in lines
            if "contributor" not in line.lower()
            and "lyrics" not in line.lower()
            and "embed" not in line.lower()
        ]
        lyrics = "\n".join(cleaned_lines).strip()

        pages = []
        current_page_content = ""
        for line in lyrics.split("\n"):
            if len(current_page_content) + len(line) + 1 > 1500:
                pages.append(f"```{current_page_content.strip()}```")
                current_page_content = ""
            current_page_content += line + "\n"
        if current_page_content.strip():
            pages.append(f"```{current_page_content.strip()}```")

        base_embed = base_embed = Embed(
            title=get_messages("lyrics.embed.title", guild_id, title=song_object.title),
            url=song_object.url,
            color=0xB5EAD7 if is_kawaii else discord.Color.green(),
        )
        if fallback_message:
            base_embed.set_author(name=fallback_message)

        view = LyricsView(pages=pages, original_embed=base_embed, guild_id=guild_id)
        initial_embed = view.update_embed()
        view.children[0].disabled = True
        if len(pages) <= 1:
            view.children[1].disabled = True

        message = await interaction.followup.send(
            silent=SILENT_MESSAGES, embed=initial_embed, view=view, wait=True
        )
        view.message = message

    except Exception as e:
        logger.error(
            f"Error in authenticated lyrics fetch for '{precise_query}': {e}",
            exc_info=True,
        )
        await interaction.followup.send(
            get_messages("api.lyrics.generic_fetch_error", interaction.guild_id),
            silent=SILENT_MESSAGES,
            ephemeral=True,
        )


def format_lyrics_display(lyrics_lines, current_line_index, guild_id: int):
    """
    Formats the lyrics for Discord display, correctly handling
    newlines and problematic Markdown characters.
    """

    def clean(text):
        # Replaces backticks and removes Windows newlines (\r)
        return text.replace("`", "'").replace("\r", "")

    display_parts = []

    # Defines the context (how many lines to show before/after)
    context_lines = 4

    # Handles the case where the karaoke has not started yet
    if current_line_index == -1:
        display_parts.append(
            get_messages("karaoke.display.waiting_for_first_line", guild_id) + "\n"
        )
        # We display the next 5 lines
        for line_obj in lyrics_lines[:5]:
            # We split each line in case it contains newlines
            for sub_line in clean(line_obj["text"]).split("\n"):
                if sub_line.strip():  # Ignore empty lines
                    display_parts.append(f"`{sub_line}`")
    else:
        # Calculates the range of lines to display
        start_index = max(0, current_line_index - context_lines)
        end_index = min(len(lyrics_lines), current_line_index + context_lines + 1)

        # Loop over the lines to display
        for i in range(start_index, end_index):
            line_obj = lyrics_lines[i]
            is_current_line_chunk = i == current_line_index

            # === THIS IS THE LOGIC THAT 100% FIXES THE BUG ===
            # We split the current lyric line into sub-lines
            sub_lines = clean(line_obj["text"]).split("\n")

            for index, sub_line in enumerate(sub_lines):
                if not sub_line.strip():
                    continue

                # The "»" arrow only appears on the first sub-line of the current block
                prefix = "**»** " if is_current_line_chunk and index == 0 else ""

                display_parts.append(f"{prefix}`{sub_line}`")

    # We assemble everything and make sure not to exceed the Discord limit
    full_text = "\n".join(display_parts)
    return full_text[:4000]


# Create loading bar
def create_loading_bar(progress, width=10):
    filled = int(progress * width)
    unfilled = width - filled
    return "```[" + "█" * filled + "░" * unfilled + "] " + f"{int(progress * 100)}%```"


# --- Platform URL Processors ---


# --- FINAL PROCESS_SPOTIFY_URL FUNCTION (Cascade Architecture) ---
async def process_spotify_url(url, interaction):
    """
    Processes a Spotify URL with a cascade architecture:
    1. Tries with the official API (spotipy) for speed and completeness.
    2. On failure (e.g., editorial playlist), falls back to the scraper (spotifyscraper).
    """
    guild_id = interaction.guild.id
    state = get_guild_state(guild_id)
    is_kawaii = state.locale == Locale.EN_X_KAWAII
    clean_url = url.split("?")[0]

    # --- METHOD 1: OFFICIAL API (SPOTIPY) ---
    if sp:
        try:
            logger.info(f"Attempt 1: Official API (Spotipy) for {clean_url}")
            tracks_to_return = []
            loop = asyncio.get_event_loop()

            if "playlist" in clean_url:
                results = await loop.run_in_executor(
                    None,
                    lambda: sp.playlist_items(
                        clean_url,
                        fields="items.track.name,items.track.artists.name,next",
                        limit=100,
                    ),
                )
                while results:
                    for item in results["items"]:
                        if item and item.get("track"):
                            track = item["track"]
                            tracks_to_return.append(
                                (track["name"], track["artists"][0]["name"])
                            )
                    if results["next"]:
                        results = await loop.run_in_executor(
                            None, lambda: sp.next(results)
                        )
                    else:
                        results = None

            elif "album" in clean_url:
                results = await loop.run_in_executor(
                    None, lambda: sp.album_tracks(clean_url, limit=50)
                )
                while results:
                    for track in results["items"]:
                        tracks_to_return.append(
                            (track["name"], track["artists"][0]["name"])
                        )
                    if results["next"]:
                        results = await loop.run_in_executor(
                            None, lambda: sp.next(results)
                        )
                    else:
                        results = None

            elif "track" in clean_url:
                track = await loop.run_in_executor(None, lambda: sp.track(clean_url))
                tracks_to_return.append((track["name"], track["artists"][0]["name"]))

            elif "artist" in clean_url:
                results = await loop.run_in_executor(
                    None, lambda: sp.artist_top_tracks(clean_url)
                )
                for track in results["tracks"]:
                    tracks_to_return.append(
                        (track["name"], track["artists"][0]["name"])
                    )

            if not tracks_to_return:
                raise ValueError("No tracks found via API.")

            logger.info(
                f"Success with Spotipy: {len(tracks_to_return)} tracks retrieved."
            )
            return tracks_to_return

        except Exception as e:
            logger.warning(
                f"Spotipy API failed for {clean_url} (Reason: {e}). Switching to plan B: SpotifyScraper."
            )

    # --- METHOD 2: FALLBACK (SPOTIFYSCRAPER) ---
    if spotify_scraper_client:
        try:
            logger.info(f"Attempt 2: Scraper (SpotifyScraper) for {clean_url}")
            tracks_to_return = []
            loop = asyncio.get_event_loop()

            if "playlist" in clean_url:
                data = await loop.run_in_executor(
                    None, lambda: spotify_scraper_client.get_playlist_info(clean_url)
                )
                for track in data.get("tracks", []):
                    tracks_to_return.append(
                        (
                            track.get("name", "Unknown Title"),
                            track.get("artists", [{}])[0].get("name", "Unknown Artist"),
                        )
                    )

            elif "album" in clean_url:
                data = await loop.run_in_executor(
                    None, lambda: spotify_scraper_client.get_album_info(clean_url)
                )
                for track in data.get("tracks", []):
                    tracks_to_return.append(
                        (
                            track.get("name", "Unknown Title"),
                            track.get("artists", [{}])[0].get("name", "Unknown Artist"),
                        )
                    )

            elif "track" in clean_url:
                data = await loop.run_in_executor(
                    None, lambda: spotify_scraper_client.get_track_info(clean_url)
                )
                tracks_to_return.append(
                    (
                        data.get("name", "Unknown Title"),
                        data.get("artists", [{}])[0].get("name", "Unknown Artist"),
                    )
                )

            if not tracks_to_return:
                raise SpotifyScraperError(
                    "The scraper could not find any tracks either."
                )

            logger.info(
                f"Success with SpotifyScraper: {len(tracks_to_return)} tracks retrieved (potentially limited)."
            )
            return tracks_to_return

        # --- THIS IS THE CORRECTED ERROR HANDLING BLOCK ---
        except (SpotifyScraperError, spotipy.exceptions.SpotifyException) as e:
            logger.error(
                f"Both methods (API and Scraper) failed. Final error: {e}",
                exc_info=True,
            )

            embed = Embed(
                title=get_messages("spotify_error_title", guild_id),
                description=get_messages(
                    "spotify_error_description_detailed", guild_id
                ),
                color=0xFF9AA2 if is_kawaii else discord.Color.red(),
            )
            await interaction.followup.send(
                silent=SILENT_MESSAGES, embed=embed, ephemeral=True
            )
            return None
        # --- END OF CORRECTION ---
        except Exception as e:  # General fallback for any other unexpected errors
            logger.error(
                f"An unexpected error occurred in the Spotify fallback: {e}",
                exc_info=True,
            )
            embed = Embed(
                description=get_messages("spotify_error", guild_id),
                color=0xFFB6C1 if is_kawaii else discord.Color.red(),
            )
            await interaction.followup.send(
                silent=SILENT_MESSAGES, embed=embed, ephemeral=True
            )
            return None

    logger.critical("No client (Spotipy or SpotifyScraper) is functional.")
    embed = Embed(
        description=get_messages("api.spotify.unreachable", guild_id),
        color=discord.Color.dark_red(),
    )
    await interaction.followup.send(silent=SILENT_MESSAGES, embed=embed, ephemeral=True)
    return None


# Process Deezer URLs
async def process_deezer_url(url, interaction):
    guild_id = interaction.guild_id
    try:
        deezer_share_regex = re.compile(r"^(https?://)?(link\.deezer\.com)/s/.+$")
        if deezer_share_regex.match(url):
            logger.info(f"Detected Deezer share link: {url}. Resolving redirect...")
            response = requests.head(url, allow_redirects=True, timeout=10)
            response.raise_for_status()
            resolved_url = response.url
            logger.info(f"Resolved to: {resolved_url}")
            url = resolved_url

        parsed_url = urlparse(url)
        path_parts = parsed_url.path.strip("/").split("/")
        if len(path_parts) > 1 and len(path_parts[0]) == 2:
            path_parts = path_parts[1:]
        if len(path_parts) < 2:
            raise ValueError("Invalid Deezer URL format")

        resource_type = path_parts[0]
        resource_id = path_parts[1].split("?")[0]

        base_api_url = "https://api.deezer.com"
        logger.info(
            f"Fetching Deezer {resource_type} with ID {resource_id} from URL {url}"
        )

        tracks = []
        if resource_type == "track":
            response = requests.get(f"{base_api_url}/track/{resource_id}", timeout=10)
            response.raise_for_status()
            data = response.json()
            if "error" in data:
                raise Exception(f"Deezer API error: {data['error']['message']}")
            logger.info(
                f"Processing Deezer track: {data.get('title', 'Unknown Title')}"
            )
            track_name = data.get("title", "Unknown Title")
            artist_name = data.get("artist", {}).get("name", "Unknown Artist")
            tracks.append((track_name, artist_name))

        elif resource_type == "playlist":
            next_url = f"{base_api_url}/playlist/{resource_id}/tracks"
            total_tracks = 0
            fetched_tracks = 0

            while next_url:
                response = requests.get(next_url, timeout=10)
                response.raise_for_status()
                data = response.json()

                if "error" in data:
                    raise Exception(f"Deezer API error: {data['error']['message']}")

                if not data.get("data"):
                    raise ValueError(
                        "No tracks found in the playlist or playlist is empty"
                    )

                for track in data["data"]:
                    track_name = track.get("title", "Unknown Title")
                    artist_name = track.get("artist", {}).get("name", "Unknown Artist")
                    tracks.append((track_name, artist_name))

                fetched_tracks += len(data["data"])
                total_tracks = data.get("total", fetched_tracks)
                logger.info(
                    f"Fetched {fetched_tracks}/{total_tracks} tracks from playlist {resource_id}"
                )

                next_url = data.get("next")
                if next_url:
                    logger.info(f"Fetching next page: {next_url}")

            logger.info(
                f"Processing Deezer playlist: {data.get('title', 'Unknown Playlist')} with {len(tracks)} tracks"
            )

        elif resource_type == "album":
            response = requests.get(
                f"{base_api_url}/album/{resource_id}/tracks", timeout=10
            )
            response.raise_for_status()
            data = response.json()
            if "error" in data:
                raise Exception(f"Deezer API error: {data['error']['message']}")
            if not data.get("data"):
                raise ValueError("No tracks found in the album or album is empty")
            logger.info(
                f"Processing Deezer album: {data.get('title', 'Unknown Album')}"
            )
            for track in data["data"]:
                track_name = track.get("title", "Unknown Title")
                artist_name = track.get("artist", {}).get("name", "Unknown Artist")
                tracks.append((track_name, artist_name))
            logger.info(f"Extracted {len(tracks)} tracks from album {resource_id}")

        elif resource_type == "artist":
            response = requests.get(
                f"{base_api_url}/artist/{resource_id}/top?limit=10", timeout=10
            )
            response.raise_for_status()
            data = response.json()
            if "error" in data:
                raise Exception(f"Deezer API error: {data['error']['message']}")
            if not data.get("data"):
                raise ValueError("No top tracks found for the artist")
            logger.info(
                f"Processing Deezer artist: {data.get('name', 'Unknown Artist')}"
            )
            for track in data["data"]:
                track_name = track.get("title", "Unknown Title")
                artist_name = track.get("artist", {}).get("name", "Unknown Artist")
                tracks.append((track_name, artist_name))
            logger.info(f"Extracted {len(tracks)} top tracks for artist {resource_id}")

        if not tracks:
            raise ValueError("No valid tracks found in the Deezer resource")

        logger.info(
            f"Successfully processed Deezer {resource_type} with {len(tracks)} tracks"
        )
        return tracks

    except requests.exceptions.RequestException as e:
        logger.error(f"Network error fetching Deezer URL {url}: {e}")
        embed = Embed(
            description=get_messages("api.deezer.network_error", guild_id),
            color=(
                0xFFB6C1
                if (get_guild_state(guild_id).locale == Locale.EN_X_KAWAII)
                else discord.Color.red()
            ),
        )
        await interaction.followup.send(
            silent=SILENT_MESSAGES, embed=embed, ephemeral=True
        )
        return None
    except ValueError as e:
        logger.error(f"Invalid Deezer data for URL {url}: {e}")
        embed = Embed(
            description=get_messages(
                "api.deezer.value_error", guild_id, error_message=str(e)
            ),
            color=(
                0xFFB6C1
                if (get_guild_state(guild_id).locale == Locale.EN_X_KAWAII)
                else discord.Color.red()
            ),
        )
        await interaction.followup.send(
            silent=SILENT_MESSAGES, embed=embed, ephemeral=True
        )
        return None
    except Exception as e:
        logger.error(f"Unexpected error processing Deezer URL {url}: {e}")
        embed = Embed(
            description=get_messages("deezer_error", guild_id),
            color=(
                0xFFB6C1
                if (get_guild_state(guild_id).locale == Locale.EN_X_KAWAII)
                else discord.Color.red()
            ),
        )
        await interaction.followup.send(
            silent=SILENT_MESSAGES, embed=embed, ephemeral=True
        )
        return None


# Process Apple Music URLs
async def process_apple_music_url(url, interaction):
    guild_id = interaction.guild.id
    logger.info(f"Starting processing for Apple Music URL: {url}")

    clean_url = url.split("?")[0]
    browser = None

    try:
        async with async_playwright() as p:
            browser = await p.firefox.launch(headless=True)
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/115.0"
            )
            page = await context.new_page()

            await page.route(
                "**/*.{png,jpg,jpeg,svg,woff,woff2}", lambda route: route.abort()
            )
            logger.info("Optimization: Disabled loading of images and fonts.")

            logger.info("Navigating to the page with a 90 second timeout...")
            await page.goto(clean_url, wait_until="domcontentloaded", timeout=90000)
            logger.info("Page loaded. Extracting data...")

            tracks = []
            resource_type = "unknown"
            path_parts = urlparse(clean_url).path.strip("/").split("/")

            if len(path_parts) > 1:
                if path_parts[1] in ["album", "playlist"]:
                    resource_type = path_parts[1]
                elif path_parts[1] == "song":
                    resource_type = "song"

            logger.info(f"Detected resource type: {resource_type}")

            if resource_type in ["album", "playlist"]:
                logger.info(
                    f"Processing as {resource_type}, using row scraping method."
                )
                await page.wait_for_selector("div.songs-list-row", timeout=20000)
                main_artist_name = "Unknown Artist"
                try:
                    main_artist_el = await page.query_selector(".headings__subtitles a")
                    if main_artist_el:
                        main_artist_name = await main_artist_el.inner_text()
                except Exception:
                    logger.warning(
                        "Could not determine the main artist for the collection."
                    )

                track_rows = await page.query_selector_all("div.songs-list-row")
                for row in track_rows:
                    try:
                        title_el = await row.query_selector(
                            "div.songs-list-row__song-name"
                        )
                        title = (
                            await title_el.inner_text() if title_el else "Unknown Title"
                        )

                        artist_elements = await row.query_selector_all(
                            "div.songs-list-row__by-line a"
                        )
                        if artist_elements:
                            artist_names = [
                                await el.inner_text() for el in artist_elements
                            ]
                            artist = " & ".join(artist_names)
                        else:
                            artist = main_artist_name

                        if title != "Unknown Title":
                            tracks.append((title.strip(), artist.strip()))
                    except Exception as e:
                        logger.warning(f"Failed to extract a track row: {e}")

            elif resource_type == "song":
                logger.info("Processing as single song, using JSON-LD method.")
                try:
                    json_ld_selector = 'script[id="schema:song"]'
                    await page.wait_for_selector(json_ld_selector, timeout=15000)

                    json_ld_content = await page.locator(json_ld_selector).inner_text()
                    data = json.loads(json_ld_content)

                    title = data["audio"]["name"]
                    artist = data["audio"]["byArtist"][0]["name"]

                    if title and artist:
                        logger.info(
                            f"Successfully extracted from JSON-LD: '{title}' by '{artist}'"
                        )
                        tracks.append((title.strip(), artist.strip()))
                    else:
                        raise ValueError("JSON-LD data is missing name or artist.")
                except Exception as e:
                    logger.warning(
                        f"JSON-LD method failed ({e}). Falling back to HTML element scraping."
                    )
                    title_selector = 'h1[data-testid="song-title"]'
                    artist_selector = 'span[data-testid="song-subtitle-artists"] a'
                    await page.wait_for_selector(title_selector, timeout=10000)

                    title = await page.locator(title_selector).first.inner_text()
                    artist = await page.locator(artist_selector).first.inner_text()

                    if title and artist:
                        logger.info(
                            f"Successfully extracted from HTML fallback: '{title}' by '{artist}'"
                        )
                        tracks.append((title.strip(), artist.strip()))

            if not tracks:
                raise ValueError(
                    "No tracks could be extracted from the Apple Music resource."
                )

            logger.info(f"Success! {len(tracks)} track(s) extracted.")
            return tracks

    except Exception as e:
        logger.error(f"Error processing Apple Music URL {url}: {e}", exc_info=True)
        if "page" in locals() and page and not page.is_closed():
            await page.screenshot(path="apple_music_scrape_failed.png")
            logger.info("Screenshot of the error saved.")

        embed = Embed(
            description=get_messages("apple_music_error", guild_id),
            color=(
                0xFFB6C1
                if (get_guild_state(guild_id).locale == Locale.EN_X_KAWAII)
                else discord.Color.red()
            ),
        )
        try:
            if interaction and not interaction.is_expired():
                await interaction.followup.send(
                    silent=SILENT_MESSAGES, embed=embed, ephemeral=True
                )
        except Exception as send_error:
            logger.error(f"Unable to send error message: {send_error}")
        return None
    finally:
        if browser:
            await browser.close()
            logger.info("Playwright (Apple Music) browser closed successfully.")


# Process Tidal URLs
async def process_tidal_url(url, interaction):
    guild_id = interaction.guild_id

    async def load_and_extract_all_tracks(page):
        logger.info("Reliable loading begins (track by track)...")
        total_tracks_expected = 0
        try:
            meta_item_selector = 'span[data-test="grid-item-meta-item-count"]'
            meta_text = await page.locator(meta_item_selector).first.inner_text(
                timeout=3000
            )
            total_tracks_expected = int(re.search(r"\d+", meta_text).group())
            logger.info(f"Goal: Extract {total_tracks_expected} tracks.")
        except Exception:
            logger.warning("Unable to determine the total number of tracks.")
            total_tracks_expected = 0
        track_row_selector = "div[data-track-id]"
        all_tracks = []
        seen_track_ids = set()
        stagnation_counter = 0
        max_loops = 500
        for i in range(max_loops):
            if total_tracks_expected > 0 and len(all_tracks) >= total_tracks_expected:
                logger.info("All expected leads have been found. Early shutdown.")
                break
            track_elements = await page.query_selector_all(track_row_selector)
            if not track_elements and i > 0:
                break
            new_tracks_found_in_loop = False
            for element in track_elements:
                track_id = await element.get_attribute("data-track-id")
                if track_id and track_id not in seen_track_ids:
                    new_tracks_found_in_loop = True
                    seen_track_ids.add(track_id)
                    try:
                        title_el = await element.query_selector(
                            'span._titleText_51cccae, span[data-test="table-cell-title"]'
                        )
                        artist_el = await element.query_selector(
                            'a._item_39605ae, a[data-test="grid-item-detail-text-title-artist"]'
                        )
                        if title_el and artist_el:
                            title = (
                                (await title_el.inner_text()).split("<span>")[0].strip()
                            )
                            artist = await artist_el.inner_text()
                            if title and artist:
                                all_tracks.append((title, artist))
                    except Exception:
                        continue
            if not new_tracks_found_in_loop and i > 1:
                stagnation_counter += 1
                if stagnation_counter >= 5:
                    logger.info("Stable stagnation. End of process.")
                    break
            else:
                stagnation_counter = 0
            if track_elements:
                await track_elements[-1].scroll_into_view_if_needed(timeout=10000)
                await asyncio.sleep(0.75)
        logger.info(
            f"Process completed. Final total of unique tracks extracted: {len(all_tracks)}"
        )
        return list(dict.fromkeys(all_tracks))

    browser = None  # Initialize the browser to None
    try:
        clean_url = url.split("?")[0]
        parsed_url = urlparse(clean_url)
        path_parts = parsed_url.path.strip("/").split("/")

        resource_type = None
        if "playlist" in path_parts:
            resource_type = "playlist"
        elif "album" in path_parts:
            resource_type = "album"
        elif "mix" in path_parts:
            resource_type = "mix"
        elif "track" in path_parts:
            resource_type = "track"
        elif "video" in path_parts:
            resource_type = "video"

        if resource_type is None:
            raise ValueError("Tidal URL not supported.")

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
            )
            await page.goto(clean_url, wait_until="domcontentloaded")
            logger.info(f"Navigate to Tidal URL ({resource_type}): {clean_url}")

            await asyncio.sleep(3)
            unique_tracks = []

            if resource_type in ["playlist", "album", "mix"]:
                unique_tracks = await load_and_extract_all_tracks(page)

            elif resource_type == "track" or resource_type == "video":
                logger.info(f"Extracting a single media ({resource_type})...")
                try:
                    await page.wait_for_selector(
                        'div[data-test="artist-profile-header"], div[data-test="footer-player"]',
                        timeout=10000,
                    )
                    title_selector = 'span[data-test="now-playing-track-title"], h1[data-test="title"]'
                    artist_selector = (
                        'a[data-test="grid-item-detail-text-title-artist"]'
                    )
                    title = await page.locator(title_selector).first.inner_text(
                        timeout=5000
                    )
                    artist = await page.locator(artist_selector).first.inner_text(
                        timeout=5000
                    )

                    if not title or not artist:
                        raise ValueError("Missing title or artist.")

                    logger.info(
                        f"Unique media found: {title.strip()} - {artist.strip()}"
                    )
                    unique_tracks = [(title.strip(), artist.strip())]

                except Exception as e:
                    logger.warning(
                        f"Direct extraction method failed ({e}), attempting with page title..."
                    )
                    try:
                        page_title = await page.title()
                        title, artist = "", ""
                        if " - " in page_title:
                            parts = page_title.split(" - ")
                            artist, title = parts[0], parts[1].split(" on TIDAL")[0]
                        elif " by " in page_title:
                            parts = page_title.split(" by ")
                            title, artist = parts[0], parts[1].split(" on TIDAL")[0]

                        if not title or not artist:
                            raise ValueError("The page title format is unknown.")

                        logger.info(
                            f"Unique media found via page title: {title.strip()} - {artist.strip()}"
                        )
                        unique_tracks = [(title.strip(), artist.strip())]
                    except Exception as fallback_e:
                        await page.screenshot(
                            path=f"tidal_{resource_type}_extraction_failed.png"
                        )
                        raise ValueError(
                            f"All extraction methods failed. Final error: {fallback_e}"
                        )

            if not unique_tracks:
                raise ValueError(
                    "No tracks could be retrieved from the Tidal resource."
                )

            return unique_tracks

    except Exception as e:
        logger.error(f"Major error in process_tidal_url for {url}: {e}")
        if interaction:
            embed = Embed(
                description=get_messages("tidal_error", guild_id),
                color=(
                    0xFFB6C1
                    if (get_guild_state(guild_id).locale == Locale.EN_X_KAWAII)
                    else discord.Color.red()
                ),
            )
            await interaction.followup.send(
                silent=SILENT_MESSAGES, embed=embed, ephemeral=True
            )
        return None
    finally:
        if browser:
            await browser.close()
            logger.info("Playwright (Tidal) browser closed properly.")


async def process_amazon_music_url(url, interaction):
    guild_id = interaction.guild_id
    logger.info(f"Launching unified processing for Amazon Music URL: {url}")

    is_album = "/albums/" in url
    is_playlist = "/playlists/" in url or "/user-playlists/" in url
    is_track = "/tracks/" in url

    browser = None  # Initialize the browser to None
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36"
            )
            page = await context.new_page()

            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            logger.info("Page loaded. Cookie management.")

            try:
                await page.click(
                    'music-button:has-text("Accepter les cookies")', timeout=7000
                )
                logger.info("Cookie banner accepted.")
            except Exception:
                logger.info("No cookie banner found.")

            tracks = []

            if is_album or is_track:
                page_type = "Album" if is_album else "Track"
                logger.info(
                    f"Page of type '{page_type}' detected. Using JSON extraction method."
                )

                selector = 'script[type="application/ld+json"]'
                await page.wait_for_selector(selector, state="attached", timeout=20000)

                json_ld_scripts = await page.locator(selector).all_inner_texts()

                found_data = False
                for script_content in json_ld_scripts:
                    data = json.loads(script_content)
                    if data.get("@type") == "MusicAlbum" or (
                        is_album and "itemListElement" in data
                    ):
                        album_artist = data.get("byArtist", {}).get(
                            "name", "Unknown Artist"
                        )
                        for item in data.get("itemListElement", []):
                            track_name = item.get("name")
                            track_artist = item.get("byArtist", {}).get(
                                "name", album_artist
                            )
                            if track_name and track_artist:
                                tracks.append((track_name, track_artist))
                        found_data = True
                        break
                    elif data.get("@type") == "MusicRecording":
                        track_name = data.get("name")
                        track_artist = data.get("byArtist", {}).get(
                            "name", "Unknown Artist"
                        )
                        if track_name and track_artist:
                            tracks.append((track_name, track_artist))
                        found_data = True
                        break

                if not found_data:
                    raise ValueError(
                        f"No data of type 'MusicAlbum' or 'MusicRecording' found in JSON-LD tags."
                    )

            elif is_playlist:
                logger.info(
                    "'Playlist' type page detected. Using fast pre-virtualization extraction."
                )
                try:
                    await page.wait_for_selector(
                        "music-image-row[primary-text]", timeout=20000
                    )
                    logger.info(
                        "Tracklist detected. Waiting 3.5 seconds for initial load."
                    )
                    await asyncio.sleep(3.5)
                except Exception as e:
                    raise ValueError(f"Unable to detect initial tracklist: {e}")

                js_script_playlist = """
                () => {
                    const tracksData = [];
                    const rows = document.querySelectorAll('music-image-row[primary-text]');
                    rows.forEach(row => {
                        const title = row.getAttribute('primary-text');
                        const artist = row.getAttribute('secondary-text-1');
                        const indexEl = row.querySelector('span.index');
                        const index = indexEl ? parseInt(indexEl.innerText.trim(), 10) : null;
                        if (title && artist && index !== null && !isNaN(index)) {
                            tracksData.push({ index: index, title: title.trim(), artist: artist.trim() });
                        }
                    });
                    tracksData.sort((a, b) => a.index - b.index);
                    return tracksData.map(t => ({ title: t.title, artist: t.artist }));
                }
                """
                tracks_data = await page.evaluate(js_script_playlist)
                tracks = [(track["title"], track["artist"]) for track in tracks_data]

            else:
                raise ValueError(
                    "Amazon Music URL not recognized (neither album, nor playlist, nor track)."
                )

            if not tracks:
                raise ValueError("No tracks could be extracted from the page.")

            logger.info(
                f"Processing complete. {len(tracks)} track(s) found. First track: {tracks[0]}"
            )
            return tracks

    except Exception as e:
        logger.error(
            f"Final error in process_amazon_music_url for {url}: {e}", exc_info=True
        )
        if "page" in locals() and page and not page.is_closed():
            await page.screenshot(path="amazon_music_scrape_failed.png")
            logger.info("Screenshot of the error saved.")

        embed = Embed(
            description=get_messages("amazon_music_error", guild_id),
            color=(
                0xFFB6C1
                if (get_guild_state(guild_id).locale == Locale.EN_X_KAWAII)
                else discord.Color.red()
            ),
        )
        try:
            if interaction and not interaction.is_expired():
                await interaction.followup.send(
                    silent=SILENT_MESSAGES, embed=embed, ephemeral=True
                )
        except Exception as send_error:
            logger.error(f"Unable to send error message: {send_error}")
        return None
    finally:
        if browser:
            await browser.close()
            logger.info("Playwright (Amazon Music) browser closed successfully.")


# --- Search & Extraction Helpers ---


# Normalize strings for search queries
def sanitize_query(query):
    query = re.sub(r"[\x00-\x1F\x7F]", "", query)  # Remove control chars
    query = re.sub(r"\s+", " ", query).strip()  # Normalize spaces
    return query


# YouTube Mix and SoundCloud Stations utilities
def get_video_id(url):
    parsed = urlparse(url)
    if parsed.hostname in ("youtube.com", "www.youtube.com", "youtu.be"):
        if parsed.hostname == "youtu.be":
            return parsed.path[1:]
        if parsed.path == "/watch":
            query = parse_qs(parsed.query)
            return query.get("v", [None])[0]
    return None


def get_mix_playlist_url(video_url):
    video_id = get_video_id(video_url)
    if video_id:
        return f"https://www.youtube.com/watch?v={video_id}&list=RD{video_id}"
    return None


def get_soundcloud_track_id(url):
    if "soundcloud.com" in url:
        try:
            ydl_opts = {
                "quiet": True,
                "no_warnings": True,
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                return info.get("id")
        except Exception:
            return None
    return None


def get_soundcloud_station_url(track_id):
    if track_id:
        return f"https://soundcloud.com/discover/sets/track-stations:{track_id}"
    return


def parse_yt_dlp_error(error_string: str) -> tuple[str, str, str]:
    """
    Parses a yt-dlp error string to find a known cause.
    Returns a tuple of (emoji, title_key, description_key).
    """
    error_lower = error_string.lower()
    if "sign in to confirm your age" in error_lower or "age-restricted" in error_lower:
        return ("🔞", "error.age_restricted.title", "error.age_restricted.description")
    if "private video" in error_lower:
        return ("🔒", "error.private.title", "error.private.description")
    if "video is unavailable" in error_lower:
        return ("❓", "error.unavailable.title", "error.unavailable.description")
    # Default fallback for other access errors
    return ("🚫", "error.generic_access.title", "error.generic_access.description")


# ==============================================================================
# 4. CORE AUDIO & PLAYBACK LOGIC
# ==============================================================================


async def handle_playback_error(guild_id: int, error: Exception):
    """
    Handles unexpected errors during playback, informs the user,
    and provides instructions for reporting the bug.
    """
    state = get_guild_state(guild_id)
    music_player = state.music_player
    if not music_player.text_channel:
        logger.error(
            f"Cannot report error in guild {guild_id}, no text channel available."
        )
        return

    tb_str = "".join(
        traceback.format_exception(type(error), value=error, tb=error.__traceback__)
    )
    logger.error(f"Unhandled playback error in guild {guild_id}:\n{tb_str}")

    state = get_guild_state(guild_id)
    is_kawaii = state.locale == Locale.EN_X_KAWAII

    error_str = str(error).lower()
    is_forbidden = "403" in error_str or "forbidden" in error_str

    if is_forbidden:
        embed = Embed(
            title=get_messages("error.youtube_403.title", guild_id),
            description=get_messages("error.youtube_403.description", guild_id),
            color=0xFF9AA2 if is_kawaii else discord.Color.orange(),
        )
        embed.add_field(
            name=get_messages("error.youtube_403.fix_field", guild_id),
            value=get_messages("error.youtube_403.fix_value", guild_id),
            inline=False,
        )
    else:
        embed = Embed(
            title=get_messages("error.critical.title", guild_id),
            description=get_messages("error.critical.description", guild_id),
            color=0xFF9AA2 if is_kawaii else discord.Color.red(),
        )
        embed.add_field(
            name=get_messages("error.critical.report_field", guild_id),
            value=get_messages("error.critical.report_value", guild_id),
            inline=False,
        )

    error_details = get_messages(
        "error.critical.details_format",
        guild_id,
        url=music_player.current_url,
        error_summary=str(error)[:500],
    )
    embed.add_field(
        name=get_messages("error.critical.details_field", guild_id),
        value=f"```\n{error_details}\n```",
        inline=False,
    )
    embed.set_footer(text=get_messages("error.critical.footer", guild_id))

    try:
        await music_player.text_channel.send(embed=embed, silent=SILENT_MESSAGES)
    except discord.Forbidden:
        logger.warning(
            f"Failed to send error report to guild {guild_id}: Missing Permissions."
        )
    except Exception as e:
        logger.error(f"Failed to send error report embed to guild {guild_id}: {e}")

    music_player.current_task = None
    music_player.current_info = None
    music_player.current_url = None
    while not music_player.queue.empty():
        music_player.queue.get_nowait()

    if music_player.voice_client:
        await music_player.voice_client.disconnect()
        get_guild_state(guild_id).music_player = MusicPlayer()
        logger.info(
            f"Player for guild {guild_id} has been reset and disconnected due to a critical error."
        )


# ==============================================================================
# 4. CORE AUDIO & PLAYBACK LOGIC
# ==============================================================================


async def play_audio(guild_id, seek_time=0, is_a_loop=False, song_that_just_ended=None):
    state = get_guild_state(guild_id)
    music_player = state.music_player
    state = get_guild_state(guild_id)
    is_kawaii = state.locale == Locale.EN_X_KAWAII

    if (
        music_player.voice_client
        and music_player.voice_client.is_playing()
        and not is_a_loop
        and not seek_time > 0
    ):
        return

    async def after_playing(error):
        if error:
            logger.error(f"Error after playing in guild {guild_id}: {error}")

        if music_player.is_paused_by_leave:
            logger.info(
                f"[{guild_id}] Playback intentionally paused due to empty channel. Not proceeding to next track."
            )
            return

        song_that_finished = music_player.current_info

        if music_player.manual_stop:
            logger.warning(
                f"[{guild_id}] after_playing: Manual stop detected. Bypassing 24/7 logic."
            )
            music_player.manual_stop = False
            bot.loop.create_task(
                play_audio(
                    guild_id, is_a_loop=False, song_that_just_ended=song_that_finished
                )
            )
            return

        if (
            not music_player.voice_client
            or not music_player.voice_client.is_connected()
        ):
            logger.info(
                f"[{guild_id}] after_playing: Voice client disconnected, stopping playback loop."
            )
            return
        if music_player.is_reconnecting:
            return

        if music_player.seek_info is not None:
            new_seek_time = music_player.seek_info
            music_player.seek_info = None
            bot.loop.create_task(
                play_audio(guild_id, seek_time=new_seek_time, is_a_loop=True)
            )
            return

        if music_player.loop_current:
            bot.loop.create_task(play_audio(guild_id, is_a_loop=True))
            return

        music_player.current_info = None

        if song_that_finished:
            track_to_requeue = create_queue_item_from_info(song_that_finished, guild_id)
            if (
                get_guild_state(guild_id)._24_7_mode
                and not music_player.autoplay_enabled
            ):
                await music_player.queue.put(track_to_requeue)

        bot.loop.create_task(
            play_audio(
                guild_id, is_a_loop=False, song_that_just_ended=song_that_finished
            )
        )

    try:
        if not (is_a_loop or seek_time > 0):
            if music_player.lyrics_task and not music_player.lyrics_task.done():
                music_player.lyrics_task.cancel()

            if music_player.queue.empty():
                if (
                    get_guild_state(guild_id)._24_7_mode
                    and not music_player.autoplay_enabled
                    and music_player.radio_playlist
                ):
                    for track_info_radio in music_player.radio_playlist:
                        await music_player.queue.put(track_info_radio)

                elif (
                    get_guild_state(guild_id)._24_7_mode
                    and music_player.autoplay_enabled
                ) or music_player.autoplay_enabled:
                    music_player.suppress_next_now_playing = False

                    seed_url = None
                    progress_message = None

                    seed_source_info = song_that_just_ended or (
                        music_player.history[-1] if music_player.history else None
                    )

                    if seed_source_info:
                        url_to_test = seed_source_info.get(
                            "webpage_url"
                        ) or seed_source_info.get("url", "")

                        if IS_PUBLIC_VERSION and (
                            "youtube.com" in url_to_test or "youtu.be" in url_to_test
                        ):
                            url_to_test = ""

                        if any(
                            s in url_to_test
                            for s in ["youtube.com", "youtu.be", "soundcloud.com"]
                        ):
                            seed_url = url_to_test
                        else:
                            if music_player.text_channel:
                                try:
                                    notice_key = (
                                        "autoplay.file_notice"
                                        if seed_source_info.get("source_type") == "file"
                                        else "autoplay.direct_link_notice"
                                    )
                                    notice_embed = Embed(
                                        description=get_messages(notice_key, guild_id),
                                        color=(
                                            0xFFB6C1
                                            if is_kawaii
                                            else discord.Color.blue()
                                        ),
                                    )
                                    progress_message = (
                                        await music_player.text_channel.send(
                                            embed=notice_embed, silent=SILENT_MESSAGES
                                        )
                                    )
                                except discord.Forbidden:
                                    pass

                            source_list = (
                                music_player.radio_playlist
                                if get_guild_state(guild_id)._24_7_mode
                                and music_player.radio_playlist
                                else music_player.history
                            )
                            for track in reversed(source_list):
                                fallback_url_to_test = track.get(
                                    "webpage_url"
                                ) or track.get("url", "")
                                if fallback_url_to_test and any(
                                    s in fallback_url_to_test
                                    for s in [
                                        "youtube.com",
                                        "youtu.be",
                                        "soundcloud.com",
                                    ]
                                ):
                                    if IS_PUBLIC_VERSION and (
                                        "youtube.com" in fallback_url_to_test
                                        or "youtu.be" in fallback_url_to_test
                                    ):
                                        continue
                                    seed_url = fallback_url_to_test
                                    break
                        if seed_url:
                            added_count = 0
                            try:
                                if not progress_message and music_player.text_channel:
                                    initial_embed = Embed(
                                        title=get_messages(
                                            "autoplay.loading_title", guild_id
                                        ),
                                        description=get_messages(
                                            "autoplay.loading_description",
                                            guild_id,
                                            progress_bar=create_loading_bar(0),
                                            processed=0,
                                            total="?",
                                        ),
                                        color=(
                                            0xC7CEEA
                                            if is_kawaii
                                            else discord.Color.blue()
                                        ),
                                    )
                                    progress_message = (
                                        await music_player.text_channel.send(
                                            embed=initial_embed, silent=SILENT_MESSAGES
                                        )
                                    )

                                recommendations = []
                                if "youtube.com" in seed_url or "youtu.be" in seed_url:
                                    mix_playlist_url = get_mix_playlist_url(seed_url)
                                    if mix_playlist_url:
                                        info = await run_ydl_with_low_priority(
                                            {
                                                "extract_flat": True,
                                                "quiet": True,
                                                "noplaylist": False,
                                            },
                                            mix_playlist_url,
                                        )
                                        if info.get("entries"):
                                            current_video_id = get_video_id(seed_url)
                                            recommendations = [
                                                entry
                                                for entry in info["entries"]
                                                if entry
                                                and get_video_id(entry.get("url", ""))
                                                != current_video_id
                                            ][:50]
                                elif "soundcloud.com" in seed_url:
                                    track_id = get_soundcloud_track_id(seed_url)
                                    station_url = get_soundcloud_station_url(track_id)
                                    if station_url:
                                        info = await run_ydl_with_low_priority(
                                            {
                                                "extract_flat": True,
                                                "quiet": True,
                                                "noplaylist": False,
                                            },
                                            station_url,
                                        )
                                        if (
                                            info.get("entries")
                                            and len(info.get("entries")) > 1
                                        ):
                                            recommendations = info["entries"][1:]

                                if recommendations and progress_message:
                                    total_to_add = len(recommendations)
                                    original_requester = (
                                        seed_source_info.get("requester", bot.user)
                                        if seed_source_info
                                        else bot.user
                                    )

                                    for i, entry in enumerate(recommendations):
                                        await music_player.queue.put(
                                            {
                                                "url": entry.get("url"),
                                                "title": entry.get(
                                                    "title", "Unknown Title"
                                                ),
                                                "webpage_url": entry.get(
                                                    "webpage_url", entry.get("url")
                                                ),
                                                "is_single": True,
                                                "requester": original_requester,
                                            }
                                        )
                                        added_count += 1

                                        if (i + 1) % 10 == 0 or (i + 1) == total_to_add:
                                            progress = (i + 1) / total_to_add
                                            updated_embed = progress_message.embeds[0]
                                            updated_embed.description = get_messages(
                                                "autoplay.loading_description",
                                                guild_id,
                                                progress_bar=create_loading_bar(
                                                    progress
                                                ),
                                                processed=added_count,
                                                total=total_to_add,
                                            )
                                            await progress_message.edit(
                                                embed=updated_embed
                                            )
                                            await asyncio.sleep(0.5)
                            except Exception as e:
                                logger.error(
                                    f"Autoplay progress UI error: {e}", exc_info=True
                                )
                            finally:
                                if progress_message and added_count > 0:
                                    final_embed = progress_message.embeds[0]
                                    final_embed.title = None
                                    final_embed.description = get_messages(
                                        "autoplay.finished_description",
                                        guild_id,
                                        count=added_count,
                                    )
                                    final_embed.color = (
                                        0xB5EAD7 if is_kawaii else discord.Color.green()
                                    )
                                    await progress_message.edit(embed=final_embed)
                                elif progress_message and added_count == 0:
                                    await progress_message.delete()

                if music_player.queue.empty():
                    music_player.current_task = None
                    bot.loop.create_task(update_controller(bot, guild_id))
                    if not get_guild_state(guild_id)._24_7_mode:
                        await asyncio.sleep(60)
                        if (
                            music_player.voice_client
                            and not music_player.voice_client.is_playing()
                            and len(music_player.voice_client.channel.members) == 1
                        ):
                            await music_player.voice_client.disconnect()
                    return

            next_item = await music_player.queue.get()
            full_playback_info = None
            if isinstance(next_item, LazySearchItem):
                logger.info(f"[{guild_id}] Lazy track detected, initiating resolution.")
                resolved_info = await next_item.resolve()

                if not resolved_info or resolved_info.get("error"):
                    failed_title = resolved_info.get("title", "unknown")
                    logger.warning(
                        f"[{guild_id}] Failed to resolve track '{failed_title}', skipping to the next one."
                    )
                    if music_player.text_channel:
                        try:
                            error_embed = Embed(
                                title=get_messages(
                                    "lazy_resolve.error.title", guild_id
                                ),
                                description=get_messages(
                                    "lazy_resolve.error.description",
                                    guild_id,
                                    title=failed_title,
                                ),
                                color=0xFF9AA2 if is_kawaii else discord.Color.red(),
                            )
                            await music_player.text_channel.send(
                                embed=error_embed, silent=SILENT_MESSAGES
                            )
                        except discord.Forbidden:
                            pass
                    bot.loop.create_task(
                        play_audio(
                            guild_id, song_that_just_ended=music_player.current_info
                        )
                    )
                    return

                full_playback_info = resolved_info
            else:
                full_playback_info = next_item

            if "requester" not in full_playback_info:
                full_playback_info["requester"] = bot.user

            if full_playback_info.pop("skip_now_playing", False):
                music_player.suppress_next_now_playing = True

            music_player.current_info = full_playback_info

            if not music_player.loop_current:
                music_player.history.append(full_playback_info)

        if (
            not music_player.voice_client
            or not music_player.voice_client.is_connected()
            or not music_player.current_info
        ):
            logger.warning(
                f"[{guild_id}] Play audio called but a condition was not met. Aborting."
            )
            return

        url_for_fetching = music_player.current_info.get(
            "webpage_url"
        ) or music_player.current_info.get("url")

        if music_player.current_info.get("source_type") != "file":
            logger.info(
                f"[{guild_id}] Refreshing stream URL for '{music_player.current_info.get('title')}' to prevent expiration."
            )
            try:
                refreshed_info = await fetch_video_info_with_retry(url_for_fetching)
                music_player.current_info.update(refreshed_info)
            except Exception as e:
                logger.error(
                    f"[{guild_id}] FAILED to refresh stream URL for {url_for_fetching}: {e}",
                    exc_info=True,
                )
                if music_player.text_channel:
                    try:
                        emoji, title_key, desc_key = parse_yt_dlp_error(str(e))
                        embed = Embed(
                            title=f'{emoji} {get_messages("error.playback_failed.title", guild_id)}',
                            description=get_messages(desc_key, guild_id)
                            + "\n*"
                            + get_messages("player.track_will_be_skipped", guild_id)
                            + "*",
                            color=0xFF9AA2 if is_kawaii else discord.Color.red(),
                        )
                        embed.add_field(
                            name=get_messages(
                                "error.generic.affected_url_field", guild_id
                            ),
                            value=f"`{url_for_fetching}`",
                        )
                        await music_player.text_channel.send(
                            embed=embed, silent=SILENT_MESSAGES
                        )
                    except discord.Forbidden:
                        pass
                bot.loop.create_task(
                    play_audio(guild_id, song_that_just_ended=music_player.current_info)
                )
                return

        audio_url = music_player.current_info.get("url")
        if not audio_url:
            logger.error(
                f"[{guild_id}] Playback info retrieved but 'url' key is missing after refresh. Skipping."
            )
            bot.loop.create_task(
                play_audio(guild_id, song_that_just_ended=music_player.current_info)
            )
            return

        music_player.is_current_live = (
            music_player.current_info.get("is_live", False)
            or music_player.current_info.get("live_status") == "is_live"
        )

        active_filters = state.server_filters
        filter_chain = (
            ",".join([AUDIO_FILTERS[f] for f in active_filters if f in active_filters])
            if active_filters
            else ""
        )

        ffmpeg_options = {"options": "-vn"}
        if music_player.current_info.get("source_type") != "file":
            ffmpeg_options["before_options"] = (
                "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
            )
        if seek_time > 0:
            ffmpeg_options["before_options"] = (
                f"-ss {seek_time} {ffmpeg_options.get('before_options', '')}".strip()
            )
        if filter_chain:
            ffmpeg_options["options"] = (
                f"{ffmpeg_options.get('options', '')} -af \"{filter_chain}\"".strip()
            )

        source = discord.PCMVolumeTransformer(
            discord.FFmpegPCMAudio(audio_url, **ffmpeg_options),
            volume=music_player.volume,
        )

        callback = lambda e: bot.loop.create_task(after_playing(e))

        if (
            not music_player.voice_client
            or not music_player.voice_client.is_connected()
        ):
            logger.warning(
                f"[{guild_id}] Playback canceled at the last moment: voice client is no longer valid."
            )
            return

        music_player.voice_client.play(source, after=callback)

        music_player.start_time = seek_time
        music_player.playback_started_at = time.time()

        state = get_guild_state(guild_id)
        if state.controller_channel_id and not is_a_loop and seek_time == 0:
            channel_id = state.controller_channel_id
            message_id = state.controller_message_id

            if channel_id and message_id:
                try:
                    channel = bot.get_channel(channel_id)
                    if channel and channel.last_message_id != message_id:
                        logger.info(
                            f"[{guild_id}] Controller is not the last message. Re-anchoring."
                        )
                        old_message = await channel.fetch_message(message_id)
                        await old_message.delete()
                        get_guild_state(guild_id).controller_message_id = None
                except (discord.NotFound, discord.Forbidden):
                    logger.info(
                        f"[{guild_id}] Old controller not found during re-anchor check. Resetting."
                    )
                    get_guild_state(guild_id).controller_message_id = None
                except Exception as e:
                    logger.error(
                        f"[{guild_id}] Error in controller re-anchor check: {e}"
                    )

        bot.loop.create_task(update_controller(bot, guild_id))

        if music_player.suppress_next_now_playing:
            music_player.suppress_next_now_playing = False

    except Exception as e:
        await handle_playback_error(guild_id, e)


async def update_karaoke_task(guild_id: int):
    """Background task for karaoke mode, manages filters and speed."""
    state = get_guild_state(guild_id)
    music_player = state.music_player
    last_line_index = -1
    # We add a flag to know if the footer has already been removed
    footer_has_been_removed = False

    while music_player.voice_client and music_player.voice_client.is_connected():
        try:
            if not music_player.voice_client.is_playing():
                await asyncio.sleep(0.5)
                continue

            real_elapsed_time = time.time() - music_player.playback_started_at
            effective_time_in_song = music_player.start_time + (
                real_elapsed_time * music_player.playback_speed
            )

            current_line_index = -1
            for i, line in enumerate(music_player.synced_lyrics):
                if effective_time_in_song * 1000 >= line["time"]:
                    current_line_index = i
                else:
                    break

            if current_line_index != last_line_index:
                last_line_index = current_line_index
                new_description = format_lyrics_display(
                    music_player.synced_lyrics, current_line_index, guild_id
                )

                if music_player.lyrics_message and music_player.lyrics_message.embeds:
                    new_embed = music_player.lyrics_message.embeds[0]
                    new_embed.description = new_description

                    # --- START OF MODIFICATION ---
                    # If the footer has not been removed yet, we do it now.
                    if not footer_has_been_removed:
                        # This line removes the embed's footer
                        new_embed.set_footer(text=None)
                        # We set the flag to True so we never do it again for this song
                        footer_has_been_removed = True
                    # --- END OF MODIFICATION ---

                    await music_player.lyrics_message.edit(embed=new_embed)

            await asyncio.sleep(1.0)

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error in karaoke task: {e}")
            break

    if music_player.lyrics_message:
        try:
            await music_player.lyrics_message.edit(
                content=get_messages("karaoke.session_finished", guild_id),
                embed=None,
                view=None,
            )
        except discord.NotFound:
            pass

    music_player.lyrics_task = None
    music_player.lyrics_message = None


# ==============================================================================
# 5. DISCORD SLASH COMMANDS
# ==============================================================================


@bot.tree.command(name="lyrics", description="Get song lyrics from Genius.")
async def lyrics(interaction: discord.Interaction):
    if not interaction.guild:
        await interaction.response.send_message(
            get_messages("command.error.guild_only", interaction.guild_id),
            ephemeral=True,
            silent=SILENT_MESSAGES,
        )
        return

    guild_id = interaction.guild.id
    state = get_guild_state(guild_id)
    music_player = state.music_player

    if (
        not music_player.voice_client
        or not music_player.voice_client.is_playing()
        or not music_player.current_info
    ):
        return await interaction.response.send_message(
            get_messages("player.no_song.title", guild_id),
            silent=SILENT_MESSAGES,
            ephemeral=True,
        )

    await interaction.response.defer()
    await fetch_and_display_genius_lyrics(interaction)


@bot.tree.command(
    name="karaoke", description="Start a synced karaoke-style lyrics display."
)
async def karaoke(interaction: discord.Interaction):
    if not interaction.guild:
        await interaction.response.send_message(
            get_messages("command.error.guild_only", interaction.guild_id),
            ephemeral=True,
            silent=SILENT_MESSAGES,
        )
        return

    guild_id = interaction.guild_id
    state = get_guild_state(guild_id)
    music_player = state.music_player
    state = get_guild_state(guild_id)
    is_kawaii = state.locale == Locale.EN_X_KAWAII

    if (
        not music_player.voice_client
        or not music_player.voice_client.is_playing()
        or not music_player.current_info
    ):
        return await interaction.response.send_message(
            get_messages("player.no_playback.title", guild_id),
            silent=SILENT_MESSAGES,
            ephemeral=True,
        )

    if music_player.lyrics_task and not music_player.lyrics_task.done():
        return await interaction.response.send_message(
            get_messages("karaoke.error.already_running", guild_id),
            silent=SILENT_MESSAGES,
            ephemeral=True,
        )

    async def proceed_with_karaoke():
        if not interaction.response.is_done():
            await interaction.response.defer()

        clean_title, artist_name = get_cleaned_song_info(
            music_player.current_info, guild_id
        )
        loop = asyncio.get_running_loop()
        lrc = None

        # Attempt 1: Precise search
        try:
            precise_query = f"{clean_title} {artist_name}"
            logger.info(f"Attempting precise synced lyrics search: '{precise_query}'")
            lrc = await asyncio.wait_for(
                loop.run_in_executor(None, syncedlyrics.search, precise_query),
                timeout=7.0,
            )
        except (asyncio.TimeoutError, Exception):
            logger.warning("Precise synced search failed or timed out.")

        # Attempt 2: Broad search
        if not lrc:
            try:
                logger.info(f"Trying broad search: '{clean_title}'")
                lrc = await asyncio.wait_for(
                    loop.run_in_executor(None, syncedlyrics.search, clean_title),
                    timeout=7.0,
                )
            except (asyncio.TimeoutError, Exception):
                logger.warning("Broad synced search also failed or timed out.")

        # First, try to parse the lyrics if a result was found
        lyrics_lines = []
        if lrc:
            lyrics_lines = [
                {
                    "time": int(m.group(1)) * 60000
                    + int(m.group(2)) * 1000
                    + int(m.group(3)),
                    "text": m.group(4).strip(),
                }
                for line in lrc.splitlines()
                if (m := re.match(r"\[(\d{2}):(\d{2})\.(\d{2,3})\](.*)", line))
            ]

        # Now, a SINGLE check handles all failures (not found OR bad format)
        if not lyrics_lines:
            error_title = get_messages("karaoke.not_found_title", guild_id)
            error_desc = get_messages(
                "karaoke.not_found_description",
                guild_id,
                query=f"{clean_title} {artist_name}",
            )

            error_embed = Embed(
                title=error_title,
                description=error_desc,
                color=0xFF9AA2 if is_kawaii else discord.Color.red(),
            )

            view = KaraokeRetryView(
                original_interaction=interaction,
                suggested_query=clean_title,
                guild_id=guild_id,
            )
            # Use followup.send because the interaction is already deferred
            await interaction.followup.send(
                silent=SILENT_MESSAGES, embed=error_embed, view=view
            )
            return

        # If we get here, lyrics_lines is valid. Proceed with karaoke.
        music_player.synced_lyrics = lyrics_lines
        embed = Embed(
            title=get_messages("karaoke.embed.title", guild_id, title=clean_title),
            description=get_messages("karaoke.embed.description", guild_id),
            color=0xC7CEEA if is_kawaii else discord.Color.blue(),
        )

        lyrics_message = await interaction.followup.send(
            silent=SILENT_MESSAGES, embed=embed, wait=True
        )
        music_player.lyrics_message = lyrics_message
        music_player.lyrics_task = asyncio.create_task(update_karaoke_task(guild_id))

    # --- Warning logic (unchanged) ---
    if get_guild_state(guild_id).karaoke_disclaimer_shown:
        await proceed_with_karaoke()
    else:
        warning_embed = Embed(
            title=get_messages("karaoke.warning.title", guild_id),
            description=get_messages("karaoke.warning.description", guild_id),
            color=0xFFB6C1 if is_kawaii else discord.Color.orange(),
        )
        view = KaraokeWarningView(interaction, karaoke_coro=proceed_with_karaoke)

        button_label = get_messages("karaoke.warning.button", guild_id)
        view.children[0].label = button_label

        await interaction.response.send_message(
            silent=SILENT_MESSAGES, embed=warning_embed, view=view
        )


# /kaomoji command
@bot.tree.command(name="kaomoji", description="Enable/disable kawaii mode")
@app_commands.default_permissions(administrator=True)
async def toggle_kawaii(interaction: discord.Interaction):
    if not interaction.guild:
        await interaction.response.send_message(
            get_messages("command.error.guild_only", interaction.guild_id),
            ephemeral=True,
            silent=SILENT_MESSAGES,
        )
        return

    guild_id = interaction.guild_id
    state = get_guild_state(guild_id)
    if state.locale == Locale.EN_X_KAWAII:
        state.locale = Locale.EN_US
    else:
        state.locale = Locale.EN_X_KAWAII
    state = (
        get_messages("kawaii_state_enabled", guild_id)
        if (get_guild_state(guild_id).locale == Locale.EN_X_KAWAII)
        else get_messages("kawaii_state_disabled", guild_id)
    )

    embed = Embed(
        description=get_messages("kawaii_toggle", guild_id, state=state),
        color=(
            0xFFB6C1
            if (get_guild_state(guild_id).locale == Locale.EN_X_KAWAII)
            else discord.Color.blue()
        ),
    )
    await interaction.response.send_message(
        silent=SILENT_MESSAGES, embed=embed, ephemeral=True
    )


async def play_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    """Provides real-time search suggestions for the /play command, including duration."""
    # Don't start a search if the user hasn't typed at least 3 characters
    if not current or len(current) < 3:
        return []

    # If the input looks like a URL, don't show any suggestions.
    if re.match(r"https?://", current):
        return []

    try:
        sanitized_query = sanitize_query(current)

        # 🔧 OPTIMISATION 1: Réduire à 3 résultats max pour plus de rapidité
        search_prefix = "scsearch3:" if IS_PUBLIC_VERSION else "ytsearch3:"
        search_query = f"{search_prefix}{sanitized_query}"

        # 🔧 OPTIMISATION 2: Timeout strict de 2 secondes pour respecter la limite Discord
        info = await asyncio.wait_for(
            fetch_video_info_with_retry(
                search_query,
                ydl_opts_override={
                    "extract_flat": True,
                    "noplaylist": True,
                    "socket_timeout": 5,
                },
            ),
            timeout=2.0,
        )

        choices = []
        if "entries" in info and info["entries"]:
            for entry in info.get("entries", [])[:3]:  # 🔧 Limiter à 3 choix max
                title = entry.get("title", "Unknown Title")
                url = entry.get("webpage_url", entry.get("url"))
                duration_seconds = entry.get("duration")

                if title and url:
                    display_name = title
                    if duration_seconds:
                        formatted_duration = format_duration(duration_seconds)
                        display_name = f"{title} - {formatted_duration}"

                    if len(display_name) > 100:
                        display_name = display_name[:97] + "..."

                    choice_value = url if len(url) <= 100 else title[:100]
                    choices.append(
                        app_commands.Choice(name=display_name, value=choice_value)
                    )

        return choices

    except asyncio.TimeoutError:
        # 🔧 Retourner vide si timeout : mieux que de crasher
        logger.warning(f"Autocomplete timeout for query: '{current}'")
        return []

    except Exception as e:
        logger.error(f"Autocomplete search for '{current}' failed: {e}")
        return []  # Toujours retourner une liste, jamais lever d'exception


@bot.tree.command(name="play", description="Play a link or search for a song")
@app_commands.describe(query="Link or title of the song/video to play")
@app_commands.autocomplete(query=play_autocomplete)
async def play(interaction: discord.Interaction, query: str):
    if not interaction.guild:
        await interaction.response.send_message(
            get_messages("command.error.guild_only", interaction.guild_id),
            ephemeral=True,
        )
        return

    guild_id = interaction.guild.id
    state = get_guild_state(guild_id)
    is_kawaii = state.locale == Locale.EN_X_KAWAII
    state = get_guild_state(guild_id)
    music_player = state.music_player

    if not interaction.response.is_done():
        await interaction.response.defer()

    if IS_PUBLIC_VERSION and re.search(
        r"youtube\.com|youtu\.be|music\.youtube\.com", query
    ):
        await show_youtube_blocked_message(interaction)
        return

    voice_client = await ensure_voice_connection(interaction)
    if not voice_client:
        return

    async def add_and_update_controller(info: dict):
        queue_item = {
            "url": info.get("webpage_url", info.get("url", "#")),
            "title": info.get("title", "Unknown Title"),
            "webpage_url": info.get("webpage_url", info.get("url", "#")),
            "thumbnail": info.get("thumbnail"),
            "is_single": True,
            "requester": interaction.user,
        }
        await music_player.queue.put(queue_item)
        await update_controller(bot, guild_id, interaction=interaction)
        if (
            not music_player.voice_client.is_playing()
            and not music_player.voice_client.is_paused()
        ):
            music_player.current_task = asyncio.create_task(play_audio(guild_id))

    async def handle_platform_playlist(platform_tracks, platform_name):
        total_tracks = len(platform_tracks)
        logger.info(
            f"[{guild_id}] Lazily adding {total_tracks} tracks from {platform_name}."
        )
        for track_name, artist_name in platform_tracks:
            lazy_item = LazySearchItem(
                query_dict={"name": track_name, "artist": artist_name},
                requester=interaction.user,
                original_platform=platform_name,
            )
            await music_player.queue.put(lazy_item)

        platform_key_map = {
            "Spotify": ("spotify_playlist_added", "spotify_playlist_description"),
            "Deezer": ("deezer_playlist_added", "deezer_playlist_description"),
            "Apple Music": (
                "apple_music_playlist_added",
                "apple_music_playlist_description",
            ),
            "Tidal": ("tidal_playlist_added", "tidal_playlist_description"),
            "Amazon Music": (
                "amazon_music_playlist_added",
                "amazon_music_playlist_description",
            ),
        }
        title_key, desc_key = platform_key_map.get(platform_name)

        embed = Embed(
            title=get_messages(title_key, guild_id),
            description=get_messages(
                desc_key, guild_id, count=total_tracks, failed=0, failed_tracks=""
            ),
            color=0xB5EAD7 if is_kawaii else discord.Color.green(),
        )
        await interaction.followup.send(silent=SILENT_MESSAGES, embed=embed)

        if (
            not music_player.voice_client.is_playing()
            and not music_player.voice_client.is_paused()
        ):
            music_player.current_task = asyncio.create_task(play_audio(guild_id))

        bot.loop.create_task(update_controller(bot, guild_id))

    try:
        # Regex for platforms that require conversion (Spotify, Deezer, etc.)
        spotify_regex = re.compile(r"^(https?://)?(open\.spotify\.com)/.+$")
        deezer_regex = re.compile(
            r"^(https?://)?((www\.)?deezer\.com/(?:[a-z]{2}/)?(track|playlist|album|artist)/.+|(link\.deezer\.com)/s/.+)$"
        )
        apple_music_regex = re.compile(r"^(https?://)?(music\.apple\.com)/.+$")
        tidal_regex = re.compile(r"^(https?://)?(www\.)?tidal\.com/.+$")
        amazon_music_regex = re.compile(
            r"^(https?://)?(music\.amazon\.(fr|com|co\.uk|de|es|it|jp))/.+$"
        )

        # Regex for direct platforms (those that yt-dlp handles natively)
        direct_platform_regex = re.compile(
            r"^(https?://)?((www|m)\.)?(youtube\.com|youtu\.be|music\.youtube\.com|soundcloud\.com|twitch\.tv)|([^\.]+)\.bandcamp\.com/.+$"
        )
        direct_link_regex = re.compile(
            r"^(https?://).+\.(mp3|wav|ogg|m4a|mp4|webm|flac)(\?.+)?$", re.IGNORECASE
        )

        # Blocking logic for the public version
        if IS_PUBLIC_VERSION and re.search(r"youtube\.com|youtu\.be", query):
            return

        # Cas 1: Plateformes nécessitant une conversion (Spotify, etc.)
        platform_processor = None
        if spotify_regex.match(query):
            platform_processor, platform_name = process_spotify_url, "Spotify"
        elif deezer_regex.match(query):
            platform_processor, platform_name = process_deezer_url, "Deezer"
        elif apple_music_regex.match(query):
            platform_processor, platform_name = process_apple_music_url, "Apple Music"
        elif tidal_regex.match(query):
            platform_processor, platform_name = process_tidal_url, "Tidal"
        elif amazon_music_regex.match(query):
            platform_processor, platform_name = process_amazon_music_url, "Amazon Music"

        if platform_processor:
            platform_tracks = await platform_processor(query, interaction)
            if platform_tracks:
                if len(platform_tracks) == 1:
                    # Conversion d'une seule piste
                    track_name, artist_name = platform_tracks[0]
                    search_term = f"{track_name} {artist_name}"
                    search_prefix = "scsearch:" if IS_PUBLIC_VERSION else "ytsearch:"
                    info = await fetch_video_info_with_retry(
                        f"{search_prefix}{sanitize_query(search_term)}",
                        ydl_opts_override={"noplaylist": True},
                    )
                    video = info["entries"][0]
                    await add_and_update_controller(video)
                else:
                    # Gestion d'une playlist complète
                    await handle_platform_playlist(platform_tracks, platform_name)
            return  # On a fini avec ce cas

        # Cas 2: Plateformes directes (SoundCloud, YouTube, Bandcamp, lien .mp3)
        if direct_platform_regex.match(query) or direct_link_regex.match(query):
            info = await fetch_video_info_with_retry(
                query, ydl_opts_override={"extract_flat": True, "noplaylist": False}
            )

            if "entries" in info and len(info["entries"]) > 1:
                # C'est une playlist, on ajoute chaque URL dans un dictionnaire simple.
                tracks_to_add = info["entries"]
                logger.info(
                    f"[{guild_id}] Adding {len(tracks_to_add)} raw tracks from a direct playlist."
                )
                for entry in tracks_to_add:
                    # WE DO NOT CREATE A LAZYSEARCHITEM, just a dictionary with the URL.
                    # Hydration will be done as needed by play_audio and create_controller_embed..
                    await music_player.queue.put(
                        {
                            "url": entry.get("url"),
                            "requester": interaction.user,
                            # We put a temporary title for the initial display if possible
                            "title": entry.get(
                                "title",
                                get_messages("player.loading_placeholder", guild_id),
                            ),
                        }
                    )

                embed = Embed(
                    title=get_messages("playlist_added", guild_id),
                    description=get_messages(
                        "playlist_description", guild_id, count=len(tracks_to_add)
                    ),
                    color=0xB5EAD7 if is_kawaii else discord.Color.green(),
                )
                await interaction.followup.send(embed=embed, silent=SILENT_MESSAGES)

                if (
                    not music_player.voice_client.is_playing()
                    and not music_player.voice_client.is_paused()
                ):
                    music_player.current_task = asyncio.create_task(
                        play_audio(guild_id)
                    )
            else:
                # C'est une piste unique
                video_info = info.get("entries", [info])[0]
                await add_and_update_controller(video_info)
            return  # On a fini

        # Cas 3: C'est une recherche par mot-clé
        search_prefix = "scsearch:" if IS_PUBLIC_VERSION else "ytsearch:"
        search_query = f"{search_prefix}{sanitize_query(query)}"
        info = await fetch_video_info_with_retry(
            search_query, ydl_opts_override={"noplaylist": True}
        )

        if not info.get("entries"):
            raise Exception("No results found.")

        video_info = info["entries"][0]
        await add_and_update_controller(video_info)

    except Exception as e:
        logger.error(f"Error in /play for '{query}': {e}", exc_info=True)
        await handle_playback_error(guild_id, e)


@bot.tree.command(
    name="play-files", description="Plays one or more uploaded audio or video files."
)
@app_commands.describe(
    file1="The first audio/video file to play.",
    file2="An optional audio/video file.",
    file3="An optional audio/video file.",
    file4="An optional audio/video file.",
    file5="An optional audio/video file.",
    file6="An optional audio/video file.",
    file7="An optional audio/video file.",
    file8="An optional audio/video file.",
    file9="An optional audio/video file.",
    file10="An optional audio/video file.",
)
async def play_files(
    interaction: discord.Interaction,
    file1: discord.Attachment,
    file2: discord.Attachment = None,
    file3: discord.Attachment = None,
    file4: discord.Attachment = None,
    file5: discord.Attachment = None,
    file6: discord.Attachment = None,
    file7: discord.Attachment = None,
    file8: discord.Attachment = None,
    file9: discord.Attachment = None,
    file10: discord.Attachment = None,
):
    """
    Downloads, saves, and queues one or more user-uploaded audio/video files.
    """
    if not interaction.guild:
        await interaction.response.send_message(
            get_messages("command.error.guild_only", interaction.guild_id),
            ephemeral=True,
            silent=SILENT_MESSAGES,
        )
        return

    guild_id = interaction.guild_id
    state = get_guild_state(guild_id)
    is_kawaii = state.locale == Locale.EN_X_KAWAII
    state = get_guild_state(guild_id)
    music_player = state.music_player

    await interaction.response.defer()

    voice_client = await ensure_voice_connection(interaction)
    if not voice_client:
        return

    base_cache_dir = "audio_cache"
    guild_cache_dir = os.path.join(base_cache_dir, str(guild_id))
    os.makedirs(guild_cache_dir, exist_ok=True)

    attachments = [
        f
        for f in [file1, file2, file3, file4, file5, file6, file7, file8, file9, file10]
        if f is not None
    ]

    added_files = []
    failed_files = []

    for attachment in attachments:
        if not attachment.content_type or not (
            attachment.content_type.startswith("audio/")
            or attachment.content_type.startswith("video/")
        ):
            failed_files.append(attachment.filename)
            continue

        file_path = os.path.join(guild_cache_dir, attachment.filename)
        try:
            await attachment.save(file_path)
            logger.info(f"File saved for guild {guild_id}: {file_path}")

            duration = get_file_duration(file_path)

            queue_item = {
                "url": file_path,
                "title": attachment.filename,
                "webpage_url": None,
                "thumbnail": None,
                "is_single": True,
                "source_type": "file",
                "duration": duration,
                "requester": interaction.user,
            }

            await music_player.queue.put(queue_item)
            added_files.append(attachment.filename)

            if get_guild_state(guild_id)._24_7_mode:
                music_player.radio_playlist.append(queue_item)
                logger.info(
                    f"Added '{attachment.filename}' to the active 24/7 radio playlist for guild {guild_id}."
                )

        except Exception as e:
            logger.error(f"Failed to process file {attachment.filename}: {e}")
            failed_files.append(attachment.filename)
            continue

    if not added_files:
        await interaction.followup.send(
            embed=Embed(
                description=get_messages(
                    "player.play_files.error.no_valid_files", guild_id
                ),
                color=0xFF9AA2 if is_kawaii else discord.Color.red(),
            ),
            ephemeral=True,
            silent=SILENT_MESSAGES,
        )
        return

    description = get_messages(
        "player.play_files.success.description",
        guild_id,
        count=len(added_files),
        file_list="\n".join([f"• {name}" for name in added_files[:10]]),
    )
    if len(added_files) > 10:
        description += f"\n... and {len(added_files) - 10} more."
    if failed_files:
        description += get_messages(
            "player.play_files.success.footer_failed", guild_id, count=len(failed_files)
        )

    embed = Embed(
        title=get_messages("player.play_files.success.title", guild_id),
        description=description,
        color=0xB5EAD7 if is_kawaii else discord.Color.blue(),
    )
    await interaction.followup.send(embed=embed, silent=SILENT_MESSAGES)

    if (
        not music_player.voice_client.is_playing()
        and not music_player.voice_client.is_paused()
    ):
        music_player.current_task = asyncio.create_task(play_audio(guild_id))


# /queue command
@bot.tree.command(
    name="queue", description="Show the current song queue and status with pages."
)
async def queue(interaction: discord.Interaction):
    if not interaction.guild:
        await interaction.response.send_message(
            get_messages("command.error.guild_only", interaction.guild_id),
            ephemeral=True,
            silent=SILENT_MESSAGES,
        )
        return

    await interaction.response.defer()
    guild_id = interaction.guild.id
    state = get_guild_state(guild_id)
    music_player = state.music_player

    is_24_7_normal = (
        get_guild_state(guild_id)._24_7_mode and not music_player.autoplay_enabled
    )
    tracks_for_display = []

    if is_24_7_normal and music_player.radio_playlist:
        current_url = (
            music_player.current_info.get("url") if music_player.current_info else None
        )
        try:
            current_index = [t.get("url") for t in music_player.radio_playlist].index(
                current_url
            )
            tracks_for_display = (
                music_player.radio_playlist[current_index + 1 :]
                + music_player.radio_playlist[: current_index + 1]
            )
        except (ValueError, IndexError):
            tracks_for_display = music_player.radio_playlist
    else:
        tracks_for_display = list(music_player.queue._queue)

    if not tracks_for_display and not music_player.current_info:
        state = get_guild_state(guild_id)
        is_kawaii = state.locale == Locale.EN_X_KAWAII
        embed = Embed(
            description=get_messages("queue_empty", guild_id),
            color=0xFF9AA2 if is_kawaii else discord.Color.red(),
        )
        await interaction.followup.send(
            silent=SILENT_MESSAGES, embed=embed, ephemeral=True
        )
        return

    view = QueueView(
        interaction=interaction, tracks=tracks_for_display, items_per_page=5
    )
    view.update_button_states()
    initial_embed = await view.create_queue_embed()
    message = await interaction.followup.send(
        embed=initial_embed, view=view, silent=SILENT_MESSAGES
    )
    view.message = message


@bot.tree.command(name="clearqueue", description="Clear the current queue")
async def clear_queue(interaction: discord.Interaction):
    if not interaction.guild:
        await interaction.response.send_message(
            get_messages("command.error.guild_only", interaction.guild_id),
            ephemeral=True,
            silent=SILENT_MESSAGES,
        )
        return

    guild_id = interaction.guild_id
    state = get_guild_state(guild_id)
    is_kawaii = state.locale == Locale.EN_X_KAWAII
    state = get_guild_state(guild_id)
    music_player = state.music_player

    bot.loop.create_task(update_controller(bot, interaction.guild.id))

    while not music_player.queue.empty():
        music_player.queue.get_nowait()

    music_player.history.clear()
    music_player.radio_playlist.clear()

    embed = Embed(
        description=get_messages("clear_queue_success", guild_id),
        color=0xB5EAD7 if is_kawaii else discord.Color.green(),
    )
    await interaction.response.send_message(silent=SILENT_MESSAGES, embed=embed)


@bot.tree.command(
    name="playnext", description="Add a song or a local file to play next"
)
@app_commands.describe(
    query="Link or title of the video/song to play next.",
    file="The local audio/video file to play next.",
)
async def play_next(
    interaction: discord.Interaction, query: str = None, file: discord.Attachment = None
):
    if not interaction.guild:
        await interaction.response.send_message(
            get_messages("command.error.guild_only", interaction.guild_id),
            ephemeral=True,
            silent=SILENT_MESSAGES,
        )
        return

    guild_id = interaction.guild.id
    state = get_guild_state(guild_id)
    is_kawaii = state.locale == Locale.EN_X_KAWAII
    state = get_guild_state(guild_id)
    music_player = state.music_player

    if (query and file) or (not query and not file):
        embed = Embed(
            description=get_messages("player.play_next.error.invalid_args", guild_id),
            color=0xFF9AA2 if is_kawaii else discord.Color.red(),
        )
        await interaction.response.send_message(
            embed=embed, ephemeral=True, silent=SILENT_MESSAGES
        )
        return

    await interaction.response.defer()

    # Define the helper function to show the YouTube blocked message
    async def show_youtube_blocked_message():
        embed = Embed(
            title=get_messages("error.youtube_blocked.title", guild_id),
            description=get_messages("error.youtube_blocked.description", guild_id),
            color=0xFF9AA2 if is_kawaii else discord.Color.orange(),
        )
        embed.add_field(
            name=get_messages("error.youtube_blocked.repo_field", guild_id),
            value=get_messages("error.youtube_blocked.repo_value", guild_id),
        )
        await interaction.followup.send(embed=embed, ephemeral=True, silent=True)

    # FIX: Check if the query is a YouTube link at the beginning
    if query:
        youtube_regex = re.compile(
            r"^(https?://)?((www|m)\.)?(youtube\.com|youtu\.be)/.+$"
        )
        ytmusic_regex = re.compile(r"^(https?://)?(music\.youtube\.com)/.+$")
        if IS_PUBLIC_VERSION and (
            youtube_regex.match(query) or ytmusic_regex.match(query)
        ):
            await show_youtube_blocked_message()
            return

    voice_client = await ensure_voice_connection(interaction)
    if not voice_client:
        return

    queue_item = None
    info = None

    if query:
        try:
            search_term = query

            spotify_regex = re.compile(r"^(https?://)?(open\.spotify\.com)/.+$")
            deezer_regex = re.compile(
                r"^(https?://)?((www\.)?deezer\.com/(?:[a-z]{2}/)?(track|playlist|album|artist)/.+|(link\.deezer\.com)/s/.+)$"
            )
            apple_music_regex = re.compile(r"^(https?://)?(music\.apple\.com)/.+$")
            tidal_regex = re.compile(r"^(https?://)?(www\.)?tidal\.com/.+$")
            amazon_music_regex = re.compile(
                r"^(https?://)?(music\.amazon\.(fr|com|co\.uk|de|es|it|jp))/.+$"
            )

            is_platform_link = (
                spotify_regex.match(query)
                or deezer_regex.match(query)
                or apple_music_regex.match(query)
                or tidal_regex.match(query)
                or amazon_music_regex.match(query)
            )

            if is_platform_link:
                tracks = None
                if spotify_regex.match(query):
                    tracks = await process_spotify_url(query, interaction)
                elif deezer_regex.match(query):
                    tracks = await process_deezer_url(query, interaction)
                elif apple_music_regex.match(query):
                    tracks = await process_apple_music_url(query, interaction)
                elif tidal_regex.match(query):
                    tracks = await process_tidal_url(query, interaction)
                elif amazon_music_regex.match(query):
                    tracks = await process_amazon_music_url(query, interaction)

                if tracks:
                    if len(tracks) > 1:
                        # Playlists are not supported for playnext, send a clear message.
                        await interaction.followup.send(
                            embed=Embed(
                                description=get_messages(
                                    "player.play_next.error.playlist_unsupported",
                                    guild_id,
                                ),
                                color=0xFF9AA2 if is_kawaii else discord.Color.red(),
                            ),
                            ephemeral=True,
                            silent=SILENT_MESSAGES,
                        )
                        return
                    track_name, artist_name = tracks[0]
                    search_term = f"{track_name} {artist_name}"

            soundcloud_regex = re.compile(r"^(https?://)?(www\.)?(soundcloud\.com)/.+$")
            direct_link_regex = re.compile(
                r"^(https?://).+\.(mp3|wav|ogg|m4a|mp4|webm|flac)(\?.+)?$",
                re.IGNORECASE,
            )

            search_query = search_term
            # FIX: Check against youtube_regex again in case it came from a platform conversion
            if not (
                youtube_regex.match(search_term)
                or soundcloud_regex.match(search_term)
                or direct_link_regex.match(search_term)
            ):
                logger.info(f"[/playnext] Processing as keyword search: {search_term}")
                search_prefix = "scsearch:" if IS_PUBLIC_VERSION else "ytsearch:"
                search_query = f"{search_prefix}{sanitize_query(search_term)}"

            info = await fetch_video_info_with_retry(
                search_query, ydl_opts_override={"noplaylist": True}
            )

            if "entries" in info and info.get("entries"):
                info = info["entries"][0]

            if not info:
                raise Exception("Could not find any video or track information.")

            queue_item = {
                "url": info.get("webpage_url", info.get("url")),
                "title": info.get("title", "Unknown Title"),
                "webpage_url": info.get("webpage_url", info.get("url")),
                "thumbnail": info.get("thumbnail"),
                "is_single": True,
                "requester": interaction.user,
            }
        except Exception as e:
            embed = Embed(
                description=get_messages("search_error", guild_id),
                color=0xFF9AA2 if is_kawaii else discord.Color.red(),
            )
            await interaction.followup.send(
                silent=SILENT_MESSAGES, embed=embed, ephemeral=True
            )
            logger.error(
                f"Error processing /playnext for query '{query}': {e}", exc_info=True
            )
            return

    # This part for handling local files remains the same
    elif file:
        if not file.content_type or not (
            file.content_type.startswith("audio/")
            or file.content_type.startswith("video/")
        ):
            embed = Embed(
                description=get_messages(
                    "player.play_files.error.invalid_type", guild_id
                ),
                color=0xFF9AA2 if is_kawaii else discord.Color.red(),
            )
            await interaction.followup.send(
                embed=embed, ephemeral=True, silent=SILENT_MESSAGES
            )
            return

        base_cache_dir = "audio_cache"
        guild_cache_dir = os.path.join(base_cache_dir, str(guild_id))
        os.makedirs(guild_cache_dir, exist_ok=True)
        file_path = os.path.join(guild_cache_dir, file.filename)

        try:
            await file.save(file_path)
            duration = get_file_duration(file_path)
            queue_item = {
                "url": file_path,
                "title": file.filename,
                "webpage_url": None,
                "thumbnail": None,
                "is_single": True,
                "source_type": "file",
                "duration": duration,
                "requester": interaction.user,
            }
        except Exception as e:
            logger.error(f"Failed to process uploaded file for /playnext: {e}")
            embed = Embed(
                description=get_messages(
                    "player.play_files.error.save_failed", guild_id
                ),
                color=0xFF9AA2 if is_kawaii else discord.Color.red(),
            )
            await interaction.followup.send(
                embed=embed, ephemeral=True, silent=SILENT_MESSAGES
            )
            return

    if queue_item:
        new_queue = asyncio.Queue()
        await new_queue.put(queue_item)
        while not music_player.queue.empty():
            item = await music_player.queue.get()
            await new_queue.put(item)
        music_player.queue = new_queue

        description_text = ""
        if queue_item.get("source_type") == "file":
            description_text = get_messages(
                "queue.now_playing_format.file", guild_id, title=queue_item["title"]
            )
        else:
            description_text = f"[{queue_item['title']}]({queue_item['webpage_url']})"

        embed = Embed(
            title=get_messages("play_next_added", guild_id),
            description=description_text,
            color=0xC7CEEA if is_kawaii else discord.Color.blue(),
        )
        if queue_item.get("thumbnail"):
            embed.set_thumbnail(url=queue_item["thumbnail"])
        if is_kawaii:
            embed.set_footer(text="☆⌒(≧▽° )")
        await interaction.followup.send(silent=SILENT_MESSAGES, embed=embed)

        bot.loop.create_task(update_controller(bot, guild_id))

        if (
            not music_player.voice_client.is_playing()
            and not music_player.voice_client.is_paused()
        ):
            music_player.current_task = asyncio.create_task(play_audio(guild_id))


@bot.tree.command(name="nowplaying", description="Show the current song playing")
async def now_playing(interaction: discord.Interaction):
    if not interaction.guild:
        await interaction.response.send_message(
            get_messages("command.error.guild_only", interaction.guild_id),
            ephemeral=True,
            silent=SILENT_MESSAGES,
        )
        return

    guild_id = interaction.guild_id
    state = get_guild_state(guild_id)
    is_kawaii = state.locale == Locale.EN_X_KAWAII
    state = get_guild_state(guild_id)
    music_player = state.music_player

    if music_player.current_info:
        title = music_player.current_info.get("title", "Unknown Title")
        thumbnail = music_player.current_info.get("thumbnail")

        description_text = ""
        if music_player.current_info.get("source_type") == "file":
            description_text = get_messages(
                "queue.now_playing_format.file", guild_id, title=title
            )
        else:
            url = music_player.current_info.get("webpage_url", music_player.current_url)
            description_text = get_messages(
                "now_playing_description", guild_id, title=title, url=url
            )

        embed = Embed(
            title=get_messages("now_playing_title", guild_id),
            description=description_text,
            color=0xC7CEEA if is_kawaii else discord.Color.green(),
        )
        if thumbnail:
            embed.set_thumbnail(url=thumbnail)

        await interaction.response.send_message(silent=SILENT_MESSAGES, embed=embed)
    else:
        embed = Embed(
            description=get_messages("no_song_playing", guild_id),
            color=0xFF9AA2 if is_kawaii else discord.Color.red(),
        )
        await interaction.response.send_message(
            silent=SILENT_MESSAGES, embed=embed, ephemeral=True
        )


@bot.tree.command(
    name="filter", description="Applies or removes audio filters in real time."
)
async def filter_command(interaction: discord.Interaction):
    if not interaction.guild:
        await interaction.response.send_message(
            get_messages("command.error.guild_only", interaction.guild_id),
            ephemeral=True,
            silent=SILENT_MESSAGES,
        )
        return

    guild_id = interaction.guild.id
    state = get_guild_state(guild_id)
    music_player = state.music_player
    state = get_guild_state(guild_id)
    is_kawaii = state.locale == Locale.EN_X_KAWAII

    if not music_player.voice_client or not (
        music_player.voice_client.is_playing() or music_player.voice_client.is_paused()
    ):
        embed = Embed(
            description=get_messages("filter.no_playback", guild_id),
            color=0xFF9AA2 if is_kawaii else discord.Color.red(),
        )
        await interaction.response.send_message(
            silent=SILENT_MESSAGES, embed=embed, ephemeral=True
        )
        return

    # Creates and sends the view with the buttons
    view = FilterView(interaction)
    embed = Embed(
        title=get_messages("filter.title", guild_id),
        description=get_messages("filter.description", guild_id),
        color=0xB5EAD7 if is_kawaii else discord.Color.blue(),
    )

    await interaction.response.send_message(
        silent=SILENT_MESSAGES, embed=embed, view=view
    )


@bot.tree.command(name="pause", description="Pause the current playback")
async def pause(interaction: discord.Interaction):
    if not interaction.guild:
        await interaction.response.send_message(
            get_messages("command.error.guild_only", interaction.guild_id),
            ephemeral=True,
            silent=SILENT_MESSAGES,
        )
        return

    # Defer the interaction immediately
    await interaction.response.defer()

    guild_id = interaction.guild_id
    state = get_guild_state(guild_id)
    is_kawaii = state.locale == Locale.EN_X_KAWAII
    state = get_guild_state(guild_id)
    music_player = state.music_player

    voice_client = await ensure_voice_connection(interaction)

    if voice_client and voice_client.is_playing():
        if music_player.playback_started_at:
            elapsed_since_play = time.time() - music_player.playback_started_at
            music_player.start_time += elapsed_since_play * music_player.playback_speed
            music_player.playback_started_at = None

        voice_client.pause()
        embed = Embed(
            description=get_messages("pause", guild_id),
            color=0xFFB7B2 if is_kawaii else discord.Color.orange(),
        )
        # Use followup.send because we deferred
        await interaction.followup.send(silent=SILENT_MESSAGES, embed=embed)
        bot.loop.create_task(update_controller(bot, interaction.guild.id))
    else:
        embed = Embed(
            description=get_messages("player.no_playback.title", guild_id),
            color=0xFF9AA2 if is_kawaii else discord.Color.red(),
        )
        # Use followup.send because we deferred
        await interaction.followup.send(
            silent=SILENT_MESSAGES, embed=embed, ephemeral=True
        )


# /resume command
@bot.tree.command(name="resume", description="Resume the playback")
async def resume(interaction: discord.Interaction):
    if not interaction.guild:
        await interaction.response.send_message(
            get_messages("command.error.guild_only", interaction.guild_id),
            ephemeral=True,
            silent=SILENT_MESSAGES,
        )
        return

    # Defer the interaction immediately
    await interaction.response.defer()

    guild_id = interaction.guild_id
    state = get_guild_state(guild_id)
    is_kawaii = state.locale == Locale.EN_X_KAWAII
    state = get_guild_state(guild_id)
    music_player = state.music_player

    voice_client = await ensure_voice_connection(interaction)

    if voice_client and voice_client.is_paused():
        if music_player.playback_started_at is None:
            music_player.playback_started_at = time.time()

        voice_client.resume()
        embed = Embed(
            description=get_messages("resume", guild_id),
            color=0xB5EAD7 if is_kawaii else discord.Color.green(),
        )
        # Use followup.send because we deferred
        await interaction.followup.send(silent=SILENT_MESSAGES, embed=embed)
        bot.loop.create_task(update_controller(bot, interaction.guild.id))
    else:
        embed = Embed(
            description=get_messages("no_paused", guild_id),
            color=0xFF9AA2 if is_kawaii else discord.Color.red(),
        )
        # Use followup.send because we deferred
        await interaction.followup.send(
            silent=SILENT_MESSAGES, embed=embed, ephemeral=True
        )


async def skip_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[int]]:
    """Provides autocomplete for the /skip command, showing song titles for track numbers."""
    guild_id = interaction.guild_id
    state = get_guild_state(guild_id)
    music_player = state.music_player
    choices = []

    # Get a snapshot of the queue to work with
    tracks = list(music_player.queue._queue)

    # We only show up to 25 choices, which is Discord's limit
    for i, track in enumerate(tracks[:25]):
        track_number = i + 1

        # Get a display-friendly title
        display_info = get_track_display_info(track)
        title = display_info.get("title", "Unknown Title")

        # The 'name' is what the user sees, the 'value' is what the bot receives.
        choice_name = f"{track_number}. {title}"

        # Filter choices based on what the user is typing in the 'number' field.
        if not current or current in str(track_number):
            # The value MUST be an integer because the command expects an integer.
            choices.append(
                app_commands.Choice(name=choice_name[:100], value=track_number)
            )

    return choices


# /skip command --- MODIFIED ---
@bot.tree.command(
    name="skip",
    description="Skips to the next song, or to a specific track number in the queue.",
)
@app_commands.describe(number="[Optional] The track number in the queue to jump to.")
@app_commands.autocomplete(number=skip_autocomplete)
async def skip(
    interaction: discord.Interaction,
    number: Optional[app_commands.Range[int, 1]] = None,
):
    """
    Skips to the next track. If a number is provided, it skips to that
    specific track in the queue, removing all preceding tracks.
    """
    if not interaction.guild:
        await interaction.response.send_message(
            get_messages("command.error.guild_only", interaction.guild_id),
            ephemeral=True,
            silent=SILENT_MESSAGES,
        )
        return

    guild_id = interaction.guild_id
    state = get_guild_state(guild_id)
    is_kawaii = state.locale == Locale.EN_X_KAWAII
    state = get_guild_state(guild_id)
    music_player = state.music_player
    voice_client = interaction.guild.voice_client

    if not voice_client or not (voice_client.is_playing() or voice_client.is_paused()):
        embed = Embed(
            description=get_messages("player.no_song.title", guild_id),
            color=0xFF9AA2 if is_kawaii else discord.Color.red(),
        )
        await interaction.response.send_message(
            embed=embed, ephemeral=True, silent=SILENT_MESSAGES
        )
        return

    # Defer the response as the action might take a moment.
    await interaction.response.defer()

    if music_player.lyrics_task and not music_player.lyrics_task.done():
        music_player.lyrics_task.cancel()

    # --- NEW LOGIC: JUMP TO A SPECIFIC SONG NUMBER ---
    if number is not None:
        async with music_player.queue_lock:
            queue_size = music_player.queue.qsize()
            if not (1 <= number <= queue_size):
                await interaction.followup.send(
                    get_messages(
                        "player.skip.error.invalid_number",
                        guild_id,
                        queue_size=queue_size,
                    ),
                    ephemeral=True,
                    silent=SILENT_MESSAGES,
                )
                return

            # Convert to 0-based index
            index_to_jump_to = number - 1

            queue_list = list(music_player.queue._queue)

            # Add the tracks that are being skipped to the history
            tracks_to_skip = queue_list[:index_to_jump_to]
            music_player.history.extend(tracks_to_skip)

            # The target song and the rest of the queue
            new_queue_list = queue_list[index_to_jump_to:]

            # Rebuild the queue
            new_queue = asyncio.Queue()
            for item in new_queue_list:
                await new_queue.put(item)
            music_player.queue = new_queue

        jumped_to_track_info = get_track_display_info(new_queue_list[0])
        title_to_announce = jumped_to_track_info.get(
            "title", get_messages("player.a_song_fallback", guild_id)
        )

        embed = Embed(
            description=get_messages(
                "player.skip.success.jumped",
                guild_id,
                number=number,
                title=title_to_announce,
            ),
            color=0xB5EAD7 if is_kawaii else discord.Color.green(),
        )
        await interaction.followup.send(embed=embed, silent=SILENT_MESSAGES)

        # Stop the current song to trigger the new one
        music_player.manual_stop = True
        await safe_stop(voice_client)
        return

    # --- ORIGINAL LOGIC: SKIP TO THE NEXT SONG ---
    if music_player.loop_current:
        # Replaying the current song
        title = music_player.current_info.get("title", "Unknown Title")
        url = music_player.current_info.get("webpage_url", music_player.current_url)
        description_text = get_messages(
            "player.replay.success_desc", guild_id, title=title, url=url
        )
        embed = Embed(
            title=get_messages("player.replay.success_title", guild_id),
            description=description_text,
            color=0xC7CEEA if is_kawaii else discord.Color.blue(),
        )
        if music_player.current_info.get("thumbnail"):
            embed.set_thumbnail(url=music_player.current_info["thumbnail"])
        await interaction.followup.send(silent=SILENT_MESSAGES, embed=embed)
        await safe_stop(voice_client)
        return

    # Announcing the next song in queue
    queue_snapshot = list(music_player.queue._queue)
    next_song_info = queue_snapshot[0] if queue_snapshot else None

    embed = None
    if next_song_info:
        # Hydrate info for a better announcement message
        hydrated_next_info = await music_player.hydrate_track_info(next_song_info)
        next_title = hydrated_next_info.get("title", "Unknown Title")

        description_text = ""
        if hydrated_next_info.get("source_type") == "file":
            description_text = get_messages(
                "queue.now_playing_format.file", guild_id, title=next_title
            )
        else:
            next_url = hydrated_next_info.get("webpage_url", "#")
            description_text = get_messages(
                "now_playing_description", guild_id, title=next_title, url=next_url
            )

        embed = Embed(
            title=get_messages("now_playing_title", guild_id),
            description=description_text,
            color=0xE2F0CB if is_kawaii else discord.Color.blue(),
        )
        embed.set_author(name=get_messages("player.skip.confirmation", guild_id))

        if hydrated_next_info.get("thumbnail"):
            embed.set_thumbnail(url=hydrated_next_info["thumbnail"])
    else:
        # Queue is now empty
        embed = Embed(
            title=get_messages("player.skip.confirmation", guild_id),
            color=0xE2F0CB if is_kawaii else discord.Color.blue(),
        )
        embed.set_footer(text=get_messages("player.skip.queue_empty", guild_id))

    await interaction.followup.send(silent=SILENT_MESSAGES, embed=embed)

    # Stop the player, the `after_playing` callback will handle the rest
    music_player.manual_stop = True  # Ensure loop/247 logic is bypassed for this skip
    await safe_stop(voice_client)


# /loop command
@bot.tree.command(name="loop", description="Enable/disable looping")
async def loop(interaction: discord.Interaction):
    if not interaction.guild:
        await interaction.response.send_message(
            get_messages("command.error.guild_only", interaction.guild_id),
            ephemeral=True,
            silent=SILENT_MESSAGES,
        )
        return

    # 1. Defer the interaction immediately
    await interaction.response.defer()

    guild_id = interaction.guild_id
    state = get_guild_state(guild_id)
    is_kawaii = state.locale == Locale.EN_X_KAWAII
    state = get_guild_state(guild_id)
    music_player = state.music_player

    music_player.loop_current = not music_player.loop_current
    state = (
        get_messages("loop_state_enabled", guild_id)
        if music_player.loop_current
        else get_messages("loop_state_disabled", guild_id)
    )

    embed = Embed(
        description=get_messages("loop", guild_id, state=state),
        color=0xC7CEEA if is_kawaii else discord.Color.blue(),
    )

    # 2. Send the actual response as a follow-up
    await interaction.followup.send(silent=SILENT_MESSAGES, embed=embed)
    bot.loop.create_task(update_controller(bot, interaction.guild.id))


# /stop command
@bot.tree.command(name="stop", description="Stop playback and disconnect the bot")
async def stop(interaction: discord.Interaction):
    if not interaction.guild:
        await interaction.response.send_message(
            get_messages("command.error.guild_only", interaction.guild_id),
            ephemeral=True,
            silent=SILENT_MESSAGES,
        )
        return

    guild_id = interaction.guild_id
    state = get_guild_state(guild_id)
    is_kawaii = state.locale == Locale.EN_X_KAWAII
    state = get_guild_state(guild_id)
    music_player = state.music_player

    if music_player.lyrics_task and not music_player.lyrics_task.done():
        music_player.lyrics_task.cancel()

    if music_player.voice_client and music_player.voice_client.is_connected():
        vc = music_player.voice_client

        # 1. We kill the FFMPEG process directly and forcefully, if it exists.
        if (
            vc.is_playing()
            and isinstance(vc.source, discord.PCMAudio)
            and hasattr(vc.source, "process")
        ):
            try:
                vc.source.process.kill()
                logger.info(
                    f"[{guild_id}] Manually killed FFMPEG process via /stop command."
                )
            except Exception as e:
                logger.error(f"[{guild_id}] Error killing FFMPEG process on /stop: {e}")

        # 2. We still call .stop() to clean up discord.py's internal state.
        if vc.is_playing():
            vc.stop()

        # 3. We cancel the main playback task if it is active.
        if music_player.current_task and not music_player.current_task.done():
            music_player.current_task.cancel()

        # 4. NOW, we can disconnect safely.
        await vc.disconnect()

        bot.loop.create_task(update_controller(bot, interaction.guild.id))

        # Final cleanup of the bot's state
        clear_audio_cache(guild_id)
        get_guild_state(guild_id).music_player = MusicPlayer()

        embed = Embed(
            description=get_messages("stop", guild_id),
            color=0xFF9AA2 if is_kawaii else discord.Color.red(),
        )
        await interaction.response.send_message(silent=SILENT_MESSAGES, embed=embed)
    else:
        embed = Embed(
            description=get_messages("player.not_connected", guild_id),
            color=0xFF9AA2 if is_kawaii else discord.Color.red(),
        )
        await interaction.response.send_message(
            silent=SILENT_MESSAGES, embed=embed, ephemeral=True
        )


# /shuffle command
@bot.tree.command(name="shuffle", description="Shuffle the current queue")
async def shuffle(interaction: discord.Interaction):
    if not interaction.guild:
        await interaction.response.send_message(
            get_messages("command.error.guild_only", interaction.guild_id),
            ephemeral=True,
            silent=SILENT_MESSAGES,
        )
        return

    guild_id = interaction.guild_id
    state = get_guild_state(guild_id)
    is_kawaii = state.locale == Locale.EN_X_KAWAII
    state = get_guild_state(guild_id)
    music_player = state.music_player

    if not music_player.queue.empty():
        items = []
        while not music_player.queue.empty():
            items.append(await music_player.queue.get())

        random.shuffle(items)

        music_player.queue = asyncio.Queue()
        for item in items:
            await music_player.queue.put(item)

        embed = Embed(
            description=get_messages("shuffle_success", guild_id),
            color=0xB5EAD7 if is_kawaii else discord.Color.green(),
        )
        await interaction.response.send_message(silent=SILENT_MESSAGES, embed=embed)
        bot.loop.create_task(update_controller(bot, interaction.guild.id))
    else:
        embed = Embed(
            description=get_messages("queue_empty", guild_id),
            color=0xFF9AA2 if is_kawaii else discord.Color.red(),
        )
        await interaction.response.send_message(
            silent=SILENT_MESSAGES, embed=embed, ephemeral=True
        )


# /autoplay command
@bot.tree.command(
    name="autoplay", description="Enable/disable autoplay of similar songs"
)
async def toggle_autoplay(interaction: discord.Interaction):
    if not interaction.guild:
        await interaction.response.send_message(
            get_messages("command.error.guild_only", interaction.guild_id),
            ephemeral=True,
            silent=SILENT_MESSAGES,
        )
        return

    guild_id = interaction.guild_id
    state = get_guild_state(guild_id)
    is_kawaii = state.locale == Locale.EN_X_KAWAII
    state = get_guild_state(guild_id)
    music_player = state.music_player

    music_player.autoplay_enabled = not music_player.autoplay_enabled
    state = (
        get_messages("autoplay_state_enabled", guild_id)
        if music_player.autoplay_enabled
        else get_messages("autoplay_state_disabled", guild_id)
    )

    embed = Embed(
        description=get_messages("autoplay_toggle", guild_id, state=state),
        color=0xC7CEEA if is_kawaii else discord.Color.blue(),
    )
    await interaction.response.send_message(silent=SILENT_MESSAGES, embed=embed)
    bot.loop.create_task(update_controller(bot, interaction.guild.id))


# /status command (hyper-complete version)
@bot.tree.command(
    name="status",
    description="Displays the bot's full performance and diagnostic stats.",
)
async def status(interaction: discord.Interaction):

    # --- Helper function to format bytes ---
    def format_bytes(size):
        if size == 0:
            return "0B"
        size_name = ("B", "KB", "MB", "GB", "TB", "PB", "EB", "ZB", "YB")
        i = int(math.floor(math.log(size, 1024)))
        p = math.pow(1024, i)
        s = round(size / p, 2)
        return f"{s} {size_name[i]}"

    await interaction.response.defer(ephemeral=True)

    # --- BOT & DISCORD METRICS ---
    bot_process = psutil.Process()
    latency = round(bot.latency * 1000)
    server_count = len(bot.guilds)
    user_count = sum(guild.member_count for guild in bot.guilds)
    current_time = time.time()
    uptime_seconds = int(round(current_time - bot.start_time))
    uptime_string = str(datetime.timedelta(seconds=uptime_seconds))

    # --- MUSIC & PLAYER METRICS ---
    active_players = len(guild_states)
    total_queued_songs = sum(
        s.music_player.queue.qsize() for s in guild_states.values()
    )
    ffmpeg_processes = 0
    try:
        children = bot_process.children(recursive=True)
        for child in children:
            if "ffmpeg" in child.name().lower():
                ffmpeg_processes += 1
    except psutil.Error:
        ffmpeg_processes = get_messages("status.not_applicable", interaction.guild_id)

    # --- HOST SYSTEM METRICS ---
    cpu_freq = psutil.cpu_freq()
    cpu_load = psutil.cpu_percent(interval=0.1)
    ram_info = psutil.virtual_memory()
    ram_total = format_bytes(ram_info.total)
    ram_used = format_bytes(ram_info.used)
    ram_percent = ram_info.percent
    bot_ram_usage = format_bytes(bot_process.memory_info().rss)
    disk_info = psutil.disk_usage("/")
    disk_total = format_bytes(disk_info.total)
    disk_used = format_bytes(disk_info.used)
    disk_percent = disk_info.percent

    # --- ENVIRONMENT & LIBRARIES ---
    python_version = (
        f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    )
    discord_py_version = discord.__version__
    yt_dlp_version = yt_dlp.version.__version__
    os_info = f"{platform.system()} {platform.release()}"

    guild_id = interaction.guild_id

    embed = discord.Embed(
        title=get_messages("status.title", guild_id),
        description=get_messages("status.description", guild_id),
        color=0x2ECC71 if latency < 200 else (0xE67E22 if latency < 500 else 0xE74C3C),
    )
    embed.set_thumbnail(url=bot.user.avatar.url)

    embed.add_field(
        name=get_messages("status.bot.title", guild_id),
        value=get_messages(
            "status.bot.value",
            guild_id,
            latency=latency,
            server_count=server_count,
            user_count=user_count,
            uptime_string=uptime_string,
        ),
        inline=True,
    )

    embed.add_field(
        name=get_messages("status.music_player.title", guild_id),
        value=get_messages(
            "status.music_player.value",
            guild_id,
            active_players=active_players,
            total_queued_songs=total_queued_songs,
            ffmpeg_processes=ffmpeg_processes,
            url_cache_size=url_cache.currsize,
            url_cache_max=url_cache.maxsize,
        ),
        inline=True,
    )

    embed.add_field(name="\u200b", value="\u200b", inline=False)

    embed.add_field(
        name=get_messages("status.host.title", guild_id),
        value=get_messages(
            "status.host.value",
            guild_id,
            os_info=os_info,
            cpu_load=cpu_load,
            cpu_freq_current=cpu_freq.current,
            ram_used=ram_used,
            ram_total=ram_total,
            ram_percent=ram_percent,
            disk_used=disk_used,
            disk_total=disk_total,
            disk_percent=disk_percent,
        ),
        inline=True,
    )

    embed.add_field(
        name=get_messages("status.environment.title", guild_id),
        value=get_messages(
            "status.environment.value",
            guild_id,
            python_version=python_version,
            discord_py_version=discord_py_version,
            yt_dlp_version=yt_dlp_version,
            bot_ram_usage=bot_ram_usage,
        ),
        inline=True,
    )

    embed.set_footer(
        text=get_messages(
            "status.footer", guild_id, user_display_name=interaction.user.display_name
        )
    )
    embed.timestamp = datetime.datetime.now(datetime.timezone.utc)

    await interaction.followup.send(silent=SILENT_MESSAGES, embed=embed)


@bot.tree.command(
    name="support", description="Shows ways to support the creator of Playify."
)
async def support(interaction: discord.Interaction):
    if not interaction.guild:
        await interaction.response.send_message(
            get_messages("support.guild_agnostic", interaction.guild_id),
            ephemeral=True,
            silent=SILENT_MESSAGES,
        )
        return

    guild_id = interaction.guild_id
    state = get_guild_state(guild_id)
    is_kawaii = state.locale == Locale.EN_X_KAWAII

    # Create the embed using messages from the dictionary
    embed = Embed(
        title=get_messages("support.title", guild_id),
        description=get_messages("support.description", guild_id),
        color=(
            0xFFC300 if not is_kawaii else 0xFFB6C1
        ),  # Gold for normal, Pink for kawaii
    )

    patreon_link = "https://patreon.com/Playify"
    paypal_link = "https://www.paypal.com/paypalme/alanmussot1"
    discord_server_link = "https://discord.gg/JeH8g6g3cG"
    discord_username = "@alananasssss"

    embed.add_field(
        name=get_messages("support.patreon_title", guild_id),
        value=get_messages("support.patreon_value", guild_id, link=patreon_link),
        inline=True,
    )
    embed.add_field(
        name=get_messages("support.paypal_title", guild_id),
        value=get_messages("support.paypal_value", guild_id, link=paypal_link),
        inline=True,
    )

    # This is a little trick to create a new line for the next inline fields
    embed.add_field(name="\u200b", value="\u200b", inline=False)

    embed.add_field(
        name=get_messages("support.discord_title", guild_id),
        value=get_messages("support.discord_value", guild_id, link=discord_server_link),
        inline=True,
    )
    embed.add_field(
        name=get_messages("support.contact_title", guild_id),
        value=get_messages(
            "support.contact_value", guild_id, username=discord_username
        ),
        inline=True,
    )

    embed.set_thumbnail(url=bot.user.avatar.url)
    embed.set_footer(text=get_messages("support.footer", guild_id))

    await interaction.response.send_message(embed=embed, silent=SILENT_MESSAGES)


@bot.tree.command(name="24_7", description="Enable or disable 24/7 mode.")
@app_commands.describe(
    mode="Choose the mode: auto (adds songs), normal (loops the queue), or off."
)
@app_commands.choices(
    mode=[
        Choice(name="Normal (Loops the current queue)", value="normal"),
        Choice(name="Auto (Adds similar songs when the queue is empty)", value="auto"),
        Choice(name="Off (Disable 24/7 mode)", value="off"),
    ]
)
async def radio_24_7(interaction: discord.Interaction, mode: str):
    if not interaction.guild:
        await interaction.response.send_message(
            get_messages("command.error.guild_only", interaction.guild_id),
            ephemeral=True,
            silent=SILENT_MESSAGES,
        )
        return

    guild_id = interaction.guild_id
    state = get_guild_state(guild_id)
    is_kawaii = state.locale == Locale.EN_X_KAWAII
    state = get_guild_state(guild_id)
    music_player = state.music_player

    await interaction.response.defer(thinking=True)

    # Case 1: The user wants to disable 24/7 mode
    if mode == "off":
        if not get_guild_state(guild_id)._24_7_mode:
            await interaction.followup.send(
                get_messages("24_7.not_active", guild_id),
                silent=SILENT_MESSAGES,
                ephemeral=True,
            )
            return

        get_guild_state(guild_id)._24_7_mode = False
        music_player.autoplay_enabled = False
        music_player.loop_current = False
        music_player.radio_playlist.clear()

        embed = Embed(
            title=get_messages("24_7.off_title", guild_id),
            description=get_messages("24_7.off_desc", guild_id),
            color=0xFF9AA2 if is_kawaii else discord.Color.red(),
        )
        await interaction.followup.send(embed=embed, silent=SILENT_MESSAGES)
        return

    voice_client = await ensure_voice_connection(interaction)
    if not voice_client:
        if music_player.text_channel:
            await music_player.text_channel.send(
                get_messages("error.connection_title", guild_id), silent=SILENT_MESSAGES
            )
        return

    if not music_player.radio_playlist:
        logger.info(
            f"[{guild_id}] 24/7 mode enabled. Creating radio playlist snapshot."
        )
        if music_player.current_info:
            music_player.radio_playlist.append(
                {
                    "url": music_player.current_url,
                    "title": music_player.current_info.get("title", "Unknown Title"),
                    "webpage_url": music_player.current_info.get(
                        "webpage_url", music_player.current_url
                    ),
                    "is_single": False,
                    "source_type": music_player.current_info.get("source_type"),
                }
            )

        queue_snapshot = list(music_player.queue._queue)
        music_player.radio_playlist.extend(queue_snapshot)

    if not music_player.radio_playlist and mode == "normal":
        await interaction.followup.send(
            get_messages("24_7.error.empty_queue_normal", guild_id),
            silent=SILENT_MESSAGES,
            ephemeral=True,
        )
        return

    get_guild_state(guild_id)._24_7_mode = True
    music_player.loop_current = False

    if mode == "auto":
        music_player.autoplay_enabled = True
        embed = Embed(
            title=get_messages("24_7.auto_title", guild_id),
            description=get_messages("24_7.auto_desc", guild_id),
            color=0xB5EAD7 if is_kawaii else discord.Color.green(),
        )
    else:  # mode == "normal"
        music_player.autoplay_enabled = False
        embed = Embed(
            title=get_messages("24_7.normal_title", guild_id),
            description=get_messages("24_7.normal_desc", guild_id),
            color=0xB5EAD7 if is_kawaii else discord.Color.green(),
        )

    if (
        not music_player.voice_client.is_playing()
        and not music_player.voice_client.is_paused()
    ):
        music_player.current_task = asyncio.create_task(play_audio(guild_id))

    await interaction.followup.send(embed=embed, silent=SILENT_MESSAGES)


@bot.tree.command(
    name="reconnect",
    description="Refreshes the voice connection to reduce lag without losing the queue.",
)
async def reconnect(interaction: discord.Interaction):
    """
    Disconnects and reconnects the bot to the voice channel,
    resuming playback at the precise timestamp. Now handles zombie states.
    """
    if not interaction.guild:
        await interaction.response.send_message(
            get_messages("command.error.guild_only", interaction.guild_id),
            ephemeral=True,
            silent=SILENT_MESSAGES,
        )
        return

    guild_id = interaction.guild_id
    state = get_guild_state(guild_id)
    is_kawaii = state.locale == Locale.EN_X_KAWAII
    state = get_guild_state(guild_id)
    music_player = state.music_player

    # --- CORRECTION PART 1: Use ensure_voice_connection to handle zombie states ---
    # This will also ensure the bot is in a channel and get the valid voice_client object
    voice_client = await ensure_voice_connection(interaction)
    if not voice_client:
        # ensure_voice_connection already sent a message if the user wasn't in a VC
        return

    # --- CORRECTION PART 2: Simplified and more robust check ---
    # We remove the `is_playing()` check. We only need to know WHAT to play,
    # not IF it's currently making sound. This is the key fix for the zombie state.
    if not music_player.current_info:
        embed = Embed(
            description=get_messages("player.reconnect.not_playing", guild_id),
            color=0xFF9AA2 if is_kawaii else discord.Color.red(),
        )
        await interaction.response.send_message(
            embed=embed, ephemeral=True, silent=SILENT_MESSAGES
        )
        return

    # If the interaction is not deferred, defer it now.
    if not interaction.response.is_done():
        await interaction.response.defer()

    current_voice_channel = voice_client.channel
    current_timestamp = 0

    # We use music_player.start_time directly if playback_started_at is None (i.e., paused)
    if music_player.playback_started_at:
        real_elapsed_time = time.time() - music_player.playback_started_at
        current_timestamp = music_player.start_time + (
            real_elapsed_time * music_player.playback_speed
        )
    else:
        current_timestamp = (
            music_player.start_time
        )  # The player was paused, use the stored time

    logger.info(
        f"[{guild_id}] Reconnect: Storing timestamp at {current_timestamp:.2f}s."
    )

    try:
        music_player.is_reconnecting = True

        if voice_client.is_playing():
            await safe_stop(voice_client)

        await voice_client.disconnect(force=True)
        await asyncio.sleep(0.75)  # A small delay to ensure clean disconnection

        # Reconnect to the same channel
        new_vc = await current_voice_channel.connect()
        music_player.voice_client = new_vc

        if isinstance(current_voice_channel, discord.StageChannel):
            logger.info(
                f"[{guild_id}] Reconnected to a Stage Channel. Promoting to speaker."
            )
            try:
                await asyncio.sleep(0.5)
                await interaction.guild.me.edit(suppress=False)
            except Exception as e:
                logger.error(
                    f"[{guild_id}] Failed to promote to speaker after reconnect: {e}"
                )

        logger.info(f"[{guild_id}] Reconnect: Restarting playback.")
        # We now reliably restart playback from the correct timestamp
        music_player.current_task = bot.loop.create_task(
            play_audio(guild_id, seek_time=current_timestamp, is_a_loop=True)
        )

        embed = Embed(
            description=get_messages("player.reconnect.success", guild_id),
            color=0xB5EAD7 if is_kawaii else discord.Color.green(),
        )
        await interaction.followup.send(embed=embed, silent=SILENT_MESSAGES)

    except Exception as e:
        logger.error(
            f"An error occurred during reconnect for guild {guild_id}: {e}",
            exc_info=True,
        )
        await interaction.followup.send(
            get_messages("player.reconnect.error.generic", guild_id),
            silent=SILENT_MESSAGES,
            ephemeral=True,
        )
    finally:
        music_player.is_reconnecting = False
        logger.info(f"[{guild_id}] Reconnect: Process finished, flag reset.")


# This is the autocomplete function. It's called by Discord as the user types.
async def song_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    guild_id = interaction.guild.id
    state = get_guild_state(guild_id)
    music_player = state.music_player
    choices = []

    # Get a snapshot of the queue to work with
    tracks = list(music_player.queue._queue)

    # Iterate through the queue and create a choice for each song
    for i, track in enumerate(tracks):
        # We only show up to 25 choices, which is Discord's limit
        if i >= 25:
            break

        title = track.get("title", "Unknown Title")

        # The 'name' is what the user sees, the 'value' is what the bot receives
        # We use the index (1-based) as the value for easy removal later.
        choice_name = f"{i + 1}. {title}"

        # Filter choices based on what the user is typing
        if current.lower() in choice_name.lower():
            choices.append(
                app_commands.Choice(name=choice_name[:100], value=str(i + 1))
            )

    return choices


@bot.tree.command(
    name="remove",
    description="Opens an interactive menu to remove songs from the queue.",
)
async def remove(interaction: discord.Interaction):
    """
    Shows an interactive, paginated, multi-select view for removing songs.
    """

    if not interaction.guild:
        await interaction.response.send_message(
            get_messages("command.error.guild_only", interaction.guild_id),
            ephemeral=True,
            silent=SILENT_MESSAGES,
        )
        return

    guild_id = interaction.guild_id
    state = get_guild_state(guild_id)
    is_kawaii = state.locale == Locale.EN_X_KAWAII
    state = get_guild_state(guild_id)
    music_player = state.music_player

    if music_player.queue.empty():
        embed = Embed(
            description=get_messages("queue_empty", guild_id),
            color=0xFF9AA2 if is_kawaii else discord.Color.red(),
        )
        await interaction.response.send_message(
            embed=embed, ephemeral=True, silent=SILENT_MESSAGES
        )
        return

    await interaction.response.defer()

    all_tracks = list(music_player.queue._queue)
    view = RemoveView(interaction, all_tracks)
    await view.update_view()

    embed = Embed(
        title=get_messages("remove_title", guild_id),
        description=get_messages("remove_description", guild_id),
        color=0xC7CEEA if is_kawaii else discord.Color.blue(),
    )

    await interaction.followup.send(embed=embed, view=view, silent=SILENT_MESSAGES)


# --- START OF NEW CODE BLOCK ---
@bot.tree.command(
    name="search",
    description="Searches for a song and lets you choose from the top results.",
)
@app_commands.describe(query="The name of the song to search for.")
async def search(interaction: discord.Interaction, query: str):
    if not interaction.guild:
        await interaction.response.send_message(
            get_messages("command.error.guild_only", interaction.guild.id),
            ephemeral=True,
            silent=SILENT_MESSAGES,
        )
        return

    await interaction.response.defer()

    guild_id = interaction.guild_id
    state = get_guild_state(guild_id)
    is_kawaii = state.locale == Locale.EN_X_KAWAII

    voice_client = await ensure_voice_connection(interaction)
    if not voice_client:
        return

    try:
        platform_name = "SoundCloud" if IS_PUBLIC_VERSION else "YouTube"
        logger.info(
            f"[{guild_id}] Executing /search for: '{query}' via {platform_name}"
        )

        sanitized_query = sanitize_query(query)
        search_prefix = "scsearch5:" if IS_PUBLIC_VERSION else "ytsearch5:"
        search_query = f"{search_prefix}{sanitized_query}"

        info = await fetch_video_info_with_retry(
            search_query, ydl_opts_override={"extract_flat": True, "noplaylist": True}
        )

        search_results = info.get("entries", [])

        if not search_results:
            embed = Embed(
                description=get_messages("search.no_results", guild_id, query=query),
                color=0xFF9AA2 if is_kawaii else discord.Color.red(),
            )
            await interaction.followup.send(
                embed=embed, silent=SILENT_MESSAGES, ephemeral=True
            )
            return

        view = SearchView(search_results, guild_id)
        embed = Embed(
            title=get_messages("search.results_title", guild_id),
            description=get_messages("search.results_description", guild_id),
            color=0xC7CEEA if is_kawaii else discord.Color.blue(),
        )

        await interaction.followup.send(embed=embed, view=view, silent=SILENT_MESSAGES)

    except Exception as e:
        logger.error(f"Error during /search for '{query}': {e}", exc_info=True)
        await handle_playback_error(guild_id, e)


@bot.tree.command(
    name="seek",
    description="Opens an interactive menu to seek, fast-forward, or rewind.",
)
async def seek_interactive(interaction: discord.Interaction):
    guild_id = interaction.guild.id
    state = get_guild_state(guild_id)
    music_player = state.music_player

    if not music_player.voice_client or not (
        music_player.voice_client.is_playing() or music_player.voice_client.is_paused()
    ):
        await interaction.response.send_message(
            get_messages("player.no_playback.title", guild_id),
            ephemeral=True,
            silent=SILENT_MESSAGES,
        )
        return

    if music_player.is_current_live:
        await interaction.response.send_message(
            get_messages("seek.fail_live", guild_id),
            ephemeral=True,
            silent=SILENT_MESSAGES,
        )
        return

    # Create the view and the initial embed
    view = SeekView(interaction)

    # Create the initial embed (will be updated by the view)
    initial_embed = Embed(
        title=get_messages("seek.interface_title", guild_id),
        description=get_messages("seek.interface.loading_description", guild_id),
        color=(
            0xB5EAD7
            if (get_guild_state(guild_id).locale == Locale.EN_X_KAWAII)
            else discord.Color.blue()
        ),
    )

    await interaction.response.send_message(
        embed=initial_embed, view=view, silent=SILENT_MESSAGES
    )

    # Update the view with the message and start the background task
    view.message = await interaction.original_response()
    await view.update_embed()  # First manual update
    await view.start_update_task()


@bot.tree.command(
    name="volume", description="Adjusts the music volume for everyone (0-200%)."
)
@app_commands.describe(
    level="The new volume level as a percentage (e.g., 50, 100, 150)."
)
@app_commands.default_permissions(manage_channels=True)
async def volume(
    interaction: discord.Interaction, level: app_commands.Range[int, 0, 200]
):
    """
    Changes the music player's volume in real-time with no cutoff.
    The `manage_channels` permission is a good proxy for moderators.
    """
    if not interaction.guild:
        await interaction.response.send_message(
            get_messages("command.error.guild_only", interaction.guild_id),
            ephemeral=True,
            silent=SILENT_MESSAGES,
        )
        return

    guild_id = interaction.guild.id
    state = get_guild_state(guild_id)
    music_player = state.music_player
    vc = interaction.guild.voice_client

    new_volume = level / 100.0
    music_player.volume = new_volume

    if vc and vc.is_playing() and isinstance(vc.source, discord.PCMVolumeTransformer):
        vc.source.volume = new_volume

    embed = Embed(
        description=get_messages("volume_success", guild_id, level=level),
        color=(
            0xB5EAD7
            if (get_guild_state(guild_id).locale == Locale.EN_X_KAWAII)
            else discord.Color.blue()
        ),
    )

    await interaction.response.send_message(embed=embed, silent=SILENT_MESSAGES)
    bot.loop.create_task(update_controller(bot, interaction.guild.id))


@app_commands.default_permissions(administrator=True)
class SetupCommands(app_commands.Group):
    """Commands for setting up the bot on the server."""

    def __init__(self, bot: commands.Bot):
        super().__init__(
            name="setup",
            description="Set up bot features for the server.",
            default_permissions=discord.Permissions(administrator=True),
        )
        self.bot = bot

    @app_commands.command(
        name="controller",
        description="Sets a channel for the persistent music controller.",
    )
    @app_commands.describe(
        channel="The text channel for the controller. Defaults to the current channel if not specified."
    )
    async def controller(
        self,
        interaction: discord.Interaction,
        channel: Optional[discord.TextChannel] = None,
    ):
        """Sets or updates the channel for the music controller."""
        if not interaction.guild:
            await interaction.response.send_message(
                get_messages("command.error.guild_only", interaction.guild.id),
                ephemeral=True,
                silent=SILENT_MESSAGES,
            )
            return

        target_channel = channel or interaction.channel
        guild_id = interaction.guild.id

        if get_guild_state(guild_id).controller_message_id:
            try:
                old_channel_id = get_guild_state(guild_id).controller_channel_id
                if old_channel_id:
                    old_channel = self.bot.get_channel(old_channel_id)
                    if old_channel:
                        old_message = await old_channel.fetch_message(
                            get_guild_state(guild_id).controller_message_id
                        )
                        await old_message.delete()
                        logger.info(
                            f"Deleted old controller message in guild {guild_id}"
                        )
            except (discord.NotFound, discord.Forbidden):
                pass

        get_guild_state(guild_id).controller_channel_id = target_channel.id
        get_guild_state(guild_id).controller_message_id = None

        await interaction.response.send_message(
            get_messages(
                "setup.controller.success",
                guild_id,
                channel_mention=target_channel.mention,
            ),
            ephemeral=True,
            silent=SILENT_MESSAGES,
        )
        await update_controller(self.bot, guild_id)

    @app_commands.command(
        name="allowlist", description="Restricts bot commands to specific channels."
    )
    @app_commands.describe(
        reset="Type 'default' to allow commands in all channels again.",
        channel1="The first channel to allow.",
        channel2="An optional second channel to allow.",
        channel3="An optional third channel to allow.",
        channel4="An optional fourth channel to allow.",
        channel5="An optional fifth channel to allow.",
    )
    async def allowlist(
        self,
        interaction: discord.Interaction,
        reset: Optional[str] = None,
        channel1: Optional[discord.TextChannel] = None,
        channel2: Optional[discord.TextChannel] = None,
        channel3: Optional[discord.TextChannel] = None,
        channel4: Optional[discord.TextChannel] = None,
        channel5: Optional[discord.TextChannel] = None,
    ):

        guild_id = interaction.guild.id
        state = get_guild_state(guild_id)
        is_kawaii = state.locale == Locale.EN_X_KAWAII

        # Case 1: Reset the allowlist
        if reset and reset.lower() == "default":
            state = get_guild_state(guild_id)
            if state.allowed_channels:
                state.allowed_channels.clear()
                logger.info(
                    f"Command channel allowlist has been RESET for guild {guild_id}."
                )

            embed = discord.Embed(
                description=get_messages("setup.allowlist.reset_success", guild_id),
                color=0xB5EAD7 if is_kawaii else discord.Color.green(),
            )
            await interaction.response.send_message(
                embed=embed, ephemeral=True, silent=True
            )
            return

        # Case 2: Set the allowlist
        channels = [
            ch
            for ch in [channel1, channel2, channel3, channel4, channel5]
            if ch is not None
        ]

        if channels:
            allowed_ids = {ch.id for ch in channels}
            get_guild_state(guild_id).allowed_channels = allowed_ids

            channel_mentions = ", ".join([ch.mention for ch in channels])
            logger.info(
                f"Command channel allowlist for guild {guild_id} set to: {allowed_ids}"
            )

            embed = discord.Embed(
                description=get_messages(
                    "setup.allowlist.set_success", guild_id, channels=channel_mentions
                ),
                color=0xB5EAD7 if is_kawaii else discord.Color.green(),
            )
            await interaction.response.send_message(
                embed=embed, ephemeral=True, silent=True
            )
            return

        # Case 3: Invalid arguments
        embed = discord.Embed(
            description=get_messages("setup.allowlist.invalid_args", guild_id),
            color=0xFF9AA2 if is_kawaii else discord.Color.orange(),
        )
        await interaction.response.send_message(
            embed=embed, ephemeral=True, silent=True
        )


@bot.tree.command(
    name="previous", description="Plays the previous song in the history."
)
async def previous(interaction: discord.Interaction):
    guild_id = interaction.guild.id
    state = get_guild_state(guild_id)
    music_player = state.music_player
    vc = interaction.guild.voice_client  # Use the guild's voice_client directly

    if not vc or not (vc.is_playing() or vc.is_paused()):
        await interaction.response.send_message(
            get_messages("player.no_playback.title", guild_id),
            ephemeral=True,
            silent=SILENT_MESSAGES,
        )
        return

    # History contains the current song, so we need at least 2 for a "previous" one
    if len(music_player.history) < 2:
        await interaction.response.send_message(
            get_messages("player.history.empty", guild_id),
            ephemeral=True,
            silent=SILENT_MESSAGES,
        )
        return

    # Defer now that we know we can proceed
    await interaction.response.defer(ephemeral=True)

    # Add the current song back to the top of the queue
    current_song = music_player.history.pop()
    previous_song = music_player.history.pop()

    # Rebuild the queue
    new_queue = asyncio.Queue()
    await new_queue.put(previous_song)
    await new_queue.put(current_song)

    old_queue_list = list(music_player.queue._queue)
    for item in old_queue_list:
        await new_queue.put(item)

    music_player.queue = new_queue

    # Stop current song to trigger the next one.
    # The after_playing -> play_audio chain will handle the controller update.
    await safe_stop(vc)

    # Send a simple confirmation to the user
    await interaction.followup.send(
        get_messages("player.previous.success", guild_id), silent=SILENT_MESSAGES
    )


@bot.tree.command(
    name="jumpto", description="Opens a menu to jump to a specific song in the queue."
)
async def jumpto(interaction: discord.Interaction):
    """
    Shows an interactive, paginated view for jumping to a specific song.
    """
    if not interaction.guild:
        await interaction.response.send_message(
            get_messages("command.error.guild_only", interaction.guild_id),
            ephemeral=True,
            silent=SILENT_MESSAGES,
        )
        return

    guild_id = interaction.guild_id
    state = get_guild_state(guild_id)
    is_kawaii = state.locale == Locale.EN_X_KAWAII
    state = get_guild_state(guild_id)
    music_player = state.music_player

    if music_player.queue.empty():
        embed = Embed(
            description=get_messages("queue_empty", guild_id),
            color=0xFF9AA2 if is_kawaii else discord.Color.red(),
        )
        await interaction.response.send_message(
            embed=embed, ephemeral=True, silent=SILENT_MESSAGES
        )
        return

    await interaction.response.defer()

    all_tracks = list(music_player.queue._queue)
    view = JumpToView(interaction, all_tracks)
    await view.update_view()

    embed = Embed(
        title=get_messages("jumpto.title", guild_id),
        description=get_messages("jumpto.description", guild_id),
        color=0xC7CEEA if is_kawaii else discord.Color.blue(),
    )

    await interaction.followup.send(embed=embed, view=view, silent=SILENT_MESSAGES)


# ==============================================================================
# 6. DISCORD EVENTS
# ==============================================================================

bot.tree.add_command(SetupCommands(bot))


@bot.event
async def on_message(message: discord.Message):
    # Ignore messages from the bot itself to prevent loops
    if message.author == bot.user:
        return

    # Ignore messages outside of guilds (DMs)
    if not message.guild:
        return

    guild_id = message.guild.id

    # This event no longer handles controller re-anchoring.
    # That logic is now in `play_audio` to trigger only on song changes.
    pass


@bot.event
async def on_voice_state_update(member, before, after):
    """
    Final, hyper-robust voice state manager.
    It relies on direct process management and a strict STOP/KILL/RESTART cycle
    to guarantee playback resumption for both regular tracks and live streams,
    and to prevent FFMPEG process leaks.
    """
    guild = member.guild
    vc = guild.voice_client

    if not vc or not vc.channel:
        return

    music_player = get_player(guild.id)
    guild_id = guild.id

    # --- BOT DISCONNECTION LOGIC (Critical Cleanup) ---
    if member.id == bot.user.id and after.channel is None:
        if music_player.is_reconnecting or music_player.is_cleaning:
            return

        if music_player.silence_task and not music_player.silence_task.done():
            music_player.silence_task.cancel()

        if get_guild_state(guild_id)._24_7_mode:
            logger.warning(
                f"Bot was disconnected from guild {guild_id}, but 24/7 mode is active. Preserving player state."
            )
            music_player.voice_client = None
            if music_player.current_task and not music_player.current_task.done():
                music_player.current_task.cancel()
            return

        logger.info(
            f"Bot was disconnected from guild {guild_id}. Triggering full cleanup."
        )
        clear_audio_cache(guild_id)
        if music_player.current_task and not music_player.current_task.done():
            music_player.current_task.cancel()

        state = get_guild_state(guild.id)
        state.music_player = MusicPlayer()
        state.server_filters.clear()
        state._24_7_mode = False
        logger.info(f"Player for guild {guild.id} has been fully reset.")
        return

    # --- HUMAN LEAVES / JOINS LOGIC ---
    bot_channel = vc.channel

    is_leaving_event = (
        not member.bot
        and before.channel == bot_channel
        and after.channel != bot_channel
    )
    if is_leaving_event:
        # After the user leaves, check if the bot is now alone.
        if not [m for m in bot_channel.members if not m.bot]:
            logger.info(f"Bot is now alone in guild {guild_id}.")

            # If music is playing, we STOP it. This is the crucial change.
            if vc.is_playing() and not music_player.is_playing_silence:
                music_player.is_paused_by_leave = True
                if music_player.playback_started_at:
                    elapsed = time.time() - music_player.playback_started_at
                    music_player.start_time += elapsed * music_player.playback_speed
                    music_player.playback_started_at = None

                # We no longer rely on the after_playing callback for this.
                if isinstance(vc.source, discord.PCMAudio) and hasattr(
                    vc.source, "process"
                ):
                    try:
                        vc.source.process.kill()
                        logger.info(
                            f"[{guild_id}] Manually killed FFMPEG process for music due to empty channel."
                        )
                    except Exception as e:
                        logger.error(
                            f"[{guild_id}] Error killing FFMPEG process on leave: {e}"
                        )

                # We still call stop() to clean up discord.py's internal state.
                vc.stop()

            if get_guild_state(guild_id)._24_7_mode:
                if not music_player.silence_task or music_player.silence_task.done():
                    music_player.silence_task = bot.loop.create_task(
                        play_silence_loop(guild_id)
                    )
            else:
                await asyncio.sleep(60)
                if vc.is_connected() and not [
                    m for m in vc.channel.members if not m.bot
                ]:
                    await vc.disconnect()

    is_joining_event = (
        not member.bot
        and after.channel == bot_channel
        and before.channel != bot_channel
    )
    if is_joining_event:
        # Check if the person who joined is the *first* human back.
        if len([m for m in bot_channel.members if not m.bot]) == 1:
            logger.info(
                f"[{guild_id}] First user joined. Resuming playback procedures."
            )

            music_player.is_paused_by_leave = False
            was_playing_silence = (
                music_player.silence_task and not music_player.silence_task.done()
            )

            if music_player.current_info:
                if was_playing_silence:
                    music_player.silence_task.cancel()
                    music_player.is_resuming_after_silence = True
                    if vc.is_playing():
                        vc.stop()  # This will be cleaned by its own 'finally' or our callback
                    await asyncio.sleep(0.1)

                current_timestamp = music_player.start_time

                if music_player.is_current_live:
                    logger.info(
                        f"Resuming a live stream for guild {guild_id}. Triggering resync."
                    )
                    music_player.is_resuming_live = True
                    bot.loop.create_task(play_audio(guild_id, is_a_loop=True))
                else:
                    logger.info(
                        f"Resuming track '{music_player.current_info.get('title')}' at {current_timestamp:.2f}s."
                    )
                    bot.loop.create_task(
                        play_audio(
                            guild_id, seek_time=current_timestamp, is_a_loop=True
                        )
                    )


async def global_interaction_check(interaction: discord.Interaction) -> bool:
    """
    Final global check for slash commands.
    Properly handles autocomplete interactions.
    """
    # If it's an autocomplete request, always allow it.
    # The actual check will be performed during command submission.
    if interaction.type == discord.InteractionType.autocomplete:
        return True

    # For all other interactions (command submission, buttons, etc.),
    # apply our security logic.
    if not interaction.guild:
        return True

    guild_id = interaction.guild.id
    allowed_ids = get_guild_state(guild_id).allowed_channels

    if not allowed_ids:
        return True

    if interaction.user.guild_permissions.manage_guild:
        return True

    if interaction.channel_id in allowed_ids:
        return True

    # Final block if no condition is met
    state = get_guild_state(guild_id)
    is_kawaii = state.locale == Locale.EN_X_KAWAII
    channel_mentions = ", ".join([f"<#{ch_id}>" for ch_id in allowed_ids])
    description_text = get_messages(
        "command.restricted_description",
        guild_id,
        bot_name=interaction.client.user.name,
    )

    embed = discord.Embed(
        title=get_messages("command.restricted_title", guild_id),
        description=description_text,
        color=0xFF9AA2 if is_kawaii else discord.Color.red(),
    )
    embed.add_field(
        name=get_messages("command.allowed_channels_field", guild_id),
        value=channel_mentions,
    )

    await interaction.response.send_message(embed=embed, ephemeral=True, silent=True)
    return False


@bot.event
async def on_ready():
    logger.info(f"{bot.user.name} is online.")
    try:
        bot.tree.interaction_check = global_interaction_check
        logger.info("Global interaction check has been manually assigned.")

        for guild in bot.guilds:
            bot.add_view(MusicControllerView(bot, guild.id))
        logger.info("Re-registered persistent MusicControllerView for all guilds.")

        synced = await bot.tree.sync()
        logger.info(f"Synced {len(synced)} slash commands.")

        async def rotate_presence():
            await bot.wait_until_ready()

            while not bot.is_closed():
                if bot.guilds:
                    guild_id = bot.guilds[0].id

                    statuses = [
                        (
                            get_messages("presence.listening_volume", guild_id),
                            discord.ActivityType.listening,
                        ),
                        (
                            get_messages("presence.listening_play", guild_id),
                            discord.ActivityType.listening,
                        ),
                        (
                            get_messages(
                                "presence.playing_servers",
                                guild_id,
                                count=len(bot.guilds),
                            ),
                            discord.ActivityType.playing,
                        ),
                    ]

                    for status_text, status_type in statuses:
                        try:
                            await bot.change_presence(
                                activity=discord.Activity(
                                    name=status_text, type=status_type
                                )
                            )
                            await asyncio.sleep(10)
                        except Exception as e:
                            logger.error(f"Error changing presence: {e}")
                            await asyncio.sleep(5)
                else:
                    await asyncio.sleep(30)

        bot.loop.create_task(rotate_presence())

        await load_states_on_startup()

    except Exception as e:
        logger.error(f"Error during command synchronization: {e}")


# ==============================================================================
# 7. BOT INITIALIZATION & RUN
# ==============================================================================

if __name__ == "__main__":
    init_db()
    bot.start_time = time.time()
    bot.run(os.getenv("DISCORD_TOKEN"))
