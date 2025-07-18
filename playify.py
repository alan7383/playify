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
from urllib.parse import urlparse, parse_qs
from cachetools import TTLCache
import logging
import requests
from playwright.async_api import async_playwright
import json
import time
import syncedlyrics
import lyricsgenius
import psutil
import time
import datetime
import platform
import sys
import math # Needed for the format_bytes helper
import traceback # To format exceptions
import os
from dotenv import load_dotenv
load_dotenv()

SILENT_MESSAGES = True

# --- Logging ---

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- API Tokens & Clients ---

GENIUS_TOKEN = os.getenv("GENIUS_TOKEN")

if GENIUS_TOKEN and GENIUS_TOKEN != "YOUR_GENIUS_TOKEN_HERE":
    genius = lyricsgenius.Genius(GENIUS_TOKEN, verbose=False, remove_section_headers=True)
    logger.info("LyricsGenius client initialized.")
else:
    genius = None
    logger.warning("GENIUS_TOKEN is not set in the code. /lyrics and fallback will not work.")

# Official API Client (fast and prioritized)

SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
try:
    sp = spotipy.Spotify(auth_manager=SpotifyClientCredentials(
        client_id=SPOTIFY_CLIENT_ID,
        client_secret=SPOTIFY_CLIENT_SECRET
    ))
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

# --- Bot Configuration Dictionaries ---

# Dictionary of available audio filters and their FFmpeg options
AUDIO_FILTERS = {
    "slowed": "asetrate=44100*0.8",
    "spedup": "asetrate=44100*1.2",
    "nightcore": "asetrate=44100*1.25,atempo=1.0",
    "reverb": "aecho=0.8:0.9:40|50|60:0.4|0.3|0.2",
    "8d": "apulsator=hz=0.08",
    "muffled": "lowpass=f=500",
    "bassboost": "bass=g=10", # Boost bass by 10 dB
    "earrape": "acrusher=level_in=8:level_out=18:bits=8:mode=log:aa=1" # Ear rape effect
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
    "earrape": "Earrape"
}

messages = {
    # --- Error reporting messages ---
    "critical_error_title": {
        "normal": "🚨 An Unexpected Error Occurred",
        "kawaii": "(╥﹏╥) Oh no! A critical error happened..."
    },
    "critical_error_description": {
        "normal": "The bot encountered a problem. Please report this issue on GitHub so we can fix it!",
        "kawaii": "Something went wrong... (´；ω；`) Can you please tell the developers on GitHub so they can make me better?"
    },
    "critical_error_report_field": {
        "normal": "Report on GitHub",
        "kawaii": "Report the boo-boo! 🩹"
    },
    "critical_error_report_value": {
        "normal": "You can create an issue here:\n**https://github.com/alan7383/playify/issues**\n\nPlease include the error details below.",
        "kawaii": "Please tell them what happened here:\n**https://github.com/alan7383/playify/issues**\n\nDon't forget to send the little error message below!~"
    },
    "critical_error_details_field": {
        "normal": "Error Details",
        "kawaii": "Error info (for the smart people!)"
    },
    # --- NEW --- /reconnect command messages
    "reconnect_success": {
        "normal": "🤖 Reconnection successful! Hopefully, things are smoother now.",
        "kawaii": "🤖 Reconnected! Let's hope it's better now~ (ﾉ◕ヮ◕)ﾉ*:･ﾟ✧"
    },
    "reconnect_fail": {
        "normal": "Oh no, an error occurred during reconnection.",
        "kawaii": "Aww, something went wrong with the reconnection... (´-ω-`)"
    },
    # --- NEW --- /queue status messages
    "queue_status_title": {
        "normal": "Current Status",
        "kawaii": "Status! (o･ω･)ﾉ"
    },
    "queue_status_none": {
        "normal": "No special modes active.",
        "kawaii": "Just chillin' normally~"
    },
    "queue_status_loop": {
        "normal": "🔁 **Loop (Song)**: Enabled",
        "kawaii": "🔁 **Loop (Song)**: On! 💖"
    },
    "queue_status_24_7": {
        "normal": "📻 **24/7 ({mode})**: Enabled",
        "kawaii": "📻 **24/7 ({mode})**: Let's go! ✨"
    },
    "queue_status_autoplay": {
        "normal": "➡️ **Autoplay**: Enabled",
        "kawaii": "➡️ **Autoplay**: On! ♫"
    },
    "now_playing_in_queue": {
        "normal": "▶️ Now Playing",
        "kawaii": "▶️ Now Playing!~"
    },
    # ... (rest of your messages dictionary)
    "no_voice_channel": {
        "normal": "You must be in a voice channel to use this command.",
        "kawaii": "(>ω<) You must be in a voice channel!"
    },
    "connection_error": {
        "normal": "Error connecting to the voice channel.",
        "kawaii": "(╥﹏╥) I couldn't connect..."
    },
    "spotify_error": {
        "normal": "Error processing the Spotify link. It may be private, region-locked, or invalid.",
        "kawaii": "(´；ω；`) Oh no! Problem with the Spotify link... maybe it’s shy or hidden?"
    },
    "spotify_playlist_added": {
        "normal": "🎶 Spotify Playlist Added",
        "kawaii": "☆*:.｡.o(≧▽≦)o.｡.:*☆ SPOTIFY PLAYLIST"
    },
    "spotify_playlist_description": {
        "normal": "**{count} tracks** added, {failed} failed.\n{failed_tracks}",
        "kawaii": "**{count} songs** added, {failed} couldn’t join! (´･ω･`)\n{failed_tracks}"
    },
    "deezer_error": {
        "normal": "Error processing the Deezer link. It may be private, region-locked, or invalid.",
        "kawaii": "(´；ω；`) Oh no! Problem with the Deezer link... maybe it’s shy or hidden?"
    },
    "deezer_playlist_added": {
        "normal": "🎶 Deezer Playlist Added",
        "kawaii": "☆*:.｡.o(≧▽≦)o.｡.:*☆ DEEZER PLAYLIST"
    },
    "deezer_playlist_description": {
        "normal": "**{count} tracks** added, {failed} failed.\n{failed_tracks}",
        "kawaii": "**{count} songs** added, {failed} couldn’t join! (´･ω･`)\n{failed_tracks}"
    },
    "apple_music_error": {
        "normal": "Error processing the Apple Music link.",
        "kawaii": "(´；ω；`) Oops! Trouble with the Apple Music link..."
    },
    "apple_music_playlist_added": {
        "normal": "🎶 Apple Music Playlist Added",
        "kawaii": "☆*:.｡.o(≧▽≦)o.｡.:*☆ APPLE MUSIC PLAYLIST"
    },
    "apple_music_playlist_description": {
        "normal": "**{count} tracks** added, {failed} failed.\n{failed_tracks}",
        "kawaii": "**{count} songs** added, {failed} couldn't join! (´･ω･`)\n{failed_tracks}"
    },
    "tidal_error": {
        "normal": "Error processing the Tidal link. It may be private, region-locked, or invalid.",
        "kawaii": "(´；ω；`) Oh no! Problem with the Tidal link... maybe it’s shy or hidden?"
    },
    "tidal_playlist_added": {
        "normal": "🎶 Tidal Playlist Added",
        "kawaii": "☆*:.｡.o(≧▽≦)o.｡.:*☆ TIDAL PLAYLIST"
    },
    "tidal_playlist_description": {
        "normal": "**{count} tracks** added, {failed} failed.\n{failed_tracks}",
        "kawaii": "**{count} songs** added, {failed} couldn’t join! (´･ω･`)\n{failed_tracks}"
    },
    "amazon_music_error": {
        "normal": "Error processing the Amazon Music link.",
        "kawaii": "(´；ω；`) Oh no! Something is wrong with the Amazon Music link..."
    },
    "amazon_music_playlist_added": {
        "normal": "🎶 Amazon Music Playlist Added",
        "kawaii": "☆*:.｡.o(≧▽≦)o.｡.:*☆ AMAZON MUSIC PLAYLIST"
    },
    "amazon_music_playlist_description": {
        "normal": "**{count} tracks** added, {failed} failed.\n{failed_tracks}",
        "kawaii": "**{count} songs** added, {failed} couldn't join! (´･ω･`)\n{failed_tracks}"
    },
    "song_added": {
        "normal": "🎵 Added to Queue",
        "kawaii": "(っ◕‿◕)っ ♫ SONG ADDED ♫"
    },
    "playlist_added": {
        "normal": "🎶 Playlist Added",
        "kawaii": "✧･ﾟ: *✧･ﾟ:* PLAYLIST *:･ﾟ✧*:･ﾟ✧"
    },
    "playlist_description": {
        "normal": "**{count} tracks** added to the queue.",
        "kawaii": "**{count} songs** added!"
    },
    "ytmusic_playlist_added": {
        "normal": "🎶 YouTube Music Playlist Added",
        "kawaii": "☆*:.｡.o(≧▽≦)o.｡.:*☆ YOUTUBE MUSIC PLAYLIST"
    },
    "ytmusic_playlist_description": {
        "normal": "**{count} tracks** being added...",
        "kawaii": "**{count} songs** added!"
    },
    "video_error": {
        "normal": "Error adding the video or playlist.",
        "kawaii": "(´；ω；`) Something went wrong with this video..."
    },
    "search_error": {
        "normal": "Error during search. Try another title.",
        "kawaii": "(︶︹︺) Couldn't find this song..."
    },
    "now_playing_title": {
        "normal": "🎵 Now Playing",
        "kawaii": "♫♬ NOW PLAYING ♬♫"
    },
    "now_playing_description": {
        "normal": "[{title}]({url})",
        "kawaii": "♪(´▽｀) [{title}]({url})"
    },
    "pause": {
        "normal": "⏸️ Playback paused.",
        "kawaii": "(´･_･`) Music paused..."
    },
    "no_playback": {
        "normal": "No playback in progress.",
        "kawaii": "(・_・;) Nothing is playing right now..."
    },
    "resume": {
        "normal": "▶️ Playback resumed.",
        "kawaii": "☆*:.｡.o(≧▽≦)o.｡.:*☆ Let's go again!"
    },
    "no_paused": {
        "normal": "No playback is paused.",
        "kawaii": "(´･ω･`) No music is paused..."
    },
    "skip": {
        "normal": "⏭️ Current song skipped.",
        "kawaii": "(ノ°ο°)ノ Skipped! Next song ~"
    },
    "no_song": {
        "normal": "No song is playing.",
        "kawaii": "(；一_一) Nothing to skip..."
    },
    "loop": {
        "normal": "🔁 Looping for the current song {state}.",
        "kawaii": "🔁 Looping for the current song {state}."
    },
    "loop_state_enabled": {
        "normal": "enabled",
        "kawaii": "enabled (◕‿◕✿)"
    },
    "loop_state_disabled": {
        "normal": "disabled",
        "kawaii": "disabled (¨_°`)"
    },
    "stop": {
        "normal": "⏹️ Playback stopped and bot disconnected.",
        "kawaii": "(ﾉ´･ω･)ﾉ ﾐ ┸━┸ All stopped! Bye bye ~"
    },
    "not_connected": {
        "normal": "The bot is not connected to a voice channel.",
        "kawaii": "(￣ω￣;) I'm not connected..."
    },
    "kawaii_toggle": {
        "normal": "Kawaii mode {state} for this server!",
        "kawaii": "Kawaii mode {state} for this server!"
    },
    "kawaii_state_enabled": {
        "normal": "enabled",
        "kawaii": "enabled (◕‿◕✿)"
    },
    "kawaii_state_disabled": {
        "normal": "disabled",
        "kawaii": "disabled"
    },
    "shuffle_success": {
        "normal": "🔀 Queue shuffled successfully!",
        "kawaii": "(✿◕‿◕) Queue shuffled! Yay! ~"
    },
    "queue_empty": {
        "normal": "The queue is empty.",
        "kawaii": "(´･ω･`) No songs in the queue..."
    },
    "autoplay_toggle": {
        "normal": "Autoplay {state}.",
        "kawaii": "♫ Autoplay {state} (◕‿◕✿)"
    },
    "autoplay_state_enabled": {
        "normal": "enabled",
        "kawaii": "enabled"
    },
    "autoplay_state_disabled": {
        "normal": "disabled",
        "kawaii": "disabled"
    },
    "autoplay_added": {
        "normal": "🎵 Adding similar songs to the queue... (This may take up to 1 minute)",
        "kawaii": "♪(´▽｀) Adding similar songs to the queue! ~ (It might take a little while!)"
    },
    "queue_title": {
        "normal": "🎶 Queue",
        "kawaii": "🎶 Queue (◕‿◕✿)"
    },
    "queue_description": {
        "normal": "There are **{count} songs** in the queue.",
        "kawaii": "**{count} songs** in the queue! ~"
    },
    "queue_next": {
        "normal": "Next Up:",
        "kawaii": "Next Up: ♫"
    },
    "queue_song": {
        "normal": "- [{title}]({url})",
        "kawaii": "- ♪ [{title}]({url})"
    },
    "clear_queue_success": {
        "normal": "✅ Queue cleared.",
        "kawaii": "(≧▽≦) Queue cleared! ~"
    },
    "play_next_added": {
        "normal": "🎵 Added as next song",
        "kawaii": "(っ◕‿◕)っ ♫ Added as next song ♫"
    },
    "no_song_playing": {
        "normal": "No song is currently playing.",
        "kawaii": "(´･ω･`) No music is playing right now..."
    },
    "loading_playlist": {
        "normal": "Processing playlist...\n{processed}/{total} tracks added",
        "kawaii": "(✿◕‿◕) Processing playlist...\n{processed}/{total} songs added"
    },
    "playlist_error": {
        "normal": "Error processing the playlist. It may be private, region-locked, or invalid.",
        "kawaii": "(´；ω；`) Oh no! Problem with the playlist... maybe it’s shy or hidden?"
    },
    "filter_title": {
        "normal": "🎧 Audio Filters",
        "kawaii": "🎧 Filters! ヾ(≧▽≦*)o"
    },
    "filter_description": {
        "normal": "Click on the buttons to enable or disable a filter in real time!",
        "kawaii": "Clicky clicky to change the sound! ~☆"
    },
    "no_filter_playback": {
        "normal": "Nothing is currently playing to apply a filter on.",
        "kawaii": "Nothing is playing... (´・ω・`)"
    },
    "lyrics_fallback_warning": {
        "normal": "Synced lyrics not found. Displaying standard lyrics instead.",
        "kawaii": "I couldn't find the synced lyrics... (｡•́︿•̀｡) But here are the normal ones for u!"
    },
    "karaoke_disclaimer": {
        "normal": "Please note: The timing of the arrow (») and lyric accuracy are matched automatically and can vary based on the song version or active filters.",
        "kawaii": "Just so you know! ପ(๑•ᴗ•๑)ଓ ♡ The arrow (») and lyrics do their best to sync up! But with different song versions or fun filters, they might not be perfectly on time~"
    },
    "karaoke_warning_title": {
        "normal": "🎤 Karaoke - Important Notice",
        "kawaii": "🎤 Karaoke Time! Just a little note~ (´• ω •`)"
    },
    "karaoke_warning_description": {
        "normal": "Please note that the timing of the lyrics (») is matched automatically and can vary.\n\n**💡 Pro Tip:** For the best results, try adding `topic` or `audio` to your search (e.g., `party addict kets4eki topic`).\n\nPress **Continue** to start.",
        "kawaii": "The timing of the lyrics (») does its best to be perfect, but sometimes it's a little shy! ପ(๑•ᴗ•๑)ଓ ♡\n\n**💡 Pro Tip:** For the bestest results, try adding `topic` or `audio` to your search, like `party addict kets4eki topic`!\n\nSmash that **Continue** button to begin~ 💖"
    },
    "karaoke_warning_button": {
        "normal": "Continue",
        "kawaii": "Continue (ﾉ◕ヮ◕)ﾉ*:･ﾟ✧"
    },
    "lyrics_not_found_title": {
        "normal": "😢 Lyrics Not Found",
        "kawaii": "૮( ´• ˕ •` )ა Lyrics not found..."
    },
    "lyrics_not_found_description": {
        "normal": "I couldn't find lyrics for **{query}**.\n\nYou can refine the search yourself. Try using just the song title.",
        "kawaii": "I searched everywhere but I couldn't find the lyrics for **{query}** (｡•́︿•̀｡)\n\nTry searching just with the title, you can do it!~"
    },
    "lyrics_refine_button": {
        "normal": "Refine Search",
        "kawaii": "Try again! (o･ω･)ﾉ"
    },
    "karaoke_not_found_title": {
        "normal": "😢 Synced Lyrics Not Found",
        "kawaii": "૮( ´• ˕ •` )ა Synced Lyrics Not Found..."
    },
    "karaoke_not_found_description": {
        "normal": "I couldn't find synced lyrics for **{query}**.\n\nYou can refine the search or search for standard (non-synced) lyrics on Genius.",
        "kawaii": "I looked everywhere but couldn't find the synced lyrics for **{query}** (｡•́︿•̀｡)\n\nYou can try again, or we can look for the normal lyrics on Genius together!~"
    },
    "karaoke_retry_button": {
        "normal": "Refine Search",
        "kawaii": "Try Again! (o･ω･)ﾉ"
    },
    "karaoke_genius_fallback_button": {
        "normal": "Search on Genius",
        "kawaii": "Find on Genius 📜"
    },
    "karaoke_retry_success": {
        "normal": "Lyrics found! Starting karaoke...",
        "kawaii": "Yay, I found them! Starting karaoke~ 🎤"
    },
    "karaoke_retry_fail": {
        "normal": "Sorry, I still couldn't find synced lyrics for **{query}**.",
        "kawaii": "Aww, still no luck finding the synced lyrics for **{query}**... (´-ω-`)"
    },
        "extraction_error": {
        "normal": "⚠️ Could Not Add Track",
        "kawaii": "(ﾉ><)ﾉ I couldn't add that one!"
    },
    "extraction_error_reason": {
        "normal": "Reason: {error_message}",
        "kawaii": "Here's why: {error_message} (´• ω •`)"
    },
        "error_title_age_restricted": {
        "normal": "Age-Restricted Video",
        "kawaii": "Video for Grown-ups! (⁄ ⁄>⁄ ᗨ ⁄<⁄ ⁄)"
    },
    "error_desc_age_restricted": {
        "normal": "This video requires sign-in to confirm the user's age and cannot be played by the bot.",
        "kawaii": "This video is for big kids only! I'm not old enough to watch it... (>_<)"
    },
    "error_title_private": {
        "normal": "Private Video",
        "kawaii": "Secret Video! (・-・)"
    },
    "error_desc_private": {
        "normal": "This video is marked as private and cannot be accessed.",
        "kawaii": "This video is a super secret! I'm not on the guest list... ( T_T)"
    },
    "error_title_unavailable": {
        "normal": "Video Unavailable",
        "kawaii": "Video went poof! (o.o)"
    },
    "error_desc_unavailable": {
        "normal": "This video is no longer available or may have been removed.",
        "kawaii": "Poof! This video has disappeared... I can't find it anywhere!"
    },
    "error_title_generic": {
        "normal": "Access Denied",
        "kawaii": "Access Denied! (壁)"
    },
    "error_desc_generic": {
        "normal": "The bot was blocked from accessing this video. This can happen with certain live streams or premieres.",
        "kawaii": "A big wall is blocking me from this video! I can't get through..."
    },
    "error_field_full_error": {
        "normal": "Full Error for Bug Report",
        "kawaii": "The techy stuff for the devs!"
    },
        "error_field_what_to_do": {
        "normal": "What to do?",
        "kawaii": "What can we do? (・_・?)"
    },
    "error_what_to_do_content": {
        "normal": "Some videos have restrictions that prevent bots from playing them.\n\nIf you believe this is a different bug, please [open an issue on GitHub]({github_link}).",
        "kawaii": "Some videos have super strong shields that stop me! ( >д<)\n\nIf you think something is really, really broken, you can [tell the super smart developers here]({github_link})!~"
    },
    "discord_command_title": {
        "normal": "🔗 Join Our Discord!",
        "kawaii": "Come hang out with us!"
    },
    "discord_command_description": {
        "normal": "Click the button below to join the official Playify support and community server.",
        "kawaii": "Join our super cute community! Just click the button below~ (ﾉ◕ヮ◕)ﾉ*:･ﾟ✧"
    },
    "discord_command_button": {
        "normal": "Join Server",
        "kawaii": "Join Us! ♡"
    },
    "24_7_on_title": {
        "normal": "📻 24/7 Radio ON",
        "kawaii": "📻 24/7 Radio ON ✧"
    },
    "24_7_on_desc": {
        "normal": "Queue will loop indefinitely – bot stays & auto-resumes when you re-join.",
        "kawaii": "(ﾉ◕ヮ◕)ﾉ*:･ﾟ✧ Radio forever! Bot never sleeps, just pauses when alone~"
    },
    "24_7_off_title": {
        "normal": "📴 24/7 Radio OFF",
        "kawaii": "📴 24/7 Radio OFF (；一_一)"
    },
    "24_7_off_desc": {
        "normal": "Queue cleared – bot will disconnect after 60 s if left alone.",
        "kawaii": "Bye-bye radio! Queue wiped, bot will nap soon~"
    },
        "24_7_auto_title": {
        "normal": "🔄 24/7 Auto Mode",
        "kawaii": "🔄 24/7 Auto Mode ✨"
    },
    "24_7_auto_desc": {
        "normal": "Autoplay enabled - will add similar songs when playlist ends!",
        "kawaii": "Autoplay on! New similar songs will appear magically~ ✨"
    },
    "24_7_normal_title": {
        "normal": "🔁 24/7 Loop Mode",
        "kawaii": "🔁 24/7 Loop Mode ♾️"
    },
    "24_7_normal_desc": {
        "normal": "Playlist will loop indefinitely without adding new songs.",
        "kawaii": "Playlist looping forever~ No new songs added! ♾️"
    },
    "24_7_invalid_mode": {
        "normal": "Invalid mode! Use `/24_7 auto` or `/24_7 normal`",
        "kawaii": "Oops! Use `/24_7 auto` or `/24_7 normal` (◕‿◕)"
    },
    "queue_page_footer": {
        "normal": "Page {current_page}/{total_pages}",
        "kawaii": "Page {current_page}/{total_pages}  (ﾉ◕ヮ◕)ﾉ*:･ﾟ✧"
    },
    "previous_button": {
        "normal": "⬅️ Previous",
        "kawaii": "⬅️ Back"
    },
    "next_button": {
        "normal": "Next ➡️",
        "kawaii": "Next! ➡️"
    },
}

