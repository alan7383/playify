"""Discord event handlers and runtime hooks."""

from .core import *
from .helpers.common import *
from .services.playback import play_audio
from .services.voice import clear_audio_cache, play_silence_loop, safe_stop
from .ui.controller import MusicControllerView

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

                await safe_stop(vc)

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
                        await safe_stop(vc)
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
