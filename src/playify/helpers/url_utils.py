"""URL parsing and query sanitization helpers."""

from ..core import *

def sanitize_query(query):
    query = re.sub(r"[\x00-\x1F\x7F]", "", query)  # Remove control chars
    query = re.sub(r"\s+", " ", query).strip()  # Normalize spaces
    return query


# YouTube Mix and SoundCloud Stations utilities
def get_video_id(url):
    parsed = urlparse(url)
    if parsed.hostname in ("youtube.com", "www.youtube.com", "youtu.be"):
        if parsed.hostname == "youtu.be":
            return parsed.path[1:]
        if parsed.path == "/watch":
            query = parse_qs(parsed.query)
            return query.get("v", [None])[0]
    return None


def get_mix_playlist_url(video_url):
    video_id = get_video_id(video_url)
    if video_id:
        return f"https://www.youtube.com/watch?v={video_id}&list=RD{video_id}"
    return None


def get_soundcloud_track_id(url):
    if "soundcloud.com" in url:
        try:
            ydl_opts = {
                "quiet": True,
                "no_warnings": True,
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                return info.get("id")
        except Exception:
            return None
    return None


def get_soundcloud_station_url(track_id):
    if track_id:
        return f"https://soundcloud.com/discover/sets/track-stations:{track_id}"
    return


def parse_yt_dlp_error(error_string: str) -> tuple[str, str, str]:
    """
    Parses a yt-dlp error string to find a known cause.
    Returns a tuple of (emoji, title_key, description_key).
    """
    error_lower = error_string.lower()
    if "sign in to confirm your age" in error_lower or "age-restricted" in error_lower:
        return ("🔞", "error.age_restricted.title", "error.age_restricted.description")
    if "private video" in error_lower:
        return ("🔒", "error.private.title", "error.private.description")
    if "video is unavailable" in error_lower:
        return ("❓", "error.unavailable.title", "error.unavailable.description")
    # Default fallback for other access errors
    return ("🚫", "error.generic_access.title", "error.generic_access.description")