# --- Discord Bot Initialization ---

# Intents for the bot
intents = discord.Intents.default()
intents.guilds = True
intents.voice_states = True

# Create the bot
bot = commands.Bot(command_prefix="!", intents=intents)

# ==============================================================================
# 2. CORE CLASSES & STATE MANAGEMENT
# ==============================================================================

# --- Global State Variables ---

# Server states
music_players = {}  # {guild_id: MusicPlayer()}
kawaii_mode = {}    # {guild_id: bool}
server_filters = {} # {guild_id: set("filter1", "filter2")}
karaoke_disclaimer_shown = set()
_24_7_active = {}  # {guild_id: bool}
reconnecting_guilds = set()

# --- Core Music Player Class ---

class MusicPlayer:
    def __init__(self):
        self.voice_client = None
        self.current_task = None
        self.queue = asyncio.Queue()
        self.history = []
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

# --- Discord UI Classes (Views & Modals) ---

# --- Discord UI Classes (Views & Modals) ---

class QueueView(View):
    """
    A View that handles the pagination for the /queue command.
    It's designed to be fast, operating on a pre-fetched list of tracks.
    """
    def __init__(self, interaction: discord.Interaction, tracks: list, items_per_page: int = 5):
        super().__init__(timeout=300.0) # The view will stop working after 5 minutes of inactivity
        self.interaction = interaction
        self.guild_id = interaction.guild_id
        self.music_player = get_player(self.guild_id)
        self.is_kawaii = get_mode(self.guild_id)

        self.tracks = tracks
        self.items_per_page = items_per_page
        self.current_page = 0
        # Calculate the total number of pages needed
        self.total_pages = math.ceil(len(self.tracks) / self.items_per_page)
        
        # Set initial button labels from the messages dictionary
        self.previous_button.label = get_messages("previous_button", self.guild_id)
        self.next_button.label = get_messages("next_button", self.guild_id)

    async def create_queue_embed(self) -> Embed:
        """Creates the embed for the current page of the queue."""
        
        # Build the status description
        status_lines = []
        if self.music_player.loop_current:
            status_lines.append(get_messages("queue_status_loop", self.guild_id))
        
        if _24_7_active.get(self.guild_id, False):
            mode_24_7 = "Auto" if self.music_player.autoplay_enabled else "Normal"
            status_lines.append(get_messages("queue_status_24_7", self.guild_id).format(mode=mode_24_7))
        elif self.music_player.autoplay_enabled:
            status_lines.append(get_messages("queue_status_autoplay", self.guild_id))
        
        status_description = "\n".join(status_lines) if status_lines else get_messages("queue_status_none", self.guild_id)

        # Create the main embed
        embed = Embed(
            title=get_messages("queue_title", self.guild_id),
            description=get_messages("queue_description", self.guild_id).format(count=len(self.tracks)),
            color=0xB5EAD7 if self.is_kawaii else discord.Color.blue()
        )

        # Add the status field
        embed.add_field(name=get_messages("queue_status_title", self.guild_id), value=status_description, inline=False)

        # Add the "Now Playing" field
        if self.music_player.current_info:
            title = self.music_player.current_info.get("title", "Unknown Title")
            url = self.music_player.current_info.get("webpage_url", self.music_player.current_url)
            embed.add_field(name=get_messages("now_playing_in_queue", self.guild_id), value=f"[{title}]({url})", inline=False)

        # Add the paginated "Next Up" field
        if self.tracks:
            start_index = self.current_page * self.items_per_page
            end_index = start_index + self.items_per_page
            tracks_on_page = self.tracks[start_index:end_index]

            next_songs_list = []
            for i, item in enumerate(tracks_on_page, start=start_index):
                title = item.get('title', 'Title not available')
                url = item.get('webpage_url', '#')
                # Add the track number to the line
                next_songs_list.append(f"`{i + 1}.` {get_messages('queue_song', self.guild_id).format(title=title, url=url)}")

            if next_songs_list:
                embed.add_field(name=get_messages("queue_next", self.guild_id), value="\n".join(next_songs_list), inline=False)
        
        # Set the footer to show the current page
        embed.set_footer(text=get_messages("queue_page_footer", self.guild_id).format(current_page=self.current_page + 1, total_pages=self.total_pages))
        
        return embed

    def update_button_states(self):
        """Disables or enables buttons based on the current page."""
        self.previous_button.disabled = self.current_page == 0
        self.next_button.disabled = self.current_page >= self.total_pages - 1

    @discord.ui.button(style=discord.ButtonStyle.grey)
    async def previous_button(self, interaction: discord.Interaction, button: Button):
        if self.current_page > 0:
            self.current_page -= 1
        
        self.update_button_states()
        new_embed = await self.create_queue_embed()
        await interaction.response.edit_message(embed=new_embed, view=self)

    @discord.ui.button(style=discord.ButtonStyle.grey)
    async def next_button(self, interaction: discord.Interaction, button: Button):
        if self.current_page < self.total_pages - 1:
            self.current_page += 1

        self.update_button_states()
        new_embed = await self.create_queue_embed()
        await interaction.response.edit_message(embed=new_embed, view=self)

class LyricsView(View):
    def __init__(self, pages: list, original_embed: Embed):
        super().__init__(timeout=300.0)
        self.pages = pages
        self.original_embed = original_embed
        self.current_page = 0

    def update_embed(self):
        self.original_embed.description = self.pages[self.current_page]
        self.original_embed.set_footer(text=f"Page {self.current_page + 1}/{len(self.pages)}")
        return self.original_embed

    @discord.ui.button(label="⬅️ Previous", style=discord.ButtonStyle.grey, row=0)
    async def previous_button(self, interaction: discord.Interaction, button: Button):
        if self.current_page > 0:
            self.current_page -= 1

        self.previous_button.disabled = self.current_page == 0
        self.next_button.disabled = False

        await interaction.response.edit_message(embed=self.update_embed(), view=self)

    @discord.ui.button(label="Next ➡️", style=discord.ButtonStyle.grey, row=0)
    async def next_button(self, interaction: discord.Interaction, button: Button):
        if self.current_page < len(self.pages) - 1:
            self.current_page += 1

        self.next_button.disabled = self.current_page == len(self.pages) - 1
        self.previous_button.disabled = False

        await interaction.response.edit_message(embed=self.update_embed(), view=self)

    @discord.ui.button(label="Refine", emoji="✏️", style=discord.ButtonStyle.secondary, row=0)
    async def refine_button(self, interaction: discord.Interaction, button: Button):
        modal = RefineLyricsModal(message_to_edit=interaction.message)
        await interaction.response.send_modal(modal)

class LyricsRetryModal(discord.ui.Modal, title="Refine Lyrics Search"):
    def __init__(self, original_interaction: discord.Interaction, suggested_query: str):
        super().__init__()
        self.original_interaction = original_interaction
        self.suggested_query = suggested_query
        self.guild_id = original_interaction.guild_id

        self.corrected_query = discord.ui.TextInput(
            label="Song Title & Artist",
            placeholder="e.g., Believer Imagine Dragons",
            default=self.suggested_query,
            style=discord.TextStyle.short
        )
        self.add_item(self.corrected_query)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True, ephemeral=True)

        new_query = self.corrected_query.value
        logger.info(f"Retrying lyrics search with new query: '{new_query}'")

        try:
            loop = asyncio.get_running_loop()
            if not genius:
                await interaction.followup.send("Genius API is not configured.", silent=SILENT_MESSAGES, ephemeral=True)
                return

            song = await loop.run_in_executor(None, lambda: genius.search_song(new_query))

            if not song:
                fail_message = get_messages("lyrics_not_found_description", self.guild_id).format(query=new_query)
                await interaction.followup.send(fail_message.split('\n')[0], silent=SILENT_MESSAGES, ephemeral=True)
                return

            raw_lyrics = song.lyrics
            lines = raw_lyrics.split('\n')
            cleaned_lines = [line for line in lines if "contributor" not in line.lower() and "lyrics" not in line.lower() and "embed" not in line.lower()]
            lyrics = "\n".join(cleaned_lines).strip()

            pages = []
            current_page_content = ""
            for line in lyrics.split('\n'):
                if len(current_page_content) + len(line) + 1 > 1500:
                    pages.append(f"```{current_page_content.strip()}```")
                    current_page_content = ""
                current_page_content += line + "\n"
            if current_page_content.strip():
                pages.append(f"```{current_page_content.strip()}```")

            base_embed = Embed(title=f"📜 Lyrics for {song.title}", url=song.url, color=discord.Color.green())

            view = LyricsView(pages=pages, original_embed=base_embed)
            initial_embed = view.update_embed()

            view.children[0].disabled = True
            if len(pages) <= 1:
                view.children[1].disabled = True

            message = await self.original_interaction.followup.send(silent=SILENT_MESSAGES,embed=initial_embed, view=view, wait=True)

            view.message = message

            await interaction.followup.send("Lyrics found!", silent=SILENT_MESSAGES, ephemeral=True)

        except Exception as e:
            logger.error(f"Error during lyrics retry: {e}")
            await interaction.followup.send("An error occurred during the new search.", silent=SILENT_MESSAGES, ephemeral=True)

