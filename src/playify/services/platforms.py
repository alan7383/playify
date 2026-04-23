"""Platform-specific URL processors for music services."""

from ..core import *
from ..helpers.common import *
from ..models.lazy_search import LazySearchItem

async def process_spotify_url(url, interaction):
    """
    Processes a Spotify URL with a cascade architecture:
    1. Tries with the official API (spotipy) for speed and completeness.
    2. On failure (e.g., editorial playlist), falls back to the scraper (spotifyscraper).
    """
    guild_id = interaction.guild.id
    state = get_guild_state(guild_id)
    is_kawaii = state.locale == Locale.EN_X_KAWAII
    clean_url = url.split("?")[0]

    # --- METHOD 1: OFFICIAL API (SPOTIPY) ---
    if sp:
        try:
            logger.info(f"Attempt 1: Official API (Spotipy) for {clean_url}")
            tracks_to_return = []
            loop = asyncio.get_event_loop()

            if "playlist" in clean_url:
                results = await loop.run_in_executor(
                    None,
                    lambda: sp.playlist_items(
                        clean_url,
                        fields="items.track.name,items.track.artists.name,next",
                        limit=100,
                    ),
                )
                while results:
                    for item in results["items"]:
                        if item and item.get("track"):
                            track = item["track"]
                            tracks_to_return.append(
                                (track["name"], track["artists"][0]["name"])
                            )
                    if results["next"]:
                        results = await loop.run_in_executor(
                            None, lambda: sp.next(results)
                        )
                    else:
                        results = None

            elif "album" in clean_url:
                results = await loop.run_in_executor(
                    None, lambda: sp.album_tracks(clean_url, limit=50)
                )
                while results:
                    for track in results["items"]:
                        tracks_to_return.append(
                            (track["name"], track["artists"][0]["name"])
                        )
                    if results["next"]:
                        results = await loop.run_in_executor(
                            None, lambda: sp.next(results)
                        )
                    else:
                        results = None

            elif "track" in clean_url:
                track = await loop.run_in_executor(None, lambda: sp.track(clean_url))
                tracks_to_return.append((track["name"], track["artists"][0]["name"]))

            elif "artist" in clean_url:
                results = await loop.run_in_executor(
                    None, lambda: sp.artist_top_tracks(clean_url)
                )
                for track in results["tracks"]:
                    tracks_to_return.append(
                        (track["name"], track["artists"][0]["name"])
                    )

            if not tracks_to_return:
                raise ValueError("No tracks found via API.")

            logger.info(
                f"Success with Spotipy: {len(tracks_to_return)} tracks retrieved."
            )
            return tracks_to_return

        except Exception as e:
            logger.warning(
                f"Spotipy API failed for {clean_url} (Reason: {e}). Switching to plan B: SpotifyScraper."
            )

    # --- METHOD 2: FALLBACK (SPOTIFYSCRAPER) ---
    if spotify_scraper_client:
        try:
            logger.info(f"Attempt 2: Scraper (SpotifyScraper) for {clean_url}")
            tracks_to_return = []
            loop = asyncio.get_event_loop()

            if "playlist" in clean_url:
                data = await loop.run_in_executor(
                    None, lambda: spotify_scraper_client.get_playlist_info(clean_url)
                )
                for track in data.get("tracks", []):
                    tracks_to_return.append(
                        (
                            track.get("name", "Unknown Title"),
                            track.get("artists", [{}])[0].get("name", "Unknown Artist"),
                        )
                    )

            elif "album" in clean_url:
                data = await loop.run_in_executor(
                    None, lambda: spotify_scraper_client.get_album_info(clean_url)
                )
                for track in data.get("tracks", []):
                    tracks_to_return.append(
                        (
                            track.get("name", "Unknown Title"),
                            track.get("artists", [{}])[0].get("name", "Unknown Artist"),
                        )
                    )

            elif "track" in clean_url:
                data = await loop.run_in_executor(
                    None, lambda: spotify_scraper_client.get_track_info(clean_url)
                )
                tracks_to_return.append(
                    (
                        data.get("name", "Unknown Title"),
                        data.get("artists", [{}])[0].get("name", "Unknown Artist"),
                    )
                )

            if not tracks_to_return:
                raise SpotifyScraperError(
                    "The scraper could not find any tracks either."
                )

            logger.info(
                f"Success with SpotifyScraper: {len(tracks_to_return)} tracks retrieved (potentially limited)."
            )
            return tracks_to_return

        # --- THIS IS THE CORRECTED ERROR HANDLING BLOCK ---
        except (SpotifyScraperError, spotipy.exceptions.SpotifyException) as e:
            logger.error(
                f"Both methods (API and Scraper) failed. Final error: {e}",
                exc_info=True,
            )

            embed = Embed(
                title=get_messages("spotify_error_title", guild_id),
                description=get_messages(
                    "spotify_error_description_detailed", guild_id
                ),
                color=0xFF9AA2 if is_kawaii else discord.Color.red(),
            )
            await interaction.followup.send(
                silent=SILENT_MESSAGES, embed=embed, ephemeral=True
            )
            return None
        # --- END OF CORRECTION ---
        except Exception as e:  # General fallback for any other unexpected errors
            logger.error(
                f"An unexpected error occurred in the Spotify fallback: {e}",
                exc_info=True,
            )
            embed = Embed(
                description=get_messages("spotify_error", guild_id),
                color=0xFFB6C1 if is_kawaii else discord.Color.red(),
            )
            await interaction.followup.send(
                silent=SILENT_MESSAGES, embed=embed, ephemeral=True
            )
            return None

    logger.critical("No client (Spotipy or SpotifyScraper) is functional.")
    embed = Embed(
        description=get_messages("api.spotify.unreachable", guild_id),
        color=discord.Color.dark_red(),
    )
    await interaction.followup.send(silent=SILENT_MESSAGES, embed=embed, ephemeral=True)
    return None


