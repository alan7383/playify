"""Voice connection and yt-dlp integration helpers."""

from ..core import *

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
        "source_address": "0.0.0.0",  # Force IPv4 to bypass VPS bot detection
        "extractor_args": {"youtube": ["player_client=android,web"]},
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
        cookies_file_to_use = str(PROJECT_ROOT / specific_cookie_file)
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
    from .playback import play_audio

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
        "source_address": "0.0.0.0",
        "extractor_args": {"youtube": ["player_client=android,web"]},
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
