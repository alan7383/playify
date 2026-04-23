"""Core playback engine and karaoke sync tasks."""

from ..core import *
from ..helpers.common import *
from ..helpers.url_utils import *
from ..models.lazy_search import LazySearchItem
from ..services.voice import fetch_video_info_with_retry, run_ydl_with_low_priority
from ..ui.controller import update_controller

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
