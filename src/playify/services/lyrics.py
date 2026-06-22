"""Lyrics fetching services."""

import aiohttp
import urllib.parse
import re
from ..core import *
from ..helpers.common import *

async def fetch_lrclib(query: str, needs_synced: bool = False) -> str | None:
    """
    Search for lyrics on LRCLIB.
    Returns a string (LRC or plain text) or None if nothing is found.
    """
    url = f"https://lrclib.net/api/search?q={urllib.parse.quote(query)}"
    headers = {
        "User-Agent": "PlayifyBot/2.0 (Discord Music Bot - https://github.com/alan7383/playify)"
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=15.0) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if not data:
                        return None

                    for track in data:
                        if needs_synced and track.get("syncedLyrics"):
                            return track["syncedLyrics"]
                        elif not needs_synced:
                            lyrics = track.get("plainLyrics") or track.get("syncedLyrics")
                            if lyrics:
                                return lyrics
    except asyncio.TimeoutError:
        logger.warning(f"LRCLIB fetch timed out for '{query}'")
    except Exception as e:
        logger.warning(f"LRCLIB fetch failed for '{query}': {type(e).__name__} - {e}")

    return None


async def fetch_and_display_lyrics(
    interaction: discord.Interaction, fallback_message: str = None
):
    """Fetches, formats, and displays lyrics using LRCLIB first, then Genius."""
    from ..ui.interactions import LyricsRetryView, LyricsView
    guild_id = interaction.guild_id
    state = get_guild_state(guild_id)
    music_player = state.music_player
    is_kawaii = state.locale == Locale.EN_X_KAWAII
    loop = asyncio.get_running_loop()

    clean_title, artist_name = get_cleaned_song_info(
        music_player.current_info, guild_id
    )
    precise_query = f"{clean_title} {artist_name}"

    lyrics = None
    song_url = None
    song_title = clean_title

    try:
        logger.info(f"Attempting LRCLIB API search for plain lyrics: '{precise_query}'")
        raw_lrclib = await fetch_lrclib(precise_query, needs_synced=False)

        if raw_lrclib:
            lyrics = re.sub(r"\[\d{2}:\d{2}\.\d{2,3}\]", "", raw_lrclib)
            lyrics = re.sub(r"\n\s*\n", "\n\n", lyrics).strip()
            song_url = (
                f"https://lrclib.net/search?q={urllib.parse.quote(precise_query)}"
            )

        elif genius:
            logger.info("LRCLIB failed. Attempting authenticated Genius API search.")
            search_result = await loop.run_in_executor(
                None, lambda: genius.search_songs(precise_query, per_page=5)
            )

            song_info = None
            if search_result and search_result.get("hits"):
                song_info = search_result["hits"][0]["result"]

            if song_info:
                song_object = await loop.run_in_executor(
                    None, lambda: genius.search_song(song_id=song_info["id"])
                )
                if song_object and song_object.lyrics:
                    raw_lyrics = song_object.lyrics
                    lines = raw_lyrics.split("\n")
                    cleaned_lines = [
                        line
                        for line in lines
                        if "contributor" not in line.lower()
                        and "lyrics" not in line.lower()
                        and "embed" not in line.lower()
                    ]
                    lyrics = "\n".join(cleaned_lines).strip()
                    song_url = song_object.url
                    song_title = song_object.title

        if not lyrics:
            error_title = get_messages("lyrics.error.not_found.title", guild_id)
            error_desc = get_messages(
                "lyrics.error.not_found.description", guild_id, query=precise_query
            )
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
            title=get_messages("lyrics.embed.title", guild_id, title=song_title),
            url=song_url,
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
        logger.error(f"Error in lyrics fetch for '{precise_query}': {e}", exc_info=True)
        if not interaction.is_expired():
            await interaction.followup.send(
                get_messages("api.lyrics.generic_fetch_error", interaction.guild_id),
                silent=SILENT_MESSAGES,
                ephemeral=True,
            )
