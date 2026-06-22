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
                    None,
                    lambda: spotify_scraper_client.get_playlist(clean_url).to_dict(),
                )
                for item in data.get("tracks", []):
                    track = item.get("track")
                    if not track:
                        continue
                    tracks_to_return.append(
                        (
                            track.get("name", "Unknown Title"),
                            track.get("artists", [{}])[0].get("name", "Unknown Artist"),
                        )
                    )

            elif "album" in clean_url:
                data = await loop.run_in_executor(
                    None, lambda: spotify_scraper_client.get_album(clean_url).to_dict()
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
                    None, lambda: spotify_scraper_client.get_track(clean_url).to_dict()
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


import aiohttp
import json
import re
from urllib.parse import urlparse, parse_qs


async def process_apple_music_url(url, interaction):
    guild_id = interaction.guild.id
    logger.info(f"Starting lightweight processing for Apple Music URL: {url}")

    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
        }

        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(url, timeout=15) as response:
                if response.status != 200:
                    raise ValueError(f"HTTP {response.status} returned by Apple Music.")
                html = await response.text()

        match = re.search(
            r'<script type="application/json" id="serialized-server-data">(.*?)</script>',
            html,
            re.DOTALL,
        )
        if not match:
            raise ValueError("Impossible de trouver les données JSON dans la page.")

        data = json.loads(match.group(1))
        tracks = []

        parsed_url = urlparse(url)
        query_params = parse_qs(parsed_url.query)
        target_track_id = query_params.get("i", [None])[0]

        sections = []
        for item in data.get("data", []):
            if "data" in item and "sections" in item["data"]:
                sections.extend(item["data"]["sections"])

        for section in sections:
            if section.get("itemKind") == "trackLockup":
                for item in section.get("items", []):
                    title = item.get("title")
                    artist = item.get("artistName")
                    track_id = None

                    try:
                        track_id = (
                            item.get("contentDescriptor", {})
                            .get("identifiers", {})
                            .get("storeAdamID")
                        )
                    except Exception:
                        pass

                    if title and artist:
                        if target_track_id:
                            # Chanson précise demandée
                            if str(track_id) == str(target_track_id):
                                tracks.append((title, artist))
                                break
                        else:
                            # Ajout de toutes les chansons
                            tracks.append((title, artist))

        if not tracks:
            raise ValueError(
                "No tracks could be extracted from the Apple Music resource."
            )

        logger.info(
            f"Success! {len(tracks)} track(s) extracted instantly via Server-Side JSON."
        )
        return tracks

    except Exception as e:
        logger.error(f"Error processing Apple Music URL {url}: {e}", exc_info=True)

        state = get_guild_state(guild_id)
        is_kawaii = state.locale == Locale.EN_X_KAWAII

        embed = Embed(
            description=get_messages("apple_music_error", guild_id),
            color=0xFFB6C1 if is_kawaii else discord.Color.red(),
        )
        try:
            if interaction and not interaction.is_expired():
                await interaction.followup.send(
                    silent=SILENT_MESSAGES, embed=embed, ephemeral=True
                )
        except Exception as send_error:
            logger.error(f"Unable to send error message: {send_error}")
        return None