# Process Deezer URLs
async def process_deezer_url(url, interaction):
    guild_id = interaction.guild_id
    try:
        deezer_share_regex = re.compile(r"^(https?://)?(link\.deezer\.com)/s/.+$")
        if deezer_share_regex.match(url):
            logger.info(f"Detected Deezer share link: {url}. Resolving redirect...")
            response = requests.head(url, allow_redirects=True, timeout=10)
            response.raise_for_status()
            resolved_url = response.url
            logger.info(f"Resolved to: {resolved_url}")
            url = resolved_url

        parsed_url = urlparse(url)
        path_parts = parsed_url.path.strip("/").split("/")
        if len(path_parts) > 1 and len(path_parts[0]) == 2:
            path_parts = path_parts[1:]
        if len(path_parts) < 2:
            raise ValueError("Invalid Deezer URL format")

        resource_type = path_parts[0]
        resource_id = path_parts[1].split("?")[0]

        base_api_url = "https://api.deezer.com"
        logger.info(
            f"Fetching Deezer {resource_type} with ID {resource_id} from URL {url}"
        )

        tracks = []
        if resource_type == "track":
            response = requests.get(f"{base_api_url}/track/{resource_id}", timeout=10)
            response.raise_for_status()
            data = response.json()
            if "error" in data:
                raise Exception(f"Deezer API error: {data['error']['message']}")
            logger.info(
                f"Processing Deezer track: {data.get('title', 'Unknown Title')}"
            )
            track_name = data.get("title", "Unknown Title")
            artist_name = data.get("artist", {}).get("name", "Unknown Artist")
            tracks.append((track_name, artist_name))

        elif resource_type == "playlist":
            next_url = f"{base_api_url}/playlist/{resource_id}/tracks"
            total_tracks = 0
            fetched_tracks = 0

            while next_url:
                response = requests.get(next_url, timeout=10)
                response.raise_for_status()
                data = response.json()

                if "error" in data:
                    raise Exception(f"Deezer API error: {data['error']['message']}")

                if not data.get("data"):
                    raise ValueError(
                        "No tracks found in the playlist or playlist is empty"
                    )

                for track in data["data"]:
                    track_name = track.get("title", "Unknown Title")
                    artist_name = track.get("artist", {}).get("name", "Unknown Artist")
                    tracks.append((track_name, artist_name))

                fetched_tracks += len(data["data"])
                total_tracks = data.get("total", fetched_tracks)
                logger.info(
                    f"Fetched {fetched_tracks}/{total_tracks} tracks from playlist {resource_id}"
                )

                next_url = data.get("next")
                if next_url:
                    logger.info(f"Fetching next page: {next_url}")

            logger.info(
                f"Processing Deezer playlist: {data.get('title', 'Unknown Playlist')} with {len(tracks)} tracks"
            )

        elif resource_type == "album":
            response = requests.get(
                f"{base_api_url}/album/{resource_id}/tracks", timeout=10
            )
            response.raise_for_status()
            data = response.json()
            if "error" in data:
                raise Exception(f"Deezer API error: {data['error']['message']}")
            if not data.get("data"):
                raise ValueError("No tracks found in the album or album is empty")
            logger.info(
                f"Processing Deezer album: {data.get('title', 'Unknown Album')}"
            )
            for track in data["data"]:
                track_name = track.get("title", "Unknown Title")
                artist_name = track.get("artist", {}).get("name", "Unknown Artist")
                tracks.append((track_name, artist_name))
            logger.info(f"Extracted {len(tracks)} tracks from album {resource_id}")

        elif resource_type == "artist":
            response = requests.get(
                f"{base_api_url}/artist/{resource_id}/top?limit=10", timeout=10
            )
            response.raise_for_status()
            data = response.json()
            if "error" in data:
                raise Exception(f"Deezer API error: {data['error']['message']}")
            if not data.get("data"):
                raise ValueError("No top tracks found for the artist")
            logger.info(
                f"Processing Deezer artist: {data.get('name', 'Unknown Artist')}"
            )
            for track in data["data"]:
                track_name = track.get("title", "Unknown Title")
                artist_name = track.get("artist", {}).get("name", "Unknown Artist")
                tracks.append((track_name, artist_name))
            logger.info(f"Extracted {len(tracks)} top tracks for artist {resource_id}")

        if not tracks:
            raise ValueError("No valid tracks found in the Deezer resource")

        logger.info(
            f"Successfully processed Deezer {resource_type} with {len(tracks)} tracks"
        )
        return tracks

    except requests.exceptions.RequestException as e:
        logger.error(f"Network error fetching Deezer URL {url}: {e}")
        embed = Embed(
            description=get_messages("api.deezer.network_error", guild_id),
            color=(
                0xFFB6C1
                if (get_guild_state(guild_id).locale == Locale.EN_X_KAWAII)
                else discord.Color.red()
            ),
        )
        await interaction.followup.send(
            silent=SILENT_MESSAGES, embed=embed, ephemeral=True
        )
        return None
    except ValueError as e:
        logger.error(f"Invalid Deezer data for URL {url}: {e}")
        embed = Embed(
            description=get_messages(
                "api.deezer.value_error", guild_id, error_message=str(e)
            ),
            color=(
                0xFFB6C1
                if (get_guild_state(guild_id).locale == Locale.EN_X_KAWAII)
                else discord.Color.red()
            ),
        )
        await interaction.followup.send(
            silent=SILENT_MESSAGES, embed=embed, ephemeral=True
        )
        return None
    except Exception as e:
        logger.error(f"Unexpected error processing Deezer URL {url}: {e}")
        embed = Embed(
            description=get_messages("deezer_error", guild_id),
            color=(
                0xFFB6C1
                if (get_guild_state(guild_id).locale == Locale.EN_X_KAWAII)
                else discord.Color.red()
            ),
        )
        await interaction.followup.send(
            silent=SILENT_MESSAGES, embed=embed, ephemeral=True
        )
        return None


