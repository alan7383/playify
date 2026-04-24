"""Shared helper functions for embeds, formatting, and localized messages."""

from ..core import *
from ..models.lazy_search import LazySearchItem

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
        # PCMVolumeTransformer wraps FFmpegPCMAudio in `original`, so unwrap first.
        source = vc.source
        while hasattr(source, "original"):
            source = source.original

        process = getattr(source, "_process", None) or getattr(source, "process", None)
        if process and process.poll() is None:
            try:
                process.kill()
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