# Process Tidal URLs
async def process_tidal_url(url, interaction):
    guild_id = interaction.guild_id
    logger.info(f"[Tidal] Starting lightweight HTTP processing for: {url}")

    try:
        clean_url = url.split("?")[0]
        parsed_url = urlparse(clean_url)
        path_parts = parsed_url.path.strip("/").split("/")

        resource_type = None
        resource_id = None

        valid_types = ["playlist", "album", "mix", "track", "video"]
        for i, part in enumerate(path_parts):
            if part in valid_types and i + 1 < len(path_parts):
                resource_type = part
                resource_id = path_parts[i + 1]
                break

        if not resource_type or not resource_id:
            raise ValueError(f"Tidal URL not supported or could not extract ID: {url}")

        token = "txNoH4kkV41MfH25"
        headers = {
            "x-tidal-token": token,
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            "Accept": "application/json"
        }

        all_tracks = []
        limit = 100
        offset = 0

        import aiohttp
        async with aiohttp.ClientSession() as session:
            while True:
                if resource_type == "playlist":
                    api_url = f"https://tidal.com/v1/playlists/{resource_id}/items?offset={offset}&limit={limit}&countryCode=FR"
                elif resource_type == "album":
                    api_url = f"https://tidal.com/v1/albums/{resource_id}/items?offset={offset}&limit={limit}&countryCode=FR"
                elif resource_type == "mix":
                    api_url = f"https://api.tidal.com/v1/mixes/{resource_id}/items?offset={offset}&limit={limit}&countryCode=FR"
                elif resource_type == "track":
                    api_url = f"https://tidal.com/v1/tracks/{resource_id}?countryCode=FR"
                elif resource_type == "video":
                    api_url = f"https://tidal.com/v1/videos/{resource_id}?countryCode=FR"

                logger.info(f"[Tidal] Fetching API: {api_url}")
                async with session.get(api_url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status != 200:
                        raise ValueError(f"Tidal API returned HTTP {resp.status}")
                    data = await resp.json()

                if resource_type in ["playlist", "album", "mix"]:
                    items = data.get("items", [])
                    if not items:
                        break

                    for item in items:
                        track_data = item.get("item", item) if resource_type in ["playlist", "mix"] else item
                        title = track_data.get("title")
                        artist = track_data.get("artist", {}).get("name")
                        if title and artist:
                            all_tracks.append((title.strip(), artist.strip()))

                    total_items = data.get("totalNumberOfItems", len(items))
                    offset += limit
                    
                    if offset >= total_items:
                        break
                else:
                    # Single track or video
                    title = data.get("title")
                    artist = data.get("artist", {}).get("name")
                    if title and artist:
                        all_tracks.append((title.strip(), artist.strip()))
                    break

        # Deduplicate
        seen = set()
        unique_tracks = []
        for t in all_tracks:
            if t not in seen:
                seen.add(t)
                unique_tracks.append(t)

        if not unique_tracks:
            raise ValueError("No tracks could be retrieved from the Tidal resource.")

        logger.info(f"[Tidal] Process completed. Extracted {len(unique_tracks)} track(s). First: {unique_tracks[0]}")
        return unique_tracks

    except Exception as e:
        logger.error(f"[Tidal] Major error in process_tidal_url for {url}: {e}")
        if interaction and not interaction.is_expired():
            embed = Embed(
                description=get_messages("tidal_error", guild_id),
                color=(
                    0xFFB6C1
                    if (get_guild_state(guild_id).locale == Locale.EN_X_KAWAII)
                    else discord.Color.red()
                ),
            )
            try:
                await interaction.followup.send(
                    silent=SILENT_MESSAGES, embed=embed, ephemeral=True
                )
            except Exception as send_error:
                logger.error(f"[Tidal] Unable to send error message: {send_error}")
        return None


async def process_amazon_music_url(url, interaction):
    guild_id = interaction.guild_id
    logger.info(f"[Amazon Music] Starting lightweight HTTP processing for: {url}")

    def _extract_tracks_recursive(obj, results):
        """
        Walks the JSON tree and collects (title, artist) tuples from nodes
        that contain either (primaryText + secondaryText1) or (trackName + artistName).
        It also checks for embedded JSON-LD strings for hidden SEO data.
        """
        if isinstance(obj, dict):
            primary = obj.get("primaryText")
            secondary = obj.get("secondaryText1")
            track_name = obj.get("trackName")
            artist_name = obj.get("artistName")

            if primary and secondary:
                if isinstance(primary, dict):
                    primary = primary.get("text", "")
                if isinstance(secondary, dict):
                    secondary = secondary.get("text", "")
                if primary and secondary:
                    results.append((str(primary).strip(), str(secondary).strip()))
            elif track_name and artist_name:
                results.append((str(track_name).strip(), str(artist_name).strip()))

            for value in obj.values():
                if isinstance(value, str) and value.startswith(
                    '{"@context":"https://schema.org"'
                ):
                    try:
                        import json

                        data = json.loads(value)
                        if data.get("@type") == "MusicAlbum" and "track" in data:
                            album_artist = data.get("byArtist", {}).get(
                                "name", "Unknown Artist"
                            )
                            for track in data.get("track", []):
                                t_name = track.get("name")
                                if t_name:
                                    results.append(
                                        (str(t_name).strip(), str(album_artist).strip())
                                    )
                        elif data.get("@type") == "MusicRecording":
                            t_name = data.get("name")
                            t_artist = data.get("byArtist", {}).get(
                                "name", "Unknown Artist"
                            )
                            if t_name:
                                results.append(
                                    (str(t_name).strip(), str(t_artist).strip())
                                )
                    except Exception:
                        pass
                _extract_tracks_recursive(value, results)
        elif isinstance(obj, list):
            for item in obj:
                _extract_tracks_recursive(item, results)

    def _clean_track_text(text):
        """Removes noise tags like [Explicit] from track/artist strings."""
        return re.sub(r"\s*\[Explicit\]\s*", "", text, flags=re.IGNORECASE).strip()

    def _deduplicate_ordered(seq):
        """Deduplicates a list of tuples while preserving insertion order."""
        seen = set()
        result = []
        for item in seq:
            if item not in seen:
                seen.add(item)
                result.append(item)
        return result

    try:
        parsed = urlparse(url)
        domain = parsed.netloc
        if not domain:
            raise ValueError(f"Could not extract domain from URL: {url}")
        logger.info(f"[Amazon Music] Domain extracted: {domain}")

        deeplink_path = parsed.path
        if parsed.query:
            deeplink_path += f"?{parsed.query}"

        user_agent = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        )

        import aiohttp

        jar = aiohttp.CookieJar()
        async with aiohttp.ClientSession(cookie_jar=jar) as session:
            config_url = f"https://{domain}/config.json"
            logger.info(f"[Amazon Music] Fetching config from: {config_url}")

            async with session.get(
                config_url,
                headers={
                    "User-Agent": user_agent,
                    "Accept": "application/json, text/plain, */*",
                    "Referer": f"https://{domain}/",
                },
                timeout=aiohttp.ClientTimeout(total=15),
            ) as config_resp:
                if config_resp.status != 200:
                    raise ValueError(f"config.json returned HTTP {config_resp.status}")
                config_data = await config_resp.json(content_type=None)

            device_id = config_data.get("deviceId", "")
            session_id = config_data.get("sessionId", "")
            csrf = config_data.get("csrf", {})
            csrf_token = csrf.get("token", "")
            csrf_ts = csrf.get("ts", csrf.get("timestamp", ""))
            csrf_rnd = csrf.get("rnd", csrf.get("rndNonce", ""))
            version = config_data.get("version", "1.0")

            show_home_url = "https://eu.web.skill.music.a2z.com/api/showHome"

            import time
            import uuid

            deeplink_obj = {
                "interface": "DeeplinkInterface.v1_0.DeeplinkClientInformation",
                "deeplink": deeplink_path,
            }

            inner_headers = {
                "x-amzn-authentication": json.dumps(
                    {
                        "interface": "ClientAuthenticationInterface.v1_0.ClientTokenElement",
                        "accessToken": "",
                    }
                ),
                "x-amzn-device-model": "WEBPLAYER",
                "x-amzn-device-width": "1920",
                "x-amzn-device-family": "WebPlayer",
                "x-amzn-device-id": device_id,
                "x-amzn-user-agent": user_agent,
                "x-amzn-session-id": session_id,
                "x-amzn-device-height": "1080",
                "x-amzn-request-id": str(uuid.uuid4()),
                "x-amzn-device-language": "fr_FR",
                "x-amzn-currency-of-preference": "EUR",
                "x-amzn-os-version": "1.0",
                "x-amzn-application-version": version,
                "x-amzn-device-time-zone": "Europe/Paris",
                "x-amzn-timestamp": str(int(time.time() * 1000)),
                "x-amzn-csrf": json.dumps(
                    {
                        "interface": "CSRFInterface.v1_0.CSRFHeaderElement",
                        "token": csrf_token,
                        "timestamp": csrf_ts,
                        "rndNonce": csrf_rnd,
                    }
                ),
                "x-amzn-music-domain": domain,
                "x-amzn-referer": "",
                "x-amzn-affiliate-tags": "",
                "x-amzn-ref-marker": "",
                "x-amzn-page-url": url,
                "x-amzn-weblab-id-overrides": "",
                "x-amzn-video-player-token": "",
                "x-amzn-feature-flags": "",
                "x-amzn-has-profile-id": "",
                "x-amzn-age-band": "",
            }

            payload = json.dumps(
                {
                    "deeplink": json.dumps(deeplink_obj),
                    "headers": json.dumps(inner_headers),
                }
            )

            post_headers = {
                "User-Agent": user_agent,
                "Content-Type": "text/plain;charset=UTF-8",
                "Accept": "*/*",
                "Origin": f"https://{domain}",
                "Referer": f"https://{domain}/",
            }

            logger.info(f"[Amazon Music] POSTing to showHome: {show_home_url}")
            async with session.post(
                show_home_url,
                data=payload,
                headers=post_headers,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as api_resp:
                if api_resp.status != 200:
                    resp_text = await api_resp.text()
                    raise ValueError(
                        f"showHome API returned HTTP {api_resp.status}: "
                        f"{resp_text[:500]}"
                    )
                api_data = await api_resp.json(content_type=None)

        raw_tracks = []
        _extract_tracks_recursive(api_data, raw_tracks)

        if not raw_tracks:
            raise ValueError(
                "No tracks found. The URL may be invalid or completely region-locked."
            )

        cleaned_tracks = [
            (_clean_track_text(title), _clean_track_text(artist))
            for title, artist in raw_tracks
            if title and artist
        ]
        tracks = _deduplicate_ordered(cleaned_tracks)

        if not tracks:
            raise ValueError("All extracted tracks were empty after cleaning.")

        logger.info(
            f"[Amazon Music] Done! {len(tracks)} unique track(s) extracted. "
            f"First: {tracks[0]}"
        )
        return tracks

    except Exception as e:
        logger.error(f"[Amazon Music] Error processing {url}: {e}", exc_info=True)

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
            logger.error(f"[Amazon Music] Unable to send error message: {send_error}")
        return None
