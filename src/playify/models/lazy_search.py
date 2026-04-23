"""Lazy search items used for deferred playlist hydration."""

from ..core import *
from ..helpers.url_utils import sanitize_query
from ..services.voice import fetch_video_info_with_retry

class LazySearchItem:
    """
    An object representing a song from a playlist that has not yet been searched for.
    The search (resolution) on SoundCloud is only performed when the song is
    about to be played. It intelligently tries to avoid 30s previews.
    """

    def __init__(
        self,
        query_dict: dict,
        requester: discord.User,
        original_platform: str = "SoundCloud",
    ):
        self.query_dict = query_dict
        self.requester = requester
        self.resolved_info = None
        self.search_lock = asyncio.Lock()
        self.original_platform = (
            original_platform  # Remembers the origin (Spotify, etc.)
        )

        self.title = self.query_dict.get("name", "Pending resolution...")
        self.artist = self.query_dict.get("artist", "Unknown Artist")

        self.url = "#"
        self.webpage_url = "#"
        self.duration = 0
        self.thumbnail = None
        self.source_type = "lazy"

    async def resolve(self):
        """
        Performs the search and stores the full result.
        It intelligently filters out 30-second previews.
        The search is done on YouTube if IS_PUBLIC_VERSION is False, otherwise on SoundCloud.
        Only performs the search once thanks to the lock and check.
        """
        async with self.search_lock:
            if self.resolved_info:
                return self.resolved_info

            if IS_PUBLIC_VERSION:
                search_prefix = "scsearch5:"
                platform_name = "SoundCloud"
            else:
                search_prefix = "ytsearch5:"
                platform_name = "YouTube"

            search_term = f"{self.title} {self.artist}"
            logger.info(f"[LazyResolve] Resolving on {platform_name}: '{search_term}'")
            try:
                search_query = f"{search_prefix}{sanitize_query(search_term)}"

                info = await fetch_video_info_with_retry(
                    search_query, {"noplaylist": True, "extract_flat": True}
                )

                entries = info.get("entries")
                if not entries:
                    raise ValueError(f"No results found on {platform_name}.")

                best_video_info = None
                if platform_name == "SoundCloud":
                    for video in entries:
                        if video.get("duration", 0) > 40:
                            best_video_info = video
                            logger.info(
                                f"[LazyResolve] Found suitable full track: '{video.get('title')}'"
                            )
                            break

                if not best_video_info:
                    logger.info(
                        f"[LazyResolve] Using first result from {platform_name}."
                    )
                    best_video_info = entries[0]

                full_video_info = await fetch_video_info_with_retry(
                    best_video_info["url"], {"noplaylist": True}
                )

                full_video_info["requester"] = self.requester
                full_video_info["original_platform"] = self.original_platform
                self.resolved_info = full_video_info
                return self.resolved_info

            except Exception as e:
                logger.error(
                    f"[LazyResolve] Failed to resolve '{search_term}' on {platform_name}: {e}"
                )
                self.resolved_info = {"error": True, "title": search_term}
                return self.resolved_info