# Process Apple Music URLs
async def process_apple_music_url(url, interaction):
    guild_id = interaction.guild.id
    logger.info(f"Starting processing for Apple Music URL: {url}")

    clean_url = url.split("?")[0]
    browser = None

    try:
        async with async_playwright() as p:
            browser = await p.firefox.launch(headless=True)
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/115.0"
            )
            page = await context.new_page()

            await page.route(
                "**/*.{png,jpg,jpeg,svg,woff,woff2}", lambda route: route.abort()
            )
            logger.info("Optimization: Disabled loading of images and fonts.")

            logger.info("Navigating to the page with a 90 second timeout...")
            await page.goto(clean_url, wait_until="domcontentloaded", timeout=90000)
            logger.info("Page loaded. Extracting data...")

            tracks = []
            resource_type = "unknown"
            path_parts = urlparse(clean_url).path.strip("/").split("/")

            if len(path_parts) > 1:
                if path_parts[1] in ["album", "playlist"]:
                    resource_type = path_parts[1]
                elif path_parts[1] == "song":
                    resource_type = "song"

            logger.info(f"Detected resource type: {resource_type}")

            if resource_type in ["album", "playlist"]:
                logger.info(
                    f"Processing as {resource_type}, using row scraping method."
                )
                await page.wait_for_selector("div.songs-list-row", timeout=20000)
                main_artist_name = "Unknown Artist"
                try:
                    main_artist_el = await page.query_selector(".headings__subtitles a")
                    if main_artist_el:
                        main_artist_name = await main_artist_el.inner_text()
                except Exception:
                    logger.warning(
                        "Could not determine the main artist for the collection."
                    )

                track_rows = await page.query_selector_all("div.songs-list-row")
                for row in track_rows:
                    try:
                        title_el = await row.query_selector(
                            "div.songs-list-row__song-name"
                        )
                        title = (
                            await title_el.inner_text() if title_el else "Unknown Title"
                        )

                        artist_elements = await row.query_selector_all(
                            "div.songs-list-row__by-line a"
                        )
                        if artist_elements:
                            artist_names = [
                                await el.inner_text() for el in artist_elements
                            ]
                            artist = " & ".join(artist_names)
                        else:
                            artist = main_artist_name

                        if title != "Unknown Title":
                            tracks.append((title.strip(), artist.strip()))
                    except Exception as e:
                        logger.warning(f"Failed to extract a track row: {e}")

            elif resource_type == "song":
                logger.info("Processing as single song, using JSON-LD method.")
                try:
                    json_ld_selector = 'script[id="schema:song"]'
                    await page.wait_for_selector(json_ld_selector, timeout=15000)

                    json_ld_content = await page.locator(json_ld_selector).inner_text()
                    data = json.loads(json_ld_content)

                    title = data["audio"]["name"]
                    artist = data["audio"]["byArtist"][0]["name"]

                    if title and artist:
                        logger.info(
                            f"Successfully extracted from JSON-LD: '{title}' by '{artist}'"
                        )
                        tracks.append((title.strip(), artist.strip()))
                    else:
                        raise ValueError("JSON-LD data is missing name or artist.")
                except Exception as e:
                    logger.warning(
                        f"JSON-LD method failed ({e}). Falling back to HTML element scraping."
                    )
                    title_selector = 'h1[data-testid="song-title"]'
                    artist_selector = 'span[data-testid="song-subtitle-artists"] a'
                    await page.wait_for_selector(title_selector, timeout=10000)

                    title = await page.locator(title_selector).first.inner_text()
                    artist = await page.locator(artist_selector).first.inner_text()

                    if title and artist:
                        logger.info(
                            f"Successfully extracted from HTML fallback: '{title}' by '{artist}'"
                        )
                        tracks.append((title.strip(), artist.strip()))

            if not tracks:
                raise ValueError(
                    "No tracks could be extracted from the Apple Music resource."
                )

            logger.info(f"Success! {len(tracks)} track(s) extracted.")
            return tracks

    except Exception as e:
        logger.error(f"Error processing Apple Music URL {url}: {e}", exc_info=True)
        if "page" in locals() and page and not page.is_closed():
            await page.screenshot(path="apple_music_scrape_failed.png")
            logger.info("Screenshot of the error saved.")

        embed = Embed(
            description=get_messages("apple_music_error", guild_id),
            color=(
                0xFFB6C1
                if (get_guild_state(guild_id).locale == Locale.EN_X_KAWAII)
                else discord.Color.red()
            ),
        )
        try:
            if interaction and not interaction.is_expired():
                await interaction.followup.send(
                    silent=SILENT_MESSAGES, embed=embed, ephemeral=True
                )
        except Exception as send_error:
            logger.error(f"Unable to send error message: {send_error}")
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
            meta_text = await page.locator(meta_item_selector).first.inner_text(
                timeout=3000
            )
            total_tracks_expected = int(re.search(r"\d+", meta_text).group())
            logger.info(f"Goal: Extract {total_tracks_expected} tracks.")
        except Exception:
            logger.warning("Unable to determine the total number of tracks.")
            total_tracks_expected = 0
        track_row_selector = "div[data-track-id]"
        all_tracks = []
        seen_track_ids = set()
        stagnation_counter = 0
        max_loops = 500
        for i in range(max_loops):
            if total_tracks_expected > 0 and len(all_tracks) >= total_tracks_expected:
                logger.info("All expected leads have been found. Early shutdown.")
                break
            track_elements = await page.query_selector_all(track_row_selector)
            if not track_elements and i > 0:
                break
            new_tracks_found_in_loop = False
            for element in track_elements:
                track_id = await element.get_attribute("data-track-id")
                if track_id and track_id not in seen_track_ids:
                    new_tracks_found_in_loop = True
                    seen_track_ids.add(track_id)
                    try:
                        title_el = await element.query_selector(
                            'span._titleText_51cccae, span[data-test="table-cell-title"]'
                        )
                        artist_el = await element.query_selector(
                            'a._item_39605ae, a[data-test="grid-item-detail-text-title-artist"]'
                        )
                        if title_el and artist_el:
                            title = (
                                (await title_el.inner_text()).split("<span>")[0].strip()
                            )
                            artist = await artist_el.inner_text()
                            if title and artist:
                                all_tracks.append((title, artist))
                    except Exception:
                        continue
            if not new_tracks_found_in_loop and i > 1:
                stagnation_counter += 1
                if stagnation_counter >= 5:
                    logger.info("Stable stagnation. End of process.")
                    break
            else:
                stagnation_counter = 0
            if track_elements:
                await track_elements[-1].scroll_into_view_if_needed(timeout=10000)
                await asyncio.sleep(0.75)
        logger.info(
            f"Process completed. Final total of unique tracks extracted: {len(all_tracks)}"
        )
        return list(dict.fromkeys(all_tracks))

    browser = None  # Initialize the browser to None
    try:
        clean_url = url.split("?")[0]
        parsed_url = urlparse(clean_url)
        path_parts = parsed_url.path.strip("/").split("/")

        resource_type = None
        if "playlist" in path_parts:
            resource_type = "playlist"
        elif "album" in path_parts:
            resource_type = "album"
        elif "mix" in path_parts:
            resource_type = "mix"
        elif "track" in path_parts:
            resource_type = "track"
        elif "video" in path_parts:
            resource_type = "video"

        if resource_type is None:
            raise ValueError("Tidal URL not supported.")

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
            )
            await page.goto(clean_url, wait_until="domcontentloaded")
            logger.info(f"Navigate to Tidal URL ({resource_type}): {clean_url}")

            await asyncio.sleep(3)
            unique_tracks = []

            if resource_type in ["playlist", "album", "mix"]:
                unique_tracks = await load_and_extract_all_tracks(page)

            elif resource_type == "track" or resource_type == "video":
                logger.info(f"Extracting a single media ({resource_type})...")
                try:
                    await page.wait_for_selector(
                        'div[data-test="artist-profile-header"], div[data-test="footer-player"]',
                        timeout=10000,
                    )
                    title_selector = 'span[data-test="now-playing-track-title"], h1[data-test="title"]'
                    artist_selector = (
                        'a[data-test="grid-item-detail-text-title-artist"]'
                    )
                    title = await page.locator(title_selector).first.inner_text(
                        timeout=5000
                    )
                    artist = await page.locator(artist_selector).first.inner_text(
                        timeout=5000
                    )

                    if not title or not artist:
                        raise ValueError("Missing title or artist.")

                    logger.info(
                        f"Unique media found: {title.strip()} - {artist.strip()}"
                    )
                    unique_tracks = [(title.strip(), artist.strip())]

                except Exception as e:
                    logger.warning(
                        f"Direct extraction method failed ({e}), attempting with page title..."
                    )
                    try:
                        page_title = await page.title()
                        title, artist = "", ""
                        if " - " in page_title:
                            parts = page_title.split(" - ")
                            artist, title = parts[0], parts[1].split(" on TIDAL")[0]
                        elif " by " in page_title:
                            parts = page_title.split(" by ")
                            title, artist = parts[0], parts[1].split(" on TIDAL")[0]

                        if not title or not artist:
                            raise ValueError("The page title format is unknown.")

                        logger.info(
                            f"Unique media found via page title: {title.strip()} - {artist.strip()}"
                        )
                        unique_tracks = [(title.strip(), artist.strip())]
                    except Exception as fallback_e:
                        await page.screenshot(
                            path=f"tidal_{resource_type}_extraction_failed.png"
                        )
                        raise ValueError(
                            f"All extraction methods failed. Final error: {fallback_e}"
                        )

            if not unique_tracks:
                raise ValueError(
                    "No tracks could be retrieved from the Tidal resource."
                )

            return unique_tracks

    except Exception as e:
        logger.error(f"Major error in process_tidal_url for {url}: {e}")
        if interaction:
            embed = Embed(
                description=get_messages("tidal_error", guild_id),
                color=(
                    0xFFB6C1
                    if (get_guild_state(guild_id).locale == Locale.EN_X_KAWAII)
                    else discord.Color.red()
                ),
            )
            await interaction.followup.send(
                silent=SILENT_MESSAGES, embed=embed, ephemeral=True
            )
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
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36"
            )
            page = await context.new_page()

            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            logger.info("Page loaded. Cookie management.")

            try:
                await page.click(
                    'music-button:has-text("Accepter les cookies")', timeout=7000
                )
                logger.info("Cookie banner accepted.")
            except Exception:
                logger.info("No cookie banner found.")

            tracks = []

            if is_album or is_track:
                page_type = "Album" if is_album else "Track"
                logger.info(
                    f"Page of type '{page_type}' detected. Using JSON extraction method."
                )

                selector = 'script[type="application/ld+json"]'
                await page.wait_for_selector(selector, state="attached", timeout=20000)

                json_ld_scripts = await page.locator(selector).all_inner_texts()

                found_data = False
                for script_content in json_ld_scripts:
                    data = json.loads(script_content)
                    if data.get("@type") == "MusicAlbum" or (
                        is_album and "itemListElement" in data
                    ):
                        album_artist = data.get("byArtist", {}).get(
                            "name", "Unknown Artist"
                        )
                        for item in data.get("itemListElement", []):
                            track_name = item.get("name")
                            track_artist = item.get("byArtist", {}).get(
                                "name", album_artist
                            )
                            if track_name and track_artist:
                                tracks.append((track_name, track_artist))
                        found_data = True
                        break
                    elif data.get("@type") == "MusicRecording":
                        track_name = data.get("name")
                        track_artist = data.get("byArtist", {}).get(
                            "name", "Unknown Artist"
                        )
                        if track_name and track_artist:
                            tracks.append((track_name, track_artist))
                        found_data = True
                        break

                if not found_data:
                    raise ValueError(
                        f"No data of type 'MusicAlbum' or 'MusicRecording' found in JSON-LD tags."
                    )

            elif is_playlist:
                logger.info(
                    "'Playlist' type page detected. Using fast pre-virtualization extraction."
                )
                try:
                    await page.wait_for_selector(
                        "music-image-row[primary-text]", timeout=20000
                    )
                    logger.info(
                        "Tracklist detected. Waiting 3.5 seconds for initial load."
                    )
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
                tracks = [(track["title"], track["artist"]) for track in tracks_data]

            else:
                raise ValueError(
                    "Amazon Music URL not recognized (neither album, nor playlist, nor track)."
                )

            if not tracks:
                raise ValueError("No tracks could be extracted from the page.")

            logger.info(
                f"Processing complete. {len(tracks)} track(s) found. First track: {tracks[0]}"
            )
            return tracks

    except Exception as e:
        logger.error(
            f"Final error in process_amazon_music_url for {url}: {e}", exc_info=True
        )
        if "page" in locals() and page and not page.is_closed():
            await page.screenshot(path="amazon_music_scrape_failed.png")
            logger.info("Screenshot of the error saved.")

        embed = Embed(
            description=get_messages("amazon_music_error", guild_id),
            color=(
                0xFFB6C1
                if (get_guild_state(guild_id).locale == Locale.EN_X_KAWAII)
                else discord.Color.red()
            ),
        )
        try:
            if interaction and not interaction.is_expired():
                await interaction.followup.send(
                    silent=SILENT_MESSAGES, embed=embed, ephemeral=True
                )
        except Exception as send_error:
            logger.error(f"Unable to send error message: {send_error}")
        return None
    finally:
        if browser:
            await browser.close()
            logger.info("Playwright (Amazon Music) browser closed successfully.")
