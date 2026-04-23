"""Lyrics fetching services."""

from ..core import *
from ..helpers.common import *
from ..ui.interactions import LyricsRetryView, LyricsView

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