class LyricsRetryView(discord.ui.View):
    # We add guild_id to the initialization
    def __init__(self, original_interaction: discord.Interaction, suggested_query: str, guild_id: int):
        super().__init__(timeout=180.0)
        self.original_interaction = original_interaction
        self.suggested_query = suggested_query

        # We get the correct label for the button
        button_label = get_messages("lyrics_refine_button", guild_id)

        # We access the button (created by the decorator) and change its label
        self.retry_button.label = button_label

    # The decorator no longer needs the label; it is defined dynamically
    @discord.ui.button(style=discord.ButtonStyle.primary)
    async def retry_button(self, interaction: discord.Interaction, button: Button):
        modal = LyricsRetryModal(
            original_interaction=self.original_interaction,
            suggested_query=self.suggested_query
        )
        await interaction.response.send_modal(modal)

class KaraokeRetryModal(discord.ui.Modal, title="Refine Karaoke Search"):
    def __init__(self, original_interaction: discord.Interaction, suggested_query: str):
        super().__init__()
        self.original_interaction = original_interaction
        self.suggested_query = suggested_query
        self.guild_id = original_interaction.guild_id
        self.music_player = get_player(self.guild_id)
        self.is_kawaii = get_mode(self.guild_id)

        self.corrected_query = discord.ui.TextInput(
            label="Song Title & Artist",
            placeholder="e.g., Believer Imagine Dragons",
            default=self.suggested_query,
            style=discord.TextStyle.short
        )
        self.add_item(self.corrected_query)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True, ephemeral=True)
        new_query = self.corrected_query.value
        logger.info(f"Retrying synced lyrics search with new query: '{new_query}'")

        loop = asyncio.get_running_loop()
        lrc = None
        try:
            lrc = await asyncio.wait_for(
                loop.run_in_executor(None, syncedlyrics.search, new_query),
                timeout=10.0
            )
        except (asyncio.TimeoutError, Exception) as e:
            logger.error(f"Error during karaoke retry search: {e}")

        if not lrc:
            fail_message = get_messages("karaoke_retry_fail", self.guild_id).format(query=new_query)
            await interaction.followup.send(fail_message, silent=SILENT_MESSAGES, ephemeral=True)
            return

        lyrics_lines = [{'time': int(m.group(1))*60000 + int(m.group(2))*1000 + int(m.group(3)), 'text': m.group(4).strip()} for line in lrc.splitlines() if (m := re.match(r'\[(\d{2}):(\d{2})\.(\d{2,3})\](.*)', line))]

        if not lyrics_lines:
            fail_message = get_messages("karaoke_retry_fail", self.guild_id).format(query=new_query)
            await interaction.followup.send(fail_message, silent=SILENT_MESSAGES, ephemeral=True)
            return

        # Success! Start the karaoke.
        self.music_player.synced_lyrics = lyrics_lines

        clean_title, _ = get_cleaned_song_info(self.music_player.current_info)
        embed = Embed(
            title=f"🎤 Karaoke for {clean_title}",
            description="Starting karaoke...",
            color=0xC7CEEA if self.is_kawaii else discord.Color.blue()
        )

        # We use the original interaction's followup to send the main message
        lyrics_message = await self.original_interaction.followup.send(silent=SILENT_MESSAGES,embed=embed, wait=True)
        self.music_player.lyrics_message = lyrics_message
        self.music_player.lyrics_task = asyncio.create_task(update_karaoke_task(self.guild_id))

        # Notify the user who clicked the button that it worked
        success_message = get_messages("karaoke_retry_success", self.guild_id)
        await interaction.followup.send(success_message, silent=SILENT_MESSAGES, ephemeral=True)

class RefineLyricsModal(discord.ui.Modal, title="Refine Lyrics Search"):
    def __init__(self, message_to_edit: discord.Message):
        super().__init__()
        self.message_to_edit = message_to_edit
        self.guild_id = message_to_edit.guild.id
        self.is_kawaii = get_mode(self.guild_id)

        self.corrected_query = discord.ui.TextInput(
            label="New Song Title & Artist",
            placeholder="e.g., Blinding Lights The Weeknd",
            style=discord.TextStyle.short
        )
        self.add_item(self.corrected_query)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True, ephemeral=True)

        new_query = self.corrected_query.value
        logger.info(f"Refining lyrics search with new query: '{new_query}'")

        if not genius:
            await interaction.followup.send("Genius API is not configured.", silent=SILENT_MESSAGES, ephemeral=True)
            return

        try:
            loop = asyncio.get_running_loop()
            song = await loop.run_in_executor(None, lambda: genius.search_song(new_query))

            if not song:
                await interaction.followup.send(f"Sorry, I still couldn't find lyrics for **{new_query}**.", silent=SILENT_MESSAGES, ephemeral=True)
                return

            raw_lyrics = song.lyrics
            lines = raw_lyrics.split('\n')
            cleaned_lines = [line for line in lines if "contributor" not in line.lower() and "lyrics" not in line.lower() and "embed" not in line.lower()]
            lyrics = "\n".join(cleaned_lines).strip()

            pages = []
            current_page_content = ""
            for line in lyrics.split('\n'):
                if len(current_page_content) + len(line) + 1 > 1500:
                    pages.append(f"```{current_page_content.strip()}```")
                    current_page_content = ""
                current_page_content += line + "\n"
            if current_page_content.strip():
                pages.append(f"```{current_page_content.strip()}```")

            new_embed = Embed(
                title=f"📜 Lyrics for {song.title}",
                url=song.url,
                color=0xB5EAD7 if self.is_kawaii else discord.Color.green()
            )

            new_view = LyricsView(pages=pages, original_embed=new_embed)

            final_embed = new_view.update_embed()
            new_view.children[0].disabled = True
            if len(pages) <= 1:
                new_view.children[1].disabled = True

            await self.message_to_edit.edit(embed=final_embed, view=new_view)


            await interaction.followup.send("Lyrics updated successfully!", silent=SILENT_MESSAGES, ephemeral=True)

        except Exception as e:
            logger.error(f"Error during lyrics refinement: {e}", exc_info=True)
            await interaction.followup.send("An error occurred during the new search.", silent=SILENT_MESSAGES, ephemeral=True)

class KaraokeRetryView(discord.ui.View):
    def __init__(self, original_interaction: discord.Interaction, suggested_query: str, guild_id: int):
        super().__init__(timeout=180.0)
        self.original_interaction = original_interaction
        self.suggested_query = suggested_query
        self.guild_id = guild_id

        # Set button labels from messages
        self.retry_button.label = get_messages("karaoke_retry_button", self.guild_id)
        self.genius_fallback_button.label = get_messages("karaoke_genius_fallback_button", self.guild_id)

    @discord.ui.button(style=discord.ButtonStyle.primary)
    async def retry_button(self, interaction: discord.Interaction, button: Button):
        modal = KaraokeRetryModal(
            original_interaction=self.original_interaction,
            suggested_query=self.suggested_query
        )
        await interaction.response.send_modal(modal)

    @discord.ui.button(style=discord.ButtonStyle.secondary)
    async def genius_fallback_button(self, interaction: discord.Interaction, button: Button):
        # Disable buttons to show action is taken
        for child in self.children:
            child.disabled = True
        await self.original_interaction.edit_original_response(view=self)

        # Acknowledge the button click before starting the search
        await interaction.response.defer()

        # Fetch standard lyrics
        fallback_msg = get_messages("lyrics_fallback_warning", self.guild_id)
        await fetch_and_display_genius_lyrics(self.original_interaction, fallback_message=fallback_msg)

class KaraokeWarningView(View):
    def __init__(self, interaction: discord.Interaction, karaoke_coro):
        super().__init__(timeout=180.0)
        self.interaction = interaction
        self.karaoke_coro = karaoke_coro # The coroutine to execute after the click

    @discord.ui.button(label="Continue", style=discord.ButtonStyle.success)
    async def continue_button(self, interaction: discord.Interaction, button: Button):
        # We check that it's the original user who is clicking
        if interaction.user.id != self.interaction.user.id:
            await interaction.response.send_message("Only the person who ran the command can do this!", silent=SILENT_MESSAGES, ephemeral=True)
            return

        # We add the server to the list of "warned" guilds
        guild_id = interaction.guild_id
        karaoke_disclaimer_shown.add(guild_id)
        logger.info(f"Karaoke disclaimer acknowledged for guild {guild_id}.")

        # We disable the button and update the message
        button.disabled = True
        button.label = "Acknowledged!"
        await interaction.response.edit_message(view=self)

        # We start the actual karaoke logic
        await self.karaoke_coro()

# View for the filter buttons
class FilterView(View):
    def __init__(self, interaction: discord.Interaction):
        super().__init__(timeout=None)
        self.guild_id = interaction.guild.id
        self.interaction = interaction
        server_filters.setdefault(self.guild_id, set())
        for effect, display_name in FILTER_DISPLAY_NAMES.items():
            is_active = effect in server_filters[self.guild_id]
            style = ButtonStyle.success if is_active else ButtonStyle.secondary
            button = Button(label=display_name, custom_id=f"filter_{effect}", style=style)
            button.callback = self.button_callback
            self.add_item(button)

    async def button_callback(self, interaction: discord.Interaction):
        effect = interaction.data['custom_id'].split('_')[1]
        active_guild_filters = server_filters[self.guild_id]

        # Enable or disable the filter
        if effect in active_guild_filters:
            active_guild_filters.remove(effect)
        else:
            active_guild_filters.add(effect)

        # Update the appearance of the buttons
        for child in self.children:
            if isinstance(child, Button):
                child_effect = child.custom_id.split('_')[1]
                child.style = ButtonStyle.success if child_effect in active_guild_filters else ButtonStyle.secondary

        await interaction.response.edit_message(view=self)

        music_player = get_player(self.guild_id)
        if music_player.voice_client and (music_player.voice_client.is_playing() or music_player.voice_client.is_paused()):

            # --- START OF CORRECTION ---

            # 1. We save the CURRENT playback speed (before the change)
            old_speed = music_player.playback_speed

            # 2. We calculate the real time elapsed since playback started
            elapsed_time = 0
            if music_player.playback_started_at:
                real_elapsed_time = time.time() - music_player.playback_started_at
                # 3. We calculate the position IN the music using the OLD speed
                elapsed_time = (real_elapsed_time * old_speed) + music_player.start_time

            # 4. We update the player's speed with the NEW speed for the next playback
            music_player.playback_speed = get_speed_multiplier_from_filters(active_guild_filters)

            # --- END OF CORRECTION ---

            # We indicate that we are changing the filter to restart playback at the correct position
            music_player.is_seeking = True
            music_player.seek_info = elapsed_time
            music_player.voice_client.stop()

# ==============================================================================
# 3. UTILITY & HELPER FUNCTIONS
# ==============================================================================

# --- General & State Helpers ---

# Get player for a server
def get_player(guild_id):
    if guild_id not in music_players:
        music_players[guild_id] = MusicPlayer()
    return music_players[guild_id]

# Get active filter for a server
def get_filter(guild_id):
    return server_filters.get(guild_id)

# Get kawaii mode
def get_mode(guild_id):
    return kawaii_mode.get(guild_id, False)

def get_messages(message_key, guild_id):
    is_kawaii = get_mode(guild_id)
    mode = "kawaii" if is_kawaii else "normal"
    return messages[message_key][mode]

# --- Text, Formatting & Lyrics Helpers ---

def get_cleaned_song_info(music_info: dict) -> tuple[str, str]:
    """Aggressively cleans the title and artist to optimize the search."""

    title = music_info.get("title", "Unknown Title")
    artist = music_info.get("uploader", "Unknown Artist")

    # --- 1. Cleaning the artist name ---
    ARTIST_NOISE = ['xoxo', 'official', 'beats', 'prod', 'music', 'records', 'tv', 'lyrics', 'archive', '- Topic']
    clean_artist = artist
    for noise in ARTIST_NOISE:
        clean_artist = re.sub(r'(?i)' + re.escape(noise), '', clean_artist).strip()

    # --- 2. Cleaning the song title ---
    patterns_to_remove = [
        r'\[.*?\]',              # Removes content in brackets, e.g., [MV]
        r'\(.*?\)',              # Removes content in parentheses, e.g., (Official Video)
        r'\s*feat\..*',          # Removes "feat." and the rest
        r'\s*ft\..*',            # Removes "ft." and the rest
        r'\s*w/.*',              # Removes "w/" (with) and the rest
        r'(?i)official video',   # Removes "official video" (case-insensitive)
        r'(?i)lyric video',      # Removes "lyric video" (case-insensitive)
        r'(?i)audio',            # Removes "audio" (case-insensitive)
        r'(?i)hd',               # Removes "hd" (case-insensitive)
        r'4K',                   # Removes "4K"
        r'\+',                   # Removes "+" symbols
    ]

    clean_title = title
    for pattern in patterns_to_remove:
        clean_title = re.sub(pattern, '', clean_title)

    # Tries to remove the artist name from the title to keep only the song name
    if clean_artist:
        clean_title = clean_title.replace(clean_artist, '')
    clean_title = clean_title.replace(artist, '').strip(' -')

    # If the title is empty after cleaning, start over from the original title without parentheses/brackets
    if not clean_title:
        clean_title = re.sub(r'\[.*?\]|\(.*?\)', '', title).strip()

    logger.info(f"Cleaned info: Title='{clean_title}', Artist='{clean_artist}'")
    return clean_title, clean_artist

def get_speed_multiplier_from_filters(active_filters: set) -> float:
    """Calculates the speed multiplier from the active filters."""
    speed = 1.0
    pitch_speed = 1.0 # Speed from asetrate (nightcore/slowed)
    tempo_speed = 1.0 # Speed from atempo

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

async def fetch_and_display_genius_lyrics(interaction: discord.Interaction, fallback_message: str = None):
    """Fetches, formats, and displays lyrics with smart pagination buttons."""
    guild_id = interaction.guild_id
    music_player = get_player(guild_id)
    is_kawaii = get_mode(guild_id)
    loop = asyncio.get_running_loop()

    if not genius:
        return await interaction.followup.send("Genius API is not configured.", silent=SILENT_MESSAGES, ephemeral=True)

    clean_title, artist_name = get_cleaned_song_info(music_player.current_info)
    precise_query = f"{clean_title} {artist_name}"

    try:
        # Attempt 1: Asynchronous precise search
        logger.info(f"Attempting precise Genius search: '{precise_query}'")
        song = await asyncio.wait_for(
            loop.run_in_executor(None, lambda: genius.search_song(precise_query)),
            timeout=10.0
        )

        # Attempt 2: If the first one fails
        if not song:
            logger.info(f"Precise Genius search failed, trying broad search: '{clean_title}'")
            song = await asyncio.wait_for(
                loop.run_in_executor(None, lambda: genius.search_song(clean_title)),
                timeout=10.0
            )

        if not song:
            # We retrieve the texts from the `messages` dictionary
            error_title = get_messages("lyrics_not_found_title", guild_id)
            error_desc = get_messages("lyrics_not_found_description", guild_id).format(query=precise_query)

            error_embed = Embed(
                title=error_title,
                description=error_desc,
                color=0xFF9AA2 if get_mode(guild_id) else discord.Color.red()
            )

            # We pass the guild_id to the view so it can choose the correct text for the button
            view = LyricsRetryView(
                original_interaction=interaction,
                suggested_query=clean_title,
                guild_id=guild_id
            )
            await interaction.followup.send(silent=SILENT_MESSAGES,embed=error_embed, view=view)
            return

        # --- The rest of the logic (fetching lyrics, pagination) ---
        raw_lyrics = song.lyrics
        lines = raw_lyrics.split('\n')

        cleaned_lines = []
        for line in lines:
            if "contributor" in line.lower() or "lyrics" in line.lower() or "embed" in line.lower():
                continue
            cleaned_lines.append(line)

        lyrics = "\n".join(cleaned_lines).strip()

        pages = []
        current_page_content = ""
        max_page_length = 1500

        for line in lyrics.split('\n'):
            if len(current_page_content) + len(line) + 1 > max_page_length:
                pages.append(f"```{current_page_content.strip()}```")
                current_page_content = ""
            current_page_content += line + "\n"

        if current_page_content.strip():
            pages.append(f"```{current_page_content.strip()}```")

        if not pages:
            return await interaction.followup.send("Could not format the lyrics.", silent=SILENT_MESSAGES, ephemeral=True)

        base_embed = Embed(
            title=f"📜 Lyrics for {song.title}",
            color=0xB5EAD7 if is_kawaii else discord.Color.green(),
            url=song.url
        )
        if fallback_message:
            base_embed.set_author(name=fallback_message)

        view = LyricsView(pages=pages, original_embed=base_embed)
        initial_embed = view.update_embed()

        view.children[0].disabled = True
        if len(pages) <= 1:
            view.children[1].disabled = True

        message = await interaction.followup.send(silent=SILENT_MESSAGES,embed=initial_embed, view=view, wait=True)

        view.message = message

    except asyncio.TimeoutError:
        logger.error(f"Genius search timed out for '{clean_title}'.")
        await interaction.followup.send("Sorry, the lyrics search took too long to respond. Please try again later.", silent=SILENT_MESSAGES, ephemeral=True)
    except Exception as e:
        logger.error(f"Error fetching/displaying Genius lyrics for '{clean_title}': {e}")
        await interaction.followup.send("An error occurred while displaying the lyrics.", silent=SILENT_MESSAGES, ephemeral=True)

