"""Interactive Discord views and modals beyond the main controller."""

from ..core import *
from ..helpers.common import *
from ..services.playback import play_audio, update_karaoke_task
from ..services.voice import fetch_video_info_with_retry, fetch_meta, safe_stop
from .controller import update_controller

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
