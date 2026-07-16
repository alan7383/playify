"""yt-dlp extraction worker, isolated in a minimal module.

This module is imported by every ProcessPoolExecutor child. On Windows
(spawn start method) a worker imports the module that defines its task
function: when that was voice.py — which star-imports core.py and thereby
discord.py, spotipy, lyricsgenius, the whole bot — every pool worker cost
~100 MB of RSS. Keeping this file's imports down to yt_dlp + psutil brings
a warm worker to a fraction of that.

The worker also slims the returned info dict: yt-dlp's full extraction
carries the complete `formats` list (dozens of entries with signed URLs and
per-format HTTP headers), thumbnails in every size, subtitles, storyboards…
easily hundreds of KB per track. Nothing in Playify reads those keys, but
the full dicts used to flow into the URL cache, the queue, the history and
the playback state — a few dozen tracks quietly became hundreds of MB.
"""

import os
import platform

import psutil

# NOTE: yt_dlp itself is imported inside ydl_worker(), not here. The parent
# process imports this module too (to hand the function to the pool), and a
# top-level import would drag ~15 MB of yt-dlp back into the main process.

# Keys that are never read anywhere in Playify and dominate the dict size.
_HEAVY_KEYS = (
    "formats",
    "requested_formats",
    "thumbnails",
    "automatic_captions",
    "subtitles",
    "heatmap",
    "storyboards",
    "chapters",
    "tags",
    "categories",
    "http_headers",
    "downloader_options",
    "fragments",
    "description",
    "_format_sort_fields",
)


def slim_info(info):
    """Strips heavy, unused yt-dlp keys from an info dict (and its entries)."""
    if not isinstance(info, dict):
        return info
    for key in _HEAVY_KEYS:
        info.pop(key, None)
    entries = info.get("entries")
    if isinstance(entries, list):
        info["entries"] = [slim_info(entry) for entry in entries]
    return info


def ydl_worker(ydl_opts, query, cookies_file=None):
    """Runs in a pool child: lower own priority, extract, return slim data.

    Exceptions are converted to plain strings so nothing unpicklable
    crosses the process boundary.
    """
    import yt_dlp  # heavy: only ever loaded in pool children

    process = psutil.Process()
    if platform.system() == "Windows":
        process.nice(psutil.IDLE_PRIORITY_CLASS)
    else:
        os.nice(19)

    if cookies_file and os.path.exists(cookies_file):
        ydl_opts["cookiefile"] = cookies_file

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            result = ydl.extract_info(query, download=False)
        return {"status": "success", "data": slim_info(result)}
    except Exception as e:
        return {"status": "error", "message": str(e)}