def format_lyrics_display(lyrics_lines, current_line_index):
    """
    Formats the lyrics for Discord display, correctly handling
    newlines and problematic Markdown characters.
    """
    def clean(text):
        # Replaces backticks and removes Windows newlines (\r)
        return text.replace('`', "'").replace('\r', '')

    display_parts = []

    # Defines the context (how many lines to show before/after)
    context_lines = 4

    # Handles the case where the karaoke has not started yet
    if current_line_index == -1:
        display_parts.append("*(Waiting for the first line...)*\n")
        # We display the next 5 lines
        for line_obj in lyrics_lines[:5]:
            # We split each line in case it contains newlines
            for sub_line in clean(line_obj['text']).split('\n'):
                if sub_line.strip(): # Ignore empty lines
                    display_parts.append(f"`{sub_line}`")
    else:
        # Calculates the range of lines to display
        start_index = max(0, current_line_index - context_lines)
        end_index = min(len(lyrics_lines), current_line_index + context_lines + 1)

        # Loop over the lines to display
        for i in range(start_index, end_index):
            line_obj = lyrics_lines[i]
            is_current_line_chunk = (i == current_line_index)

            # === THIS IS THE LOGIC THAT 100% FIXES THE BUG ===
            # We split the current lyric line into sub-lines
            sub_lines = clean(line_obj['text']).split('\n')

            for index, sub_line in enumerate(sub_lines):
                if not sub_line.strip(): continue

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
    return '```[' + '█' * filled + '░' * unfilled + '] ' + f'{int(progress * 100)}%```'

# --- Platform URL Processors ---

# --- FINAL PROCESS_SPOTIFY_URL FUNCTION (Cascade Architecture) ---
async def process_spotify_url(url, interaction):
    """
    Processes a Spotify URL with a cascade architecture:
    1. Tries with the official API (spotipy) for speed and completeness.
    2. On failure (e.g., editorial playlist), falls back to the scraper (spotifyscraper).
    """
    guild_id = interaction.guild_id
    clean_url = url.split('?')[0]

    # --- METHOD 1: OFFICIAL API (SPOTIPY) ---
    if sp:
        try:
            logger.info(f"Attempt 1: Official API (Spotipy) for {clean_url}")
            tracks_to_return = []

            loop = asyncio.get_event_loop()

            if 'playlist' in clean_url:
                results = await loop.run_in_executor(None, lambda: sp.playlist_items(clean_url, fields='items.track.name,items.track.artists.name,next', limit=100))
                while results:
                    for item in results['items']:
                        if item and item.get('track'):
                            track = item['track']
                            tracks_to_return.append((track['name'], track['artists'][0]['name']))
                    if results['next']:
                        results = await loop.run_in_executor(None, lambda: sp.next(results))
                    else:
                        results = None

            elif 'album' in clean_url:
                results = await loop.run_in_executor(None, lambda: sp.album_tracks(clean_url, limit=50))
                while results:
                    for track in results['items']:
                        tracks_to_return.append((track['name'], track['artists'][0]['name']))
                    if results['next']:
                        results = await loop.run_in_executor(None, lambda: sp.next(results))
                    else:
                        results = None

            elif 'track' in clean_url:
                track = await loop.run_in_executor(None, lambda: sp.track(clean_url))
                tracks_to_return.append((track['name'], track['artists'][0]['name']))

            elif 'artist' in clean_url:
                results = await loop.run_in_executor(None, lambda: sp.artist_top_tracks(clean_url))
                for track in results['tracks']:
                    tracks_to_return.append((track['name'], track['artists'][0]['name']))

            if not tracks_to_return:
                    raise ValueError("No tracks found via API.")

            logger.info(f"Success with Spotipy: {len(tracks_to_return)} tracks retrieved.")
            return tracks_to_return

        except Exception as e:
            logger.warning(f"Spotipy API failed for {clean_url} (Reason: {e}). Switching to plan B: SpotifyScraper.")

    # --- METHOD 2: FALLBACK (SPOTIFYSCRAPER) ---
    if spotify_scraper_client:
        try:
            logger.info(f"Attempt 2: Scraper (SpotifyScraper) for {clean_url}")
            tracks_to_return = []
            loop = asyncio.get_event_loop()

            if 'playlist' in clean_url:
                data = await loop.run_in_executor(None, lambda: spotify_scraper_client.get_playlist_info(clean_url))
                for track in data.get('tracks', []):
                    tracks_to_return.append((track.get('name', 'Unknown Title'), track.get('artists', [{}])[0].get('name', 'Unknown Artist')))

            elif 'album' in clean_url:
                data = await loop.run_in_executor(None, lambda: spotify_scraper_client.get_album_info(clean_url))
                for track in data.get('tracks', []):
                    tracks_to_return.append((track.get('name', 'Unknown Title'), track.get('artists', [{}])[0].get('name', 'Unknown Artist')))

            elif 'track' in clean_url:
                data = await loop.run_in_executor(None, lambda: spotify_scraper_client.get_track_info(clean_url))
                tracks_to_return.append((data.get('name', 'Unknown Title'), data.get('artists', [{}])[0].get('name', 'Unknown Artist')))

            if not tracks_to_return:
                raise SpotifyScraperError("The scraper could not find any tracks either.")

            logger.info(f"Success with SpotifyScraper: {len(tracks_to_return)} tracks retrieved (potentially limited).")
            return tracks_to_return

        except Exception as e:
            logger.error(f"Both methods (API and Scraper) failed. Final SpotifyScraper error: {e}", exc_info=True)
            embed = Embed(description=get_messages("spotify_error", guild_id), color=0xFFB6C1 if get_mode(guild_id) else discord.Color.red())
            await interaction.followup.send(silent=SILENT_MESSAGES,embed=embed, ephemeral=True)
            return None

    logger.critical("No client (Spotipy or SpotifyScraper) is functional.")
    # Optional: send an error message if no client is available
    embed = Embed(description="Critical error: Spotify services are unreachable.", color=discord.Color.dark_red())
    await interaction.followup.send(silent=SILENT_MESSAGES,embed=embed, ephemeral=True)
    return None

# Process Deezer URLs
async def process_deezer_url(url, interaction):
    guild_id = interaction.guild_id
    try:
        deezer_share_regex = re.compile(r'^(https?://)?(link\.deezer\.com)/s/.+$')
        if deezer_share_regex.match(url):
            logger.info(f"Detected Deezer share link: {url}. Resolving redirect...")
            response = requests.head(url, allow_redirects=True, timeout=10)
            response.raise_for_status()
            resolved_url = response.url
            logger.info(f"Resolved to: {resolved_url}")
            url = resolved_url

        parsed_url = urlparse(url)
        path_parts = parsed_url.path.strip('/').split('/')
        if len(path_parts) > 1 and len(path_parts[0]) == 2:
            path_parts = path_parts[1:]
        if len(path_parts) < 2:
            raise ValueError("Invalid Deezer URL format")

        resource_type = path_parts[0]
        resource_id = path_parts[1].split('?')[0]

        base_api_url = "https://api.deezer.com"
        logger.info(f"Fetching Deezer {resource_type} with ID {resource_id} from URL {url}")

        tracks = []
        if resource_type == 'track':
            response = requests.get(f"{base_api_url}/track/{resource_id}", timeout=10)
            response.raise_for_status()
            data = response.json()
            if 'error' in data:
                raise Exception(f"Deezer API error: {data['error']['message']}")
            logger.info(f"Processing Deezer track: {data.get('title', 'Unknown Title')}")
            track_name = data.get('title', 'Unknown Title')
            artist_name = data.get('artist', {}).get('name', 'Unknown Artist')
            tracks.append((track_name, artist_name))

        elif resource_type == 'playlist':
            next_url = f"{base_api_url}/playlist/{resource_id}/tracks"
            total_tracks = 0
            fetched_tracks = 0

            while next_url:
                response = requests.get(next_url, timeout=10)
                response.raise_for_status()
                data = response.json()

                if 'error' in data:
                    raise Exception(f"Deezer API error: {data['error']['message']}")

                if not data.get('data'):
                    raise ValueError("No tracks found in the playlist or playlist is empty")

                for track in data['data']:
                    track_name = track.get('title', 'Unknown Title')
                    artist_name = track.get('artist', {}).get('name', 'Unknown Artist')
                    tracks.append((track_name, artist_name))

                fetched_tracks += len(data['data'])
                total_tracks = data.get('total', fetched_tracks)
                logger.info(f"Fetched {fetched_tracks}/{total_tracks} tracks from playlist {resource_id}")

                next_url = data.get('next')
                if next_url:
                    logger.info(f"Fetching next page: {next_url}")

            logger.info(f"Processing Deezer playlist: {data.get('title', 'Unknown Playlist')} with {len(tracks)} tracks")

        elif resource_type == 'album':
            response = requests.get(f"{base_api_url}/album/{resource_id}/tracks", timeout=10)
            response.raise_for_status()
            data = response.json()
            if 'error' in data:
                raise Exception(f"Deezer API error: {data['error']['message']}")
            if not data.get('data'):
                raise ValueError("No tracks found in the album or album is empty")
            logger.info(f"Processing Deezer album: {data.get('title', 'Unknown Album')}")
            for track in data['data']:
                track_name = track.get('title', 'Unknown Title')
                artist_name = track.get('artist', {}).get('name', 'Unknown Artist')
                tracks.append((track_name, artist_name))
            logger.info(f"Extracted {len(tracks)} tracks from album {resource_id}")

        elif resource_type == 'artist':
            response = requests.get(f"{base_api_url}/artist/{resource_id}/top?limit=10", timeout=10)
            response.raise_for_status()
            data = response.json()
            if 'error' in data:
                raise Exception(f"Deezer API error: {data['error']['message']}")
            if not data.get('data'):
                raise ValueError("No top tracks found for the artist")
            logger.info(f"Processing Deezer artist: {data.get('name', 'Unknown Artist')}")
            for track in data['data']:
                track_name = track.get('title', 'Unknown Title')
                artist_name = track.get('artist', {}).get('name', 'Unknown Artist')
                tracks.append((track_name, artist_name))
            logger.info(f"Extracted {len(tracks)} top tracks for artist {resource_id}")

        if not tracks:
            raise ValueError("No valid tracks found in the Deezer resource")

        logger.info(f"Successfully processed Deezer {resource_type} with {len(tracks)} tracks")
        return tracks

    except requests.exceptions.RequestException as e:
        logger.error(f"Network error fetching Deezer URL {url}: {e}")
        embed = Embed(
            description="Network error while retrieving Deezer data. Please try again later.",
            color=0xFFB6C1 if get_mode(guild_id) else discord.Color.red()
        )
        await interaction.followup.send(silent=SILENT_MESSAGES,embed=embed, ephemeral=True)
        return None
    except ValueError as e:
        logger.error(f"Invalid Deezer data for URL {url}: {e}")
        embed = Embed(
            description=f"Error: {str(e)}",
            color=0xFFB6C1 if get_mode(guild_id) else discord.Color.red()
        )
        await interaction.followup.send(silent=SILENT_MESSAGES,embed=embed, ephemeral=True)
        return None
    except Exception as e:
        logger.error(f"Unexpected error processing Deezer URL {url}: {e}")
        embed = Embed(
            description=get_messages("deezer_error", guild_id),
            color=0xFFB6C1 if get_mode(guild_id) else discord.Color.red()
        )
        await interaction.followup.send(silent=SILENT_MESSAGES,embed=embed, ephemeral=True)
        return None

