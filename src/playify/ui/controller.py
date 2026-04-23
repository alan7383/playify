"""Controller views and embeds for the Playify UI."""

from ..core import *
from ..helpers.common import *
from ..models.lazy_search import LazySearchItem
from ..services.voice import clear_audio_cache, fetch_meta, safe_stop

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
