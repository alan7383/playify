"""Music and playback slash commands."""

from ..core import *
from ..helpers.common import *
from ..helpers.url_utils import *
from ..models.lazy_search import LazySearchItem
from ..services.lyrics import fetch_and_display_genius_lyrics
from ..services.platforms import *
from ..services.playback import *
from ..services.voice import *
from ..ui.controller import *
from ..ui.interactions import *

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

        # 1. Stop playback and kill the underlying FFmpeg process cleanly.
        await safe_stop(vc)

        # 2. We cancel the main playback task if it is active.
        if music_player.current_task and not music_player.current_task.done():
            music_player.current_task.cancel()

        # 3. NOW, we can disconnect safely.
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