# Process Apple Music URLs
async def process_apple_music_url(url, interaction):
    guild_id = interaction.guild_id
    logger.info(f"Starting processing for Apple Music URL: {url}")

    clean_url = url.split('?')[0]
    browser = None  # Initialize the browser to None

    try:
        async with async_playwright() as p:
            browser = await p.firefox.launch(headless=True)
            context = await browser.new_context(
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/115.0'
            )
            page = await context.new_page()

            await page.route("**/*.{png,jpg,jpeg,svg,woff,woff2}", lambda route: route.abort())
            logger.info("Optimization: Disabled loading of images and fonts.")

            logger.info("Navigating to the page with a 90 second timeout...")
            await page.goto(clean_url, wait_until="domcontentloaded", timeout=90000)
            logger.info("Page loaded. Extracting data...")

            tracks = []
            resource_type = urlparse(clean_url).path.strip('/').split('/')[1]

            if resource_type in ['album', 'playlist']:
                await page.wait_for_selector('div.songs-list-row', timeout=15000)
                main_artist_name = "Unknown Artist"
                try:
                    main_artist_el = await page.query_selector('.headings__subtitles a')
                    if main_artist_el:
                        main_artist_name = await main_artist_el.inner_text()
                except Exception:
                    logger.warning("Unable to determine the main artist.")

                track_rows = await page.query_selector_all('div.songs-list-row')
                for row in track_rows:
                    try:
                        title_el = await row.query_selector('div.songs-list-row__song-name')
                        title = await title_el.inner_text() if title_el else "Unknown Title"

                        artist_elements = await row.query_selector_all('div.songs-list-row__by-line a')
                        if artist_elements:
                            artist_names = [await el.inner_text() for el in artist_elements]
                            artist = " & ".join(artist_names)
                        else:
                            artist = main_artist_name

                        if title != "Unknown Title":
                            tracks.append((title.strip(), artist.strip()))
                    except Exception as e:
                        logger.warning(f"Unable to extract a line: {e}")

            elif resource_type == 'song':
                try:
                    title_selector = 'h1.song-header-page__song-header-title'
                    artist_selector = 'div.song-header-page-details a.click-action'
                    await page.wait_for_selector(title_selector, timeout=10000)
                    title = await page.locator(title_selector).first.inner_text()
                    artist = await page.locator(artist_selector).first.inner_text()
                    if title and artist:
                        tracks.append((title.strip(), artist.strip()))
                except Exception:
                    page_title = await page.title()
                    parts = page_title.split(' by ')
                    title = parts[0].replace("", "").strip()
                    artist = parts[1].split(' on Apple')[0].strip()
                    if title and artist:
                        tracks.append((title, artist))

            if not tracks:
                raise ValueError("No tracks found in Apple Music resource")

            logger.info(f"Success! {len(tracks)} track(s) extracted.")
            return tracks

    except Exception as e:
        logger.error(f"Error processing Apple Music URL {url}: {e}", exc_info=True)
        embed = Embed(
            description=get_messages("apple_music_error", guild_id),
            color=0xFFB6C1 if get_mode(guild_id) else discord.Color.red()
        )
        try:
            await interaction.followup.send(silent=SILENT_MESSAGES,embed=embed, ephemeral=True)
        except discord.errors.InteractionResponded:
            await interaction.edit_original_response(embed=embed)
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
            meta_text = await page.locator(meta_item_selector).first.inner_text(timeout=3000)
            total_tracks_expected = int(re.search(r'\d+', meta_text).group())
            logger.info(f"Goal: Extract {total_tracks_expected} tracks.")
        except Exception:
            logger.warning("Unable to determine the total number of tracks.")
            total_tracks_expected = 0
        track_row_selector = 'div[data-track-id]'
        all_tracks = []
        seen_track_ids = set()
        stagnation_counter = 0
        max_loops = 500
        for i in range(max_loops):
            if total_tracks_expected > 0 and len(all_tracks) >= total_tracks_expected:
                logger.info("All expected leads have been found. Early shutdown.")
                break
            track_elements = await page.query_selector_all(track_row_selector)
            if not track_elements and i > 0: break
            new_tracks_found_in_loop = False
            for element in track_elements:
                track_id = await element.get_attribute('data-track-id')
                if track_id and track_id not in seen_track_ids:
                    new_tracks_found_in_loop = True
                    seen_track_ids.add(track_id)
                    try:
                        title_el = await element.query_selector('span._titleText_51cccae, span[data-test="table-cell-title"]')
                        artist_el = await element.query_selector('a._item_39605ae, a[data-test="grid-item-detail-text-title-artist"]')
                        if title_el and artist_el:
                            title = (await title_el.inner_text()).split("<span>")[0].strip()
                            artist = await artist_el.inner_text()
                            if title and artist: all_tracks.append((title, artist))
                    except Exception: continue
            if not new_tracks_found_in_loop and i > 1:
                stagnation_counter += 1
                if stagnation_counter >= 5:
                    logger.info("Stable stagnation. End of process.")
                    break
            else: stagnation_counter = 0
            if track_elements:
                await track_elements[-1].scroll_into_view_if_needed(timeout=10000)
                await asyncio.sleep(0.75)
        logger.info(f"Process completed. Final total of unique tracks extracted: {len(all_tracks)}")
        return list(dict.fromkeys(all_tracks))

    browser = None  # Initialize the browser to None
    try:
        clean_url = url.split('?')[0]
        parsed_url = urlparse(clean_url)
        path_parts = parsed_url.path.strip('/').split('/')

        resource_type = None
        if 'playlist' in path_parts: resource_type = 'playlist'
        elif 'album' in path_parts: resource_type = 'album'
        elif 'mix' in path_parts: resource_type = 'mix'
        elif 'track' in path_parts: resource_type = 'track'
        elif 'video' in path_parts: resource_type = 'video'

        if resource_type is None:
            raise ValueError("Tidal URL not supported.")

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36')
            await page.goto(clean_url, wait_until="domcontentloaded")
            logger.info(f"Navigate to Tidal URL ({resource_type}): {clean_url}")

            await asyncio.sleep(3)
            unique_tracks = []

            if resource_type in ['playlist', 'album', 'mix']:
                unique_tracks = await load_and_extract_all_tracks(page)

            elif resource_type == 'track' or resource_type == 'video':
                logger.info(f"Extracting a single media ({resource_type})...")
                try:
                    await page.wait_for_selector('div[data-test="artist-profile-header"], div[data-test="footer-player"]', timeout=10000)
                    title_selector = 'span[data-test="now-playing-track-title"], h1[data-test="title"]'
                    artist_selector = 'a[data-test="grid-item-detail-text-title-artist"]'
                    title = await page.locator(title_selector).first.inner_text(timeout=5000)
                    artist = await page.locator(artist_selector).first.inner_text(timeout=5000)

                    if not title or not artist:
                        raise ValueError("Missing title or artist.")

                    logger.info(f"Unique media found: {title.strip()} - {artist.strip()}")
                    unique_tracks = [(title.strip(), artist.strip())]

                except Exception as e:
                    logger.warning(f"Direct extraction method failed ({e}), attempting with page title...")
                    try:
                        page_title = await page.title()
                        title, artist = "", ""
                        if " - " in page_title:
                            parts = page_title.split(' - ')
                            artist, title = parts[0], parts[1].split(' on TIDAL')[0]
                        elif " by " in page_title:
                            parts = page_title.split(' by ')
                            title, artist = parts[0], parts[1].split(' on TIDAL')[0]

                        if not title or not artist: raise ValueError("The page title format is unknown.")

                        logger.info(f"Unique media found via page title: {title.strip()} - {artist.strip()}")
                        unique_tracks = [(title.strip(), artist.strip())]
                    except Exception as fallback_e:
                        await page.screenshot(path=f"tidal_{resource_type}_extraction_failed.png")
                        raise ValueError(f"All extraction methods failed. Final error: {fallback_e}")

            if not unique_tracks:
                raise ValueError("No tracks could be retrieved from the Tidal resource.")

            return unique_tracks

    except Exception as e:
        logger.error(f"Major error in process_tidal_url for {url}: {e}")
        if interaction:
                embed = Embed(description=get_messages("tidal_error", guild_id), color=0xFFB6C1 if get_mode(guild_id) else discord.Color.red())
                await interaction.followup.send(silent=SILENT_MESSAGES,embed=embed, ephemeral=True)
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
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36'
            )
            page = await context.new_page()

            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            logger.info("Page loaded. Cookie management.")

            try:
                await page.click('music-button:has-text("Accepter les cookies")', timeout=7000)
                logger.info("Cookie banner accepted.")
            except Exception:
                logger.info("No cookie banner found.")

            tracks = []

            if is_album or is_track:
                page_type = "Album" if is_album else "Track"
                logger.info(f"Page of type '{page_type}' detected. Using JSON extraction method.")

                selector = 'script[type="application/ld+json"]'
                await page.wait_for_selector(selector, state='attached', timeout=20000)

                json_ld_scripts = await page.locator(selector).all_inner_texts()

                found_data = False
                for script_content in json_ld_scripts:
                    data = json.loads(script_content)
                    if data.get('@type') == 'MusicAlbum' or (is_album and 'itemListElement' in data):
                        album_artist = data.get('byArtist', {}).get('name', 'Unknown Artist')
                        for item in data.get('itemListElement', []):
                            track_name = item.get('name')
                            track_artist = item.get('byArtist', {}).get('name', album_artist)
                            if track_name and track_artist:
                                tracks.append((track_name, track_artist))
                        found_data = True
                        break
                    elif data.get('@type') == 'MusicRecording':
                        track_name = data.get('name')
                        track_artist = data.get('byArtist', {}).get('name', 'Unknown Artist')
                        if track_name and track_artist:
                            tracks.append((track_name, track_artist))
                        found_data = True
                        break

                if not found_data:
                    raise ValueError(f"No data of type 'MusicAlbum' or 'MusicRecording' found in JSON-LD tags.")

            elif is_playlist:
                logger.info("'Playlist' type page detected. Using fast pre-virtualization extraction.")
                try:
                    await page.wait_for_selector("music-image-row[primary-text]", timeout=20000)
                    logger.info("Tracklist detected. Waiting 3.5 seconds for initial load.")
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
                tracks = [(track['title'], track['artist']) for track in tracks_data]

            else:
                raise ValueError("Amazon Music URL not recognized (neither album, nor playlist, nor track).")

            if not tracks:
                raise ValueError("No tracks could be extracted from the page.")

            logger.info(f"Processing complete. {len(tracks)} track(s) found. First track: {tracks[0]}")
            return tracks

    except Exception as e:
        logger.error(f"Final error in process_amazon_music_url for {url}: {e}", exc_info=True)
        if 'page' in locals() and page and not page.is_closed():
                await page.screenshot(path="amazon_music_scrape_failed.png")
                logger.info("Screenshot of the error saved.")

        embed = Embed(description=get_messages("amazon_music_error", guild_id), color=0xFFB6C1 if get_mode(guild_id) else discord.Color.red())
        try:
            if interaction and not interaction.is_expired():
                await interaction.followup.send(silent=SILENT_MESSAGES,embed=embed, ephemeral=True)
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
    query = re.sub(r'[\x00-\x1F\x7F]', '', query)  # Remove control chars
    query = re.sub(r'\s+', ' ', query).strip()  # Normalize spaces
    return query

# Async yt-dlp info extraction
async def extract_info_async(ydl_opts, query, loop=None):
    if loop is None:
        loop = asyncio.get_running_loop()

    def extract():
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            return ydl.extract_info(query, download=False)

    return await loop.run_in_executor(None, extract)

# YouTube Mix and SoundCloud Stations utilities
def get_video_id(url):
    parsed = urlparse(url)
    if parsed.hostname in ('youtube.com', 'www.youtube.com', 'youtu.be'):
        if parsed.hostname == 'youtu.be':
            return parsed.path[1:]
        if parsed.path == '/watch':
            query = parse_qs(parsed.query)
            return query.get('v', [None])[0]
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
    
# Helper function to parse yt-dlp errors into user-friendly messages
def parse_yt_dlp_error(error_string: str) -> tuple[str, str, str]:
    """
    Parses a yt-dlp error string to find a known cause.
    Returns a tuple of (emoji, title_key, description_key).
    """
    error_lower = error_string.lower()
    if "sign in to confirm your age" in error_lower or "age-restricted" in error_lower:
        return ("🔞", "error_title_age_restricted", "error_desc_age_restricted")
    if "private video" in error_lower:
        return ("🔒", "error_title_private", "error_desc_private")
    if "video is unavailable" in error_lower:
        return ("❓", "error_title_unavailable", "error_desc_unavailable")
    # Default fallback for other access errors
    return ("🚫", "error_title_generic", "error_desc_generic")

# ==============================================================================
# 4. CORE AUDIO & PLAYBACK LOGIC
# ==============================================================================

# Global error handler
async def handle_playback_error(guild_id: int, error: Exception):
    """
    Handles unexpected errors during playback, informs the user,
    and provides instructions for reporting the bug.
    """
    music_player = get_player(guild_id)
    if not music_player.text_channel:
        logger.error(f"Cannot report error in guild {guild_id}, no text channel available.")
        return

    # Log the full error with traceback
    tb_str = ''.join(traceback.format_exception(type(error), value=error, tb=error.__traceback__))
    logger.error(f"Unhandled playback error in guild {guild_id}:\n{tb_str}")

    # Prepare user-facing embed
    is_kawaii = get_mode(guild_id)
    embed = Embed(
        title=get_messages("critical_error_title", guild_id),
        description=get_messages("critical_error_description", guild_id),
        color=0xFF9AA2 if is_kawaii else discord.Color.red()
    )
    embed.add_field(
        name=get_messages("critical_error_report_field", guild_id),
        value=get_messages("critical_error_report_value", guild_id),
        inline=False
    )
    # Format a concise error message for the user to copy
    error_details = f"URL: {music_player.current_url}\nError: {str(error)[:500]}"
    embed.add_field(
        name=get_messages("critical_error_details_field", guild_id),
        value=f"```\n{error_details}\n```",
        inline=False
    )
    embed.set_footer(text="Your help is appreciated!")

    try:
        await music_player.text_channel.send(embed=embed, silent=SILENT_MESSAGES)
    except Exception as e:
        logger.error(f"Failed to send error report embed to guild {guild_id}: {e}")

    # Reset player state to prevent getting stuck
    music_player.current_task = None
    music_player.current_info = None
    music_player.current_url = None
    # Optionally, clear the queue
    while not music_player.queue.empty():
        music_player.queue.get_nowait()

    # Disconnect the bot
    if music_player.voice_client:
        await music_player.voice_client.disconnect()
        # Full reset
        music_players[guild_id] = MusicPlayer()
        logger.info(f"Player for guild {guild_id} has been reset and disconnected due to a critical error.")


# --- FINAL & ROBUST play_audio FUNCTION v4 ---
async def play_audio(guild_id, seek_time=0, is_a_loop=False):
    music_player = get_player(guild_id)
    try:
        # If we are seeking, it means we are resuming. We MUST NOT fetch a new song.
        if seek_time == 0 and not is_a_loop:
            if music_player.lyrics_task and not music_player.lyrics_task.done():
                music_player.lyrics_task.cancel()

            if music_player.queue.empty():
                # Autoplay & 24/7 logic... (full logic is here)
                is_kawaii = get_mode(guild_id)
                if music_player.autoplay_enabled and music_player.current_url:
                    if music_player.text_channel:
                        embed = Embed(description=get_messages("autoplay_added", guild_id), color=0xFFB6C1 if is_kawaii else discord.Color.blue())
                        await music_player.text_channel.send(embed=embed, silent=SILENT_MESSAGES)
                    async def add_autoplay_track(entry):
                        if not entry or 'url' not in entry: return
                        await music_player.queue.put({'url': entry['url'], 'title': entry.get('title', 'Unknown Title'), 'webpage_url': entry.get('webpage_url', entry['url']), 'is_single': True})
                    if "youtube.com" in music_player.current_url or "youtu.be" in music_player.current_url:
                        mix_playlist_url = get_mix_playlist_url(music_player.current_url)
                        if mix_playlist_url:
                            try:
                                info = await extract_info_async({"extract_flat": True, "quiet": True}, mix_playlist_url)
                                if info.get("entries"):
                                    current_video_id = get_video_id(music_player.current_url)
                                    for entry in info["entries"]:
                                        if entry and get_video_id(entry.get("url", "")) != current_video_id: await add_autoplay_track(entry)
                            except Exception as e: logger.error(f"YouTube Mix Error: {e}")
                elif _24_7_active.get(guild_id, False) and music_player.history:
                    logger.info(f"24/7 Normal: Re-queuing {len(music_player.history)} tracks.")
                    for track_info in music_player.history: await music_player.queue.put(track_info)
                    music_player.history.clear()
                
                if music_player.queue.empty():
                    music_player.current_task = None; music_player.current_info = None
                    await asyncio.sleep(60)
                    if music_player.voice_client and not music_player.voice_client.is_playing() and len(music_player.voice_client.channel.members) == 1:
                        await music_player.voice_client.disconnect()
                    return

            track_info_from_queue = await music_player.queue.get()
            music_player.history.append(track_info_from_queue)
            try:
                full_info = await extract_info_async({"format": "bestaudio[acodec=opus]/bestaudio/best", "quiet": True, "no_warnings": True, "socket_timeout": 10}, track_info_from_queue['url'])
                music_player.current_info = full_info
                music_player.current_url = full_info.get("webpage_url", track_info_from_queue['url'])
                music_player.last_was_single = track_info_from_queue.get('is_single', True)
            except Exception as e:
                logger.warning(f"Failed to extract info for {track_info_from_queue['url']}: {e}. Skipping.")
                music_player.current_task = bot.loop.create_task(play_audio(guild_id))
                return
        
        if not music_player.voice_client or not music_player.voice_client.is_connected(): return
        if not music_player.current_info: return

        active_filters = server_filters.get(guild_id, set())
        filter_chain = ",".join([AUDIO_FILTERS[f] for f in active_filters if f in AUDIO_FILTERS])
        
        before_options = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
        if seek_time > 0: before_options += f" -ss {seek_time}"

        ffmpeg_options = {"before_options": before_options, "options": "-vn"}
        if filter_chain: ffmpeg_options["options"] += f" -af \"{filter_chain}\""

        source = discord.FFmpegPCMAudio(music_player.current_info["url"], **ffmpeg_options)

        def after_playing(error):
            # --- THE ACTUAL KILL SWITCH ---
            if guild_id in reconnecting_guilds:
                logger.info(f"Guild {guild_id} is reconnecting, aborting the 'after_playing' callback.")
                return
            # --- END OF KILL SWITCH ---

            if error: logger.error(f'Error after playing in guild {guild_id}: {error}')
            
            async def schedule_next():
                if music_player.loop_current:
                    current_track_data = {'url': music_player.current_url, 'title': music_player.current_info.get('title'), 'webpage_url': music_player.current_url, 'is_single': music_player.last_was_single}
                    items_in_queue = list(music_player.queue._queue)
                    new_queue = asyncio.Queue()
                    await new_queue.put(current_track_data)
                    for item in items_in_queue: await new_queue.put(item)
                    music_player.queue = new_queue
                    if music_player.history: music_player.history.pop()
                    await play_audio(guild_id, is_a_loop=True)
                else:
                    await play_audio(guild_id)

            music_player.current_task = bot.loop.create_task(schedule_next())

        music_player.voice_client.play(source, after=after_playing)
        music_player.start_time = seek_time
        music_player.playback_started_at = time.time()
        
        if seek_time == 0 and not is_a_loop:
            is_kawaii = get_mode(guild_id)
            title = music_player.current_info.get("title", "Unknown Title")
            webpage_url = music_player.current_info.get("webpage_url", music_player.current_url)
            embed = Embed(title=get_messages("now_playing_title", guild_id), description=get_messages("now_playing_description", guild_id).format(title=title, url=webpage_url), color=0xC7CEEA if is_kawaii else discord.Color.green())
            if music_player.current_info.get("thumbnail"): embed.set_thumbnail(url=music_player.current_info["thumbnail"])
            if music_player.text_channel: await music_player.text_channel.send(embed=embed, silent=SILENT_MESSAGES)

    except Exception as e:
        await handle_playback_error(guild_id, e)

async def update_karaoke_task(guild_id: int):
    """Background task for karaoke mode, manages filters and speed."""
    music_player = get_player(guild_id)
    last_line_index = -1
    footer_has_been_removed = False

    while music_player.voice_client and music_player.voice_client.is_connected():
        try:
            if not music_player.voice_client.is_playing():
                await asyncio.sleep(0.5)
                continue

            real_elapsed_time = (time.time() - music_player.playback_started_at)
            effective_time_in_song = music_player.start_time + (real_elapsed_time * music_player.playback_speed)

            current_line_index = -1
            for i, line in enumerate(music_player.synced_lyrics):
                if effective_time_in_song * 1000 >= line['time']:
                    current_line_index = i
                else:
                    break

            if current_line_index != last_line_index:
                last_line_index = current_line_index
                new_description = format_lyrics_display(music_player.synced_lyrics, current_line_index)

                if music_player.lyrics_message and music_player.lyrics_message.embeds:
                    new_embed = music_player.lyrics_message.embeds[0]
                    new_embed.description = new_description

                    if not footer_has_been_removed:
                        new_embed.set_footer(text=None)
                        footer_has_been_removed = True

                    await music_player.lyrics_message.edit(embed=new_embed)

            await asyncio.sleep(1.0)

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error in karaoke task: {e}")
            break

    if music_player.lyrics_message:
        try:
            await music_player.lyrics_message.edit(content="*Karaoke session finished!*", embed=None, view=None)
        except discord.NotFound:
            pass

    music_player.lyrics_task = None
    music_player.lyrics_message = None

# ==============================================================================
# 5. DISCORD SLASH COMMANDS
# ==============================================================================

@bot.tree.command(name="lyrics", description="Get song lyrics from Genius.")
async def lyrics(interaction: discord.Interaction):
    guild_id = interaction.guild_id
    music_player = get_player(guild_id)

    if not music_player.voice_client or not music_player.voice_client.is_playing() or not music_player.current_info:
        return await interaction.response.send_message("No music is currently playing.", silent=SILENT_MESSAGES, ephemeral=True)

    await interaction.response.defer()
    await fetch_and_display_genius_lyrics(interaction)

@bot.tree.command(name="karaoke", description="Start a synced karaoke-style lyrics display.")
async def karaoke(interaction: discord.Interaction):
    guild_id = interaction.guild_id
    music_player = get_player(guild_id)
    is_kawaii = get_mode(guild_id)

    if not music_player.voice_client or not music_player.voice_client.is_playing() or not music_player.current_info:
        return await interaction.response.send_message("No music is currently playing.", silent=SILENT_MESSAGES, ephemeral=True)

    if music_player.lyrics_task and not music_player.lyrics_task.done():
        return await interaction.response.send_message("Lyrics are already being displayed!", silent=SILENT_MESSAGES, ephemeral=True)

    async def proceed_with_karaoke():
        if not interaction.response.is_done():
            await interaction.response.defer()

        clean_title, artist_name = get_cleaned_song_info(music_player.current_info)
        loop = asyncio.get_running_loop()
        lrc = None

        try:
            precise_query = f"{clean_title} {artist_name}"
            logger.info(f"Attempting precise synced lyrics search: '{precise_query}'")
            lrc = await asyncio.wait_for(
                loop.run_in_executor(None, syncedlyrics.search, precise_query),
                timeout=7.0
            )
        except (asyncio.TimeoutError, Exception):
            logger.warning("Precise synced search failed or timed out.")

        if not lrc:
            try:
                logger.info(f"Trying broad search: '{clean_title}'")
                lrc = await asyncio.wait_for(
                    loop.run_in_executor(None, syncedlyrics.search, clean_title),
                    timeout=7.0
                )
            except (asyncio.TimeoutError, Exception):
                logger.warning("Broad synced search also failed or timed out.")

        lyrics_lines = []
        if lrc:
            lyrics_lines = [{'time': int(m.group(1))*60000 + int(m.group(2))*1000 + int(m.group(3)), 'text': m.group(4).strip()} for line in lrc.splitlines() if (m := re.match(r'\[(\d{2}):(\d{2})\.(\d{2,3})\](.*)', line))]

        if not lyrics_lines:
            error_title = get_messages("karaoke_not_found_title", guild_id)
            error_desc = get_messages("karaoke_not_found_description", guild_id).format(query=f"{clean_title} {artist_name}")

            error_embed = Embed(
                title=error_title,
                description=error_desc,
                color=0xFF9AA2 if is_kawaii else discord.Color.red()
            )

            view = KaraokeRetryView(
                original_interaction=interaction,
                suggested_query=clean_title,
                guild_id=guild_id
            )
            await interaction.followup.send(silent=SILENT_MESSAGES,embed=error_embed, view=view)
            return

        music_player.synced_lyrics = lyrics_lines
        embed = Embed(title=f"🎤 Karaoke for {clean_title}", description="Starting karaoke...", color=0xC7CEEA if is_kawaii else discord.Color.blue())

        lyrics_message = await interaction.followup.send(silent=SILENT_MESSAGES,embed=embed, wait=True)
        music_player.lyrics_message = lyrics_message
        music_player.lyrics_task = asyncio.create_task(update_karaoke_task(guild_id))

    if guild_id in karaoke_disclaimer_shown:
        await proceed_with_karaoke()
    else:
        warning_embed = Embed(
            title=get_messages("karaoke_warning_title", guild_id),
            description=get_messages("karaoke_warning_description", guild_id),
            color=0xFFB6C1 if is_kawaii else discord.Color.orange()
        )
        view = KaraokeWarningView(interaction, karaoke_coro=proceed_with_karaoke)

        button_label = get_messages("karaoke_warning_button", guild_id)
        view.children[0].label = button_label

        await interaction.response.send_message(silent=SILENT_MESSAGES,embed=warning_embed, view=view)

# /kaomoji command
@bot.tree.command(name="kaomoji", description="Enable/disable kawaii mode")
@app_commands.default_permissions(administrator=True)
async def toggle_kawaii(interaction: discord.Interaction):
    guild_id = interaction.guild_id
    kawaii_mode[guild_id] = not get_mode(guild_id)
    state = get_messages("kawaii_state_enabled", guild_id) if kawaii_mode[guild_id] else get_messages("kawaii_state_disabled", guild_id)

    embed = Embed(
        description=get_messages("kawaii_toggle", guild_id).format(state=state),
        color=0xFFB6C1 if kawaii_mode[guild_id] else discord.Color.blue()
    )
    await interaction.response.send_message(silent=SILENT_MESSAGES,embed=embed, ephemeral=True)

@bot.tree.command(name="play", description="Play a link or search for a song")
@app_commands.describe(query="Link or title of the song/video to play")
async def play(interaction: discord.Interaction, query: str):
    guild_id = interaction.guild_id
    is_kawaii = get_mode(guild_id)
    music_player = get_player(guild_id)

    if not interaction.user.voice or not interaction.user.voice.channel:
        embed = Embed(
            description=get_messages("no_voice_channel", guild_id),
            color=0xFF9AA2 if is_kawaii else discord.Color.red()
        )
        await interaction.response.send_message(silent=SILENT_MESSAGES,embed=embed, ephemeral=True)
        return

    await interaction.response.defer()

    if not music_player.voice_client or not music_player.voice_client.is_connected():
        try:
            if music_player.voice_client:
                await music_player.voice_client.disconnect(force=True)
            music_player.voice_client = await interaction.user.voice.channel.connect()
        except discord.errors.ClientException as e:
            logger.warning(f"Connection attempt failed, likely already connected. Error: {e}")
            if interaction.guild.voice_client:
                 await interaction.guild.voice_client.move_to(interaction.user.voice.channel)
                 music_player.voice_client = interaction.guild.voice_client
            else:
                 embed = Embed(description=get_messages("connection_error", guild_id), color=0xFF9AA2 if is_kawaii else discord.Color.red())
                 await interaction.followup.send(silent=SILENT_MESSAGES,embed=embed, ephemeral=True)
                 return
        except Exception as e:
            embed = Embed(
                description=get_messages("connection_error", guild_id),
                color=0xFF9AA2 if is_kawaii else discord.Color.red()
            )
            await interaction.followup.send(silent=SILENT_MESSAGES,embed=embed, ephemeral=True)
            logger.error(f"Error connecting: {e}")
            return

    music_player.text_channel = interaction.channel
    
    spotify_regex = re.compile(r'^(https?://)?(open\.spotify\.com)/.+$')
    deezer_regex = re.compile(r'^(https?://)?((www\.)?deezer\.com/(?:[a-z]{2}/)?(track|playlist|album|artist)/.+|(link\.deezer\.com)/s/.+)$')
    soundcloud_regex = re.compile(r'^(https?://)?(www\.)?(soundcloud\.com)/.+$')
    youtube_regex = re.compile(r'^(https?://)?(www\.)?(youtube\.com|youtu\.be)/.+$')
    ytmusic_regex = re.compile(r'^(https?://)?(music\.youtube\.com)/.+$')
    bandcamp_regex = re.compile(r'^(https?://)?([^\.]+)\.bandcamp\.com/.+$')
    apple_music_regex = re.compile(r'^(https?://)?(music\.apple\.com)/.+$')
    tidal_regex = re.compile(r'^(https?://)?(www\.)?tidal\.com/.+$')
    amazon_music_regex = re.compile(r'^(https?://)?(music\.amazon\.(fr|com|co\.uk|de|es|it|jp))/.+$')

    # OPTIMIZED search_track to return a full queue item or None
    async def search_track(track_info):
        track_name, artist_name = track_info
        original_query = f"{track_name} {artist_name}"
        sanitized_query = sanitize_query(original_query)
        cache_key = sanitized_query.lower()
        logger.info(f"Searching YouTube for: {original_query} (Sanitized: {sanitized_query})")

        try:
            cached_url = url_cache.get(cache_key)
            if cached_url:
                logger.info(f"Cache hit for {cache_key}")
                # We still need the title, so we must fetch info if not cached
                if isinstance(cached_url, dict):
                    return cached_url # Already cached with title
                else: # Old cache format (just URL)
                    pass # Proceed to fetch

            ydl_opts_search = {
                "format": "bestaudio/best", "quiet": True, "no_warnings": True,
                "extract_flat": True, "noplaylist": True, "no_color": True,
                "socket_timeout": 10,
            }

            # Search with a more precise query first
            search_query = f"ytsearch:\"{sanitized_query}\" audio"
            info = await extract_info_async(ydl_opts_search, search_query)
            
            # Fallback if no results
            if not info.get("entries"):
                search_query = f"ytsearch:{sanitized_query}"
                info = await extract_info_async(ydl_opts_search, search_query)

            if "entries" in info and info["entries"]:
                entry = info["entries"][0]
                video_url = entry.get("webpage_url", entry.get("url"))
                video_title = entry.get('title', 'Unknown Title')
                
                queue_item = {
                    'url': video_url,
                    'title': video_title,
                    'webpage_url': video_url,
                    'is_single': False # Assuming it's part of a playlist
                }
                url_cache[cache_key] = queue_item # Cache the full object
                return queue_item

            logger.warning(f"No YouTube results for {sanitized_query}")
            url_cache[cache_key] = None
            return None

        except Exception as e:
            logger.error(f"Failed to search YouTube for {sanitized_query}: {e}")
            url_cache[cache_key] = None
            return None

    if any(regex.match(query) for regex in [spotify_regex, deezer_regex, apple_music_regex, tidal_regex, amazon_music_regex]):
        platform_name = ""
        tracks = []
        
        if spotify_regex.match(query):
            platform_name = "Spotify"
            tracks = await process_spotify_url(query, interaction)
        elif deezer_regex.match(query):
            platform_name = "Deezer"
            tracks = await process_deezer_url(query, interaction)
        elif apple_music_regex.match(query):
            platform_name = "Apple Music"
            tracks = await process_apple_music_url(query, interaction)
        elif tidal_regex.match(query):
            platform_name = "Tidal"
            tracks = await process_tidal_url(query, interaction)
        elif amazon_music_regex.match(query):
            platform_name = "Amazon Music"
            tracks = await process_amazon_music_url(query, interaction)
        
        if not tracks:
            return # Error message is sent from the processing function

        if len(tracks) == 1:
             # It's a single track, handle it like a search
            query = f"{tracks[0][0]} {tracks[0][1]}" # Fall through to the generic search logic
        else:
            # It's a playlist
            embed = Embed(
                title=f"{platform_name} Playlist Processing",
                description="Starting...",
                color=0xFFB6C1 if is_kawaii else discord.Color.blue()
            )
            message = await interaction.followup.send(silent=SILENT_MESSAGES,embed=embed)

            total_tracks = len(tracks)
            processed = 0
            failed = 0
            failed_tracks_list = []
            
            tasks = [search_track(track) for track in tracks]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for i, result in enumerate(results):
                processed += 1
                if isinstance(result, dict) and result.get('url'):
                    await music_player.queue.put(result)
                else:
                    failed += 1
                    original_track = tracks[i]
                    if len(failed_tracks_list) < 5:
                        failed_tracks_list.append(f"{original_track[0]} by {original_track[1]}")
                
                if processed % 10 == 0 or processed == total_tracks:
                    progress = processed / total_tracks
                    bar = create_loading_bar(progress)
                    description = f"{bar}\n" + get_messages("loading_playlist", guild_id).format(
                        processed=processed,
                        total=total_tracks
                    )
                    embed.description = description
                    await message.edit(embed=embed)
            
            logger.info(f"{platform_name} playlist processed: {processed - failed} tracks added, {failed} failed")

            if processed - failed == 0:
                embed = Embed(
                    description="No tracks could be added from this playlist.",
                    color=0xFF9AA2 if is_kawaii else discord.Color.red()
                )
                await message.edit(embed=embed)
                return
            
            failed_text = "\nFailed tracks (up to 5):\n" + "\n".join([f"- {track}" for track in failed_tracks_list]) if failed_tracks_list else ""
            
            # Dynamically get the right message key
            platform_key = platform_name.lower().replace(" ", "_")
            title_key = f"{platform_key}_playlist_added"
            desc_key = f"{platform_key}_playlist_description"

            embed.title = get_messages(title_key, guild_id)
            embed.description = get_messages(desc_key, guild_id).format(
                count=processed - failed,
                failed=failed,
                failed_tracks=failed_text
            )
            embed.color = 0xB5EAD7 if is_kawaii else discord.Color.green()
            await message.edit(embed=embed)

    elif any(regex.match(query) for regex in [soundcloud_regex, youtube_regex, ytmusic_regex, bandcamp_regex]):
        try:
            ydl_opts_playlist = {
                "format": "bestaudio/best", "quiet": True, "no_warnings": True,
                "extract_flat": "in_playlist", "noplaylist": False, "no_color": True,
                "socket_timeout": 10,
            }
            info = await extract_info_async(ydl_opts_playlist, query)

            if "entries" in info: # It's a playlist
                tracks_to_add = []
                for entry in info.get('entries', []):
                    if entry and entry.get('url'):
                        tracks_to_add.append(entry)

                if not tracks_to_add:
                    raise ValueError("No playable tracks found in the playlist.")

                for track_entry in tracks_to_add:
                    await music_player.queue.put({
                        'url': track_entry['url'],
                        'title': track_entry.get('title', 'Unknown Title'),
                        'webpage_url': track_entry.get('webpage_url', track_entry['url']),
                        'is_single': False
                    })
                
                embed = Embed(
                    title=get_messages("playlist_added", guild_id),
                    description=get_messages("playlist_description", guild_id).format(count=len(tracks_to_add)),
                    color=0xE2F0CB if is_kawaii else discord.Color.green()
                )
                if tracks_to_add[0].get("thumbnail"):
                    embed.set_thumbnail(url=tracks_to_add[0]["thumbnail"])
                await interaction.followup.send(silent=SILENT_MESSAGES, embed=embed)
            
            else: # It's a single track
                video_url = info.get("webpage_url", info.get("url"))
                await music_player.queue.put({
                    'url': video_url,
                    'title': info.get('title', 'Unknown Title'),
                    'webpage_url': video_url,
                    'is_single': True,
                    'skip_now_playing': True
                })
                embed = Embed(
                    title=get_messages("song_added", guild_id),
                    description=f"[{info['title']}]({video_url})",
                    color=0xFFDAC1 if is_kawaii else discord.Color.blue()
                )
                if info.get("thumbnail"):
                    embed.set_thumbnail(url=info["thumbnail"])
                await interaction.followup.send(silent=SILENT_MESSAGES,embed=embed)

        except yt_dlp.utils.DownloadError as e:
            emoji, title_key, desc_key = parse_yt_dlp_error(str(e))
            embed = Embed(title=f'{emoji} {get_messages(title_key, guild_id)}', description=get_messages(desc_key, guild_id), color=0xFF9AA2 if is_kawaii else discord.Color.orange())
            github_url = "https://github.com/alan7383/playify/issues"
            embed.add_field(name=f'🤔 {get_messages("error_field_what_to_do", guild_id)}', value=get_messages("error_what_to_do_content", guild_id).format(github_link=github_url), inline=False)
            embed.add_field(name=f'📋 {get_messages("error_field_full_error", guild_id)}', value=f"```\n{str(e)}\n```", inline=False)
            await interaction.followup.send(silent=SILENT_MESSAGES,embed=embed, ephemeral=True)
            logger.warning(f"A user-facing download error occurred: {str(e).splitlines()[0]}")
        except Exception as e:
            embed = Embed(description=get_messages("video_error", guild_id), color=0xFF9AA2 if is_kawaii else discord.Color.red())
            await interaction.followup.send(silent=SILENT_MESSAGES,embed=embed, ephemeral=True)
            logger.error(f"Error processing direct URL/Playlist: {e}", exc_info=True)
    else: # Keyword search
        try:
            ydl_opts_full = {
                "format": "bestaudio/best", "quiet": True, "no_warnings": True,
                "noplaylist": True, "no_color": True, "socket_timeout": 10,
            }
            sanitized_query = sanitize_query(query)
            search_query = f"ytsearch:{sanitized_query}"
            info = await extract_info_async(ydl_opts_full, search_query)
            video = info["entries"][0] if "entries" in info and info["entries"] else None
            if not video:
                raise Exception("No results found")
            
            video_url = video.get("webpage_url", video.get("url"))
            video_title = video.get('title', 'Unknown Title')
            
            await music_player.queue.put({
                'url': video_url,
                'title': video_title,
                'webpage_url': video_url,
                'is_single': True,
                'skip_now_playing': True
            })

            embed = Embed(
                title=get_messages("song_added", guild_id),
                description=f"[{video_title}]({video_url})",
                color=0xB5EAD7 if is_kawaii else discord.Color.blue()
            )
            if video.get("thumbnail"):
                embed.set_thumbnail(url=video["thumbnail"])
            if is_kawaii:
                embed.set_footer(text="☆⌒(≧▽° )")
            await interaction.followup.send(silent=SILENT_MESSAGES,embed=embed)
        except Exception as e:
            embed = Embed(
                description=get_messages("search_error", guild_id),
                color=0xFF9AA2 if is_kawaii else discord.Color.red()
            )
            await interaction.followup.send(silent=SILENT_MESSAGES,embed=embed, ephemeral=True)
            logger.error(f"Error searching for {query}: {e}")

    if not music_player.voice_client.is_playing() and not music_player.voice_client.is_paused():
        music_player.current_task = asyncio.create_task(play_audio(guild_id))

# /queue command - Paginated, Optimized, English, and Kawaii-ready
@bot.tree.command(name="queue", description="Show the current song queue and status with pages.")
async def queue(interaction: discord.Interaction):
    # This command is now instantaneous because it doesn't make any web requests.
    await interaction.response.defer()
    guild_id = interaction.guild_id
    music_player = get_player(guild_id)

    # Convert the queue to a list to work with it.
    all_tracks = list(music_player.queue._queue)

    # Check if both queue and current song are empty
    if not all_tracks and not music_player.current_info:
        is_kawaii = get_mode(guild_id)
        embed = Embed(
            description=get_messages("queue_empty", guild_id),
            color=0xFF9AA2 if is_kawaii else discord.Color.red()
        )
        await interaction.followup.send(silent=SILENT_MESSAGES, embed=embed, ephemeral=True)
        return

    # Create the paginated view
    view = QueueView(interaction=interaction, tracks=all_tracks, items_per_page=5)

    # Set the initial state of the buttons
    view.update_button_states()

    # Create the embed for the first page
    initial_embed = await view.create_queue_embed()

    # Send the first page with the buttons
    await interaction.followup.send(embed=initial_embed, view=view, silent=SILENT_MESSAGES)

@bot.tree.command(name="clearqueue", description="Clear the current queue")
async def clear_queue(interaction: discord.Interaction):
    guild_id = interaction.guild_id
    is_kawaii = get_mode(guild_id)
    music_player = get_player(guild_id)

    while not music_player.queue.empty():
        music_player.queue.get_nowait()

    music_player.history.clear()

    embed = Embed(
        description=get_messages("clear_queue_success", guild_id),
        color=0xB5EAD7 if is_kawaii else discord.Color.green()
    )
    await interaction.response.send_message(silent=SILENT_MESSAGES,embed=embed)

@bot.tree.command(name="playnext", description="Add a song to play next")
@app_commands.describe(query="Link or title of the video/song to play next")
async def play_next(interaction: discord.Interaction, query: str):
    guild_id = interaction.guild_id
    is_kawaii = get_mode(guild_id)
    music_player = get_player(guild_id)

    if not interaction.user.voice or not interaction.user.voice.channel:
        embed = Embed(
            description=get_messages("no_voice_channel", guild_id),
            color=0xFF9AA2 if is_kawaii else discord.Color.red()
        )
        await interaction.response.send_message(silent=SILENT_MESSAGES,embed=embed, ephemeral=True)
        return

    if not music_player.voice_client or not music_player.voice_client.is_connected():
        try:
            music_player.voice_client = await interaction.user.voice.channel.connect()
        except Exception as e:
            embed = Embed(description=get_messages("connection_error", guild_id), color=0xFF9AA2 if is_kawaii else discord.Color.red())
            await interaction.response.send_message(silent=SILENT_MESSAGES,embed=embed, ephemeral=True)
            logger.error(f"Error: {e}")
            return

    music_player.text_channel = interaction.channel
    await interaction.response.defer()

    # Re-using regexes from the play command
    spotify_regex = re.compile(r'^(https?://)?(open\.spotify\.com)/.+$')
    deezer_regex = re.compile(r'^(https?://)?((www\.)?deezer\.com/(?:[a-z]{2}/)?(track|playlist|album|artist)/.+|(link\.deezer\.com)/s/.+)$')
    apple_music_regex = re.compile(r'^(https?://)?(music\.apple\.com)/.+$')
    tidal_regex = re.compile(r'^(https?://)?(www\.)?tidal\.com/.+$')
    amazon_music_regex = re.compile(r'^(https?://)?(music\.amazon\.(fr|com|co\.uk|de|es|it|jp))/.+$')

    try:
        search_query_for_yt = query
        tracks = None

        if spotify_regex.match(query): tracks = await process_spotify_url(query, interaction)
        elif deezer_regex.match(query): tracks = await process_deezer_url(query, interaction)
        elif apple_music_regex.match(query): tracks = await process_apple_music_url(query, interaction)
        elif tidal_regex.match(query): tracks = await process_tidal_url(query, interaction)
        elif amazon_music_regex.match(query): tracks = await process_amazon_music_url(query, interaction)
        
        if tracks:
            if len(tracks) > 1:
                await interaction.followup.send("Playlists and albums are not supported for `/playnext`.", ephemeral=True, silent=SILENT_MESSAGES)
                return
            search_query_for_yt = f"{tracks[0][0]} {tracks[0][1]}"

        ydl_opts = {
            "format": "bestaudio/best", "quiet": True, "no_warnings": True,
            "noplaylist": True, "no_color": True, "socket_timeout": 10,
        }
        yt_search_term = f"ytsearch:{sanitize_query(search_query_for_yt)}" if not search_query_for_yt.startswith(('http://', 'https://')) else search_query_for_yt

        info = await extract_info_async(ydl_opts, yt_search_term)
        video = info["entries"][0] if "entries" in info and info["entries"] else info
        
        video_url = video.get("webpage_url", video.get("url"))
        video_title = video.get('title', 'Unknown Title')
        
        if not video_url:
            raise KeyError("No valid URL found in video metadata")

        # OPTIMIZED: Prepare the full queue item
        queue_item = {
            'url': video_url,
            'title': video_title,
            'webpage_url': video_url,
            'is_single': True
        }

        new_queue = asyncio.Queue()
        await new_queue.put(queue_item)

        while not music_player.queue.empty():
            item = await music_player.queue.get()
            await new_queue.put(item)
        music_player.queue = new_queue

        embed = Embed(
            title=get_messages("play_next_added", guild_id),
            description=f"[{video_title}]({video_url})",
            color=0xC7CEEA if is_kawaii else discord.Color.blue()
        )
        if video.get("thumbnail"):
            embed.set_thumbnail(url=video["thumbnail"])
        if is_kawaii:
            embed.set_footer(text="☆⌒(≧▽° )")
        await interaction.followup.send(silent=SILENT_MESSAGES,embed=embed)

        if not music_player.voice_client.is_playing() and not music_player.voice_client.is_paused():
            music_player.current_task = asyncio.create_task(play_audio(guild_id))

    except Exception as e:
        embed = Embed(description=get_messages("search_error", guild_id), color=0xFF9AA2 if is_kawaii else discord.Color.red())
        await interaction.followup.send(silent=SILENT_MESSAGES,embed=embed, ephemeral=True)
        logger.error(f"Error processing /playnext for '{query}': {e}", exc_info=True)

# /nowplaying command
@bot.tree.command(name="nowplaying", description="Show the current song playing")
async def now_playing(interaction: discord.Interaction):
    guild_id = interaction.guild_id
    is_kawaii = get_mode(guild_id)
    music_player = get_player(guild_id)

    if music_player.current_info:
        title = music_player.current_info.get("title", "Unknown Title")
        url = music_player.current_info.get("webpage_url", music_player.current_url)
        thumbnail = music_player.current_info.get("thumbnail")

        embed = Embed(
            title=get_messages("now_playing_title", guild_id),
            description=get_messages("now_playing_description", guild_id).format(title=title, url=url),
            color=0xC7CEEA if is_kawaii else discord.Color.green()
        )
        if thumbnail:
            embed.set_thumbnail(url=thumbnail)
        await interaction.response.send_message(silent=SILENT_MESSAGES,embed=embed)
    else:
        embed = Embed(
            description=get_messages("no_song_playing", guild_id),
            color=0xFF9AA2 if is_kawaii else discord.Color.red()
        )
        await interaction.response.send_message(silent=SILENT_MESSAGES,embed=embed, ephemeral=True)

@bot.tree.command(name="filter", description="Applies or removes audio filters in real time.")
async def filter_command(interaction: discord.Interaction):
    guild_id = interaction.guild.id
    music_player = get_player(guild_id)
    is_kawaii = get_mode(guild_id)

    if not music_player.voice_client or not (music_player.voice_client.is_playing() or music_player.voice_client.is_paused()):
        embed = Embed(
            description=get_messages("no_filter_playback", guild_id),
            color=0xFF9AA2 if is_kawaii else discord.Color.red()
        )
        await interaction.response.send_message(silent=SILENT_MESSAGES,embed=embed, ephemeral=True)
        return

    view = FilterView(interaction)
    embed = Embed(
        title=get_messages("filter_title", guild_id),
        description=get_messages("filter_description", guild_id),
        color=0xB5EAD7 if is_kawaii else discord.Color.blue()
    )

    await interaction.response.send_message(silent=SILENT_MESSAGES,embed=embed, view=view)

# /pause command
@bot.tree.command(name="pause", description="Pause the current playback")
async def pause(interaction: discord.Interaction):
    guild_id = interaction.guild_id
    is_kawaii = get_mode(guild_id)
    music_player = get_player(guild_id)

    if music_player.voice_client and music_player.voice_client.is_playing():
        music_player.voice_client.pause()
        embed = Embed(
            description=get_messages("pause", guild_id),
            color=0xFFB7B2 if is_kawaii else discord.Color.orange()
        )
        await interaction.response.send_message(silent=SILENT_MESSAGES,embed=embed)
    else:
        embed = Embed(
            description=get_messages("no_playback", guild_id),
            color=0xFF9AA2 if is_kawaii else discord.Color.red()
        )
        await interaction.response.send_message(silent=SILENT_MESSAGES,embed=embed, ephemeral=True)

# /resume command
@bot.tree.command(name="resume", description="Resume the playback")
async def resume(interaction: discord.Interaction):
    guild_id = interaction.guild_id
    is_kawaii = get_mode(guild_id)
    music_player = get_player(guild_id)

    if music_player.voice_client and music_player.voice_client.is_paused():
        music_player.voice_client.resume()
        embed = Embed(
            description=get_messages("resume", guild_id),
            color=0xB5EAD7 if is_kawaii else discord.Color.green()
        )
        await interaction.response.send_message(silent=SILENT_MESSAGES,embed=embed)
    else:
        embed = Embed(
            description=get_messages("no_paused", guild_id),
            color=0xFF9AA2 if is_kawaii else discord.Color.red()
        )
        await interaction.response.send_message(silent=SILENT_MESSAGES,embed=embed, ephemeral=True)

# /skip command
@bot.tree.command(name="skip", description="Skip to the next song")
async def skip(interaction: discord.Interaction):
    guild_id = interaction.guild_id
    is_kawaii = get_mode(guild_id)
    music_player = get_player(guild_id)

    if music_player.lyrics_task and not music_player.lyrics_task.done():
        music_player.lyrics_task.cancel()

    if music_player.voice_client and music_player.voice_client.is_playing():
        music_player.start_time = 0
        music_player.playback_started_at = None
        music_player.voice_client.stop() 

        embed = Embed(
            description=get_messages("skip", guild_id),
            color=0xE2F0CB if is_kawaii else discord.Color.blue()
        )
        await interaction.response.send_message(silent=SILENT_MESSAGES,embed=embed)
    else:
        embed = Embed(
            description=get_messages("no_song", guild_id),
            color=0xFF9AA2 if is_kawaii else discord.Color.red()
        )
        await interaction.response.send_message(silent=SILENT_MESSAGES,embed=embed, ephemeral=True)

# /loop command
@bot.tree.command(name="loop", description="Enable/disable looping for the current song")
async def loop(interaction: discord.Interaction):
    await interaction.response.defer()
    guild_id = interaction.guild_id
    is_kawaii = get_mode(guild_id)
    music_player = get_player(guild_id)

    music_player.loop_current = not music_player.loop_current
    state = get_messages("loop_state_enabled", guild_id) if music_player.loop_current else get_messages("loop_state_disabled", guild_id)

    embed = Embed(
        description=get_messages("loop", guild_id).format(state=state),
        color=0xC7CEEA if is_kawaii else discord.Color.blue()
    )
    await interaction.followup.send(silent=SILENT_MESSAGES,embed=embed)

# /stop command
@bot.tree.command(name="stop", description="Stop playback and disconnect the bot")
async def stop(interaction: discord.Interaction):
    guild_id = interaction.guild_id
    is_kawaii = get_mode(guild_id)
    music_player = get_player(guild_id)

    if music_player.lyrics_task and not music_player.lyrics_task.done():
        music_player.lyrics_task.cancel()

    if music_player.voice_client:
        if music_player.current_task and not music_player.current_task.done():
            music_player.current_task.cancel()
        if music_player.voice_client.is_playing():
            music_player.voice_client.stop()
        
        while not music_player.queue.empty():
            music_player.queue.get_nowait()
        
        await music_player.voice_client.disconnect()
        music_players[guild_id] = MusicPlayer() # Reset player state

        embed = Embed(
            description=get_messages("stop", guild_id),
            color=0xFF9AA2 if is_kawaii else discord.Color.red()
        )
        await interaction.response.send_message(silent=SILENT_MESSAGES,embed=embed)
    else:
        embed = Embed(
            description=get_messages("not_connected", guild_id),
            color=0xFF9AA2 if is_kawaii else discord.Color.red()
        )
        await interaction.response.send_message(silent=SILENT_MESSAGES,embed=embed, ephemeral=True)

# /shuffle command
@bot.tree.command(name="shuffle", description="Shuffle the current queue")
async def shuffle(interaction: discord.Interaction):
    guild_id = interaction.guild_id
    is_kawaii = get_mode(guild_id)
    music_player = get_player(guild_id)

    if not music_player.queue.empty():
        items = list(music_player.queue._queue)
        random.shuffle(items)
        
        new_queue = asyncio.Queue()
        for item in items:
            await new_queue.put(item)
        music_player.queue = new_queue

        embed = Embed(
            description=get_messages("shuffle_success", guild_id),
            color=0xB5EAD7 if is_kawaii else discord.Color.green()
        )
        await interaction.response.send_message(silent=SILENT_MESSAGES,embed=embed)
    else:
        embed = Embed(
            description=get_messages("queue_empty", guild_id),
            color=0xFF9AA2 if is_kawaii else discord.Color.red()
        )
        await interaction.response.send_message(silent=SILENT_MESSAGES,embed=embed, ephemeral=True)

# /autoplay command
@bot.tree.command(name="autoplay", description="Enable/disable autoplay of similar songs")
async def toggle_autoplay(interaction: discord.Interaction):
    guild_id = interaction.guild_id
    is_kawaii = get_mode(guild_id)
    music_player = get_player(guild_id)

    music_player.autoplay_enabled = not music_player.autoplay_enabled
    state = get_messages("autoplay_state_enabled", guild_id) if music_player.autoplay_enabled else get_messages("autoplay_state_disabled", guild_id)

    embed = Embed(
        description=get_messages("autoplay_toggle", guild_id).format(state=state),
        color=0xC7CEEA if is_kawaii else discord.Color.blue()
    )
    await interaction.response.send_message(silent=SILENT_MESSAGES,embed=embed)
    
# /status command
@bot.tree.command(name="status", description="Displays the bot's full performance and diagnostic stats.")
async def status(interaction: discord.Interaction):
    def format_bytes(size):
        if size == 0: return "0B"
        size_name = ("B", "KB", "MB", "GB", "TB")
        i = int(math.floor(math.log(size, 1024)))
        p = math.pow(1024, i)
        s = round(size / p, 2)
        return f"{s} {size_name[i]}"

    await interaction.response.defer(ephemeral=True)

    bot_process = psutil.Process()
    latency = round(bot.latency * 1000)
    server_count = len(bot.guilds)
    user_count = sum(guild.member_count for guild in bot.guilds)
    uptime_seconds = int(round(time.time() - bot.start_time))
    uptime_string = str(datetime.timedelta(seconds=uptime_seconds))

    active_players = len([p for p in music_players.values() if p.voice_client and p.voice_client.is_connected()])
    total_queued_songs = sum(p.queue.qsize() for p in music_players.values())
    ffmpeg_processes = sum(1 for child in bot_process.children(recursive=True) if child.name().lower() == 'ffmpeg')

    ram_info = psutil.virtual_memory()
    bot_ram_usage = format_bytes(bot_process.memory_info().rss)

    python_version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    discord_py_version = discord.__version__
    yt_dlp_version = yt_dlp.version.__version__

    embed = discord.Embed(
        title=f"Playify's Dashboard",
        description=f"Full operational status of the bot.",
        color=0x2ECC71 if latency < 200 else (0xE67E22 if latency < 500 else 0xE74C3C)
    )
    embed.set_thumbnail(url=bot.user.avatar.url)
    embed.add_field(
        name="📊 Bot",
        value=f"**Latency:** {latency} ms\n**Servers:** {server_count}\n**Users:** {user_count}\n**Uptime:** {uptime_string}",
        inline=True
    )
    embed.add_field(
        name="🎧 Music",
        value=f"**Active Players:** {active_players}\n**Queued Songs:** {total_queued_songs}\n**FFmpeg Procs:** `{ffmpeg_processes}`\n**URL Cache:** {url_cache.currsize}/{url_cache.maxsize}",
        inline=True
    )
    embed.add_field(
        name="⚙️ Environment",
        value=f"**Python:** v{python_version}\n**Discord.py:** v{discord_py_version}\n**yt-dlp:** v{yt_dlp_version}\n**Bot RAM:** {bot_ram_usage}",
        inline=True
    )
    embed.set_footer(text=f"Data requested by {interaction.user.display_name}")
    embed.timestamp = datetime.datetime.now(datetime.timezone.utc)
    await interaction.followup.send(silent=SILENT_MESSAGES,embed=embed)
    
# /discord command
@bot.tree.command(name="discord", description="Get an invite to the official community and support server.")
async def discord_command(interaction: discord.Interaction):
    guild_id = interaction.guild_id
    is_kawaii = get_mode(guild_id)
    
    embed = Embed(
        title=get_messages("discord_command_title", guild_id),
        description=get_messages("discord_command_description", guild_id),
        color=0xFFB6C1 if is_kawaii else discord.Color.blue()
    )
    view = View()
    button = Button(
        label=get_messages("discord_command_button", guild_id),
        style=discord.ButtonStyle.link,
        url="https://discord.gg/JeH8g6g3cG"
    )
    view.add_item(button)
    await interaction.response.send_message(silent=SILENT_MESSAGES,embed=embed, view=view)    

@bot.tree.command(name="24_7", description="Enable or disable 24/7 mode.")
@app_commands.describe(mode="Choose the mode: auto (adds songs), normal (loops the queue), or off.")
@app_commands.choices(mode=[
    Choice(name="Normal (Loops the current queue)", value="normal"),
    Choice(name="Auto (Adds similar songs when the queue is empty)", value="auto"),
    Choice(name="Off (Disable 24/7 mode)", value="off")
])
async def radio_24_7(interaction: discord.Interaction, mode: str):
    guild_id = interaction.guild_id
    is_kawaii = get_mode(guild_id)
    music_player = get_player(guild_id)

    await interaction.response.defer(thinking=True)

    if mode == "off":
        if not _24_7_active.get(guild_id, False):
            await interaction.followup.send("24/7 mode was not active.", silent=SILENT_MESSAGES, ephemeral=True)
            return
        
        _24_7_active[guild_id] = False
        music_player.autoplay_enabled = False 
        music_player.loop_current = False
        music_player.history.clear()
        
        embed = Embed(
            title=get_messages("24_7_off_title", guild_id),
            description=get_messages("24_7_off_desc", guild_id),
            color=0xFF9AA2 if is_kawaii else discord.Color.red()
        )
        await interaction.followup.send(embed=embed, silent=SILENT_MESSAGES)
        return

    if not music_player.voice_client or not music_player.voice_client.is_connected():
        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.followup.send("You are not in a voice channel.", silent=SILENT_MESSAGES, ephemeral=True)
            return
        try:
            music_player.voice_client = await interaction.user.voice.channel.connect()
        except Exception as e:
            logger.error(f"Failed to connect for 24/7 command: {e}")
            await interaction.followup.send("Could not connect to the voice channel.", silent=SILENT_MESSAGES, ephemeral=True)
            return

    _24_7_active[guild_id] = True
    music_player.loop_current = False

    if mode == "auto":
        music_player.autoplay_enabled = True
        embed = Embed(
            title=get_messages("24_7_auto_title", guild_id),
            description=get_messages("24_7_auto_desc", guild_id),
            color=0xB5EAD7 if is_kawaii else discord.Color.green()
        )
    else: # mode == "normal"
        music_player.autoplay_enabled = False
        embed = Embed(
            title=get_messages("24_7_normal_title", guild_id),
            description=get_messages("24_7_normal_desc", guild_id),
            color=0xB5EAD7 if is_kawaii else discord.Color.green()
        )
    
    if not music_player.voice_client.is_playing() and not music_player.voice_client.is_paused():
        music_player.current_task = asyncio.create_task(play_audio(guild_id))

    await interaction.followup.send(embed=embed, silent=SILENT_MESSAGES)

# --- FINAL & ROBUST /reconnect COMMAND v4 ---
@bot.tree.command(name="reconnect", description="Forces the bot to reconnect, resuming playback at the exact same spot.")
async def reconnect(interaction: discord.Interaction):
    guild_id = interaction.guild_id
    is_kawaii = get_mode(guild_id)
    music_player = get_player(guild_id)

    if not music_player.voice_client or not music_player.voice_client.is_connected():
        embed = Embed(description=get_messages("not_connected", guild_id), color=0xFF9AA2 if is_kawaii else discord.Color.red())
        await interaction.response.send_message(embed=embed, ephemeral=True, silent=SILENT_MESSAGES)
        return

    await interaction.response.defer()

    current_channel = music_player.voice_client.channel
    
    resume_position = 0
    was_playing = music_player.voice_client.is_playing() or music_player.voice_client.is_paused()
    track_info_to_resume = music_player.current_info
    
    if was_playing and track_info_to_resume:
        if music_player.playback_started_at:
            elapsed_time = time.time() - music_player.playback_started_at
            resume_position = music_player.start_time + (elapsed_time * music_player.playback_speed)

    # --- THIS IS THE KILL SWITCH SETUP ---
    reconnecting_guilds.add(guild_id)
    
    try:
        # Force stop and disconnect, which will trigger the old 'after_playing'
        # But the kill switch will prevent it from doing anything.
        await music_player.voice_client.disconnect(force=True)
        await asyncio.sleep(1) 
        
        new_vc = await current_channel.connect()
        music_player.voice_client = new_vc
        
        # Restore the player's state
        music_player.current_info = track_info_to_resume
        if track_info_to_resume:
            music_player.current_url = track_info_to_resume.get('webpage_url', track_info_to_resume.get('url'))

        logger.info(f"Bot reconnected successfully in guild {guild_id}.")
        embed = Embed(
            description=get_messages("reconnect_success", guild_id),
            color=0xB5EAD7 if is_kawaii else discord.Color.green()
        )
        await interaction.followup.send(embed=embed, silent=SILENT_MESSAGES)

        # Now, safely start a new playback task.
        if was_playing:
            music_player.current_task = asyncio.create_task(play_audio(guild_id, seek_time=resume_position))

    except Exception as e:
        logger.error(f"Failed to reconnect in guild {guild_id}: {e}", exc_info=True)
        embed = Embed(description=get_messages("reconnect_fail", guild_id), color=0xFF9AA2 if is_kawaii else discord.Color.red())
        await interaction.followup.send(embed=embed, ephemeral=True, silent=SILENT_MESSAGES)
    finally:
        # CRUCIAL: Always remove the kill switch
        reconnecting_guilds.discard(guild_id)
        
# ==============================================================================
# 6. DISCORD EVENTS
# ==============================================================================

@bot.event
async def on_voice_state_update(member, before, after):
    """
    Comprehensive voice state event handler.
    - Pauses/resumes music if the channel empties/re-populates.
    - Handles automatic disconnection (except in 24/7 mode).
    - Cleans up and resets the player when the bot is disconnected.
    """
    guild = member.guild

    # Do nothing if the bot isn't in a voice channel on this server
    if not guild.voice_client:
        return

    guild_id = guild.id
    vc = guild.voice_client
    bot_channel = vc.channel

    # CASE 1: The bot is disconnected (by a user, /stop, or inactivity)
    if member.id == bot.user.id and before.channel is not None and after.channel is None:
        # --- MODIFIED PART ---
        # If the bot is just reconnecting, do not perform cleanup.
        if guild_id in reconnecting_guilds:
            logger.info(f"Bot is reconnecting in guild {guild_id}. Skipping cleanup.")
            return
        # --- END OF MODIFICATION ---
        
        logger.info(f"Bot was disconnected from guild {guild_id}. Triggering full cleanup.")
        music_player = get_player(guild_id)
        if music_player.current_task and not music_player.current_task.done():
            music_player.current_task.cancel()
        
        # Full reset of the player for this guild
        music_players[guild_id] = MusicPlayer()
        # Ensure filters and other states are also reset if needed
        if guild_id in server_filters:
            del server_filters[guild_id]
        if guild_id in _24_7_active:
            del _24_7_active[guild_id]
        
        logger.info(f"Player for guild {guild_id} has been reset.")
        return

    # CASE 2: A user leaves or the bot is left alone
    if before.channel == bot_channel and after.channel != bot_channel or (member.id != bot.user.id and after.channel == bot_channel):
        humans_in_channel = [m for m in bot_channel.members if not m.bot]
        
        if not humans_in_channel:
            if vc.is_playing():
                logger.info(f"Bot is now alone in channel for guild {guild_id}. Pausing.")
                vc.pause()
            
            if not _24_7_active.get(guild_id, False):
                await asyncio.sleep(60)
                if vc.is_connected() and not any(m for m in vc.channel.members if not m.bot):
                    logger.info(f"Disconnecting from guild {guild_id} after 60s of inactivity.")
                    await vc.disconnect()
        
        # CASE 3: A user joined and the bot was paused
        elif vc.is_paused():
            logger.info(f"A user is present in guild {guild_id}. Resuming playback.")
            vc.resume()

@bot.event
async def on_ready():
    logger.info(f"{bot.user.name} is online.")
    try:
        synced = await bot.tree.sync()
        logger.info(f"Synced {len(synced)} slash commands.")

        async def rotate_presence():
            while True:
                if not bot.is_ready() or bot.is_closed():
                    return

                statuses = [
                    ("/karaoke & /lyrics", discord.ActivityType.listening),
                    ("/play [link] ", discord.ActivityType.listening),
                    (f"{len(bot.guilds)} servers", discord.ActivityType.playing)
                ]

                for status_text, status_type in statuses:
                    try:
                        await bot.change_presence(
                            activity=discord.Activity(
                                name=status_text,
                                type=status_type
                            )
                        )
                        await asyncio.sleep(10)
                    except Exception as e:
                        logger.error(f"Error changing presence: {e}")
                        await asyncio.sleep(5)

        bot.loop.create_task(rotate_presence())

    except Exception as e:
        logger.error(f"Error during command synchronization: {e}")

# ==============================================================================
# 7. BOT INITIALIZATION & RUN
# ==============================================================================

bot.start_time = time.time()
bot.run(os.getenv("DISCORD_TOKEN"))
