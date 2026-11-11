"""Playlist pull: read the channel's own playlists and their video ids from YouTube (read only) and store them
all at once, so description templates can claim videos by playlist."""

from __future__ import annotations

import math
from dataclasses import dataclass

from googleapiclient.errors import HttpError

from .catalog import NoChannelError
from .quota import QuotaExceeded, QuotaGuard
from .store import Store
from .youtube import MAX_RESULTS, YouTubeReader, api_error_info

NOTE = "playlist pull"
# playlistItems.list answers that mean "this one playlist cannot be read": it is skipped, the pull goes on.
SKIP_REASONS = frozenset({"playlistNotFound", "playlistItemsNotAccessible", "forbidden"})


@dataclass(frozen=True)
class PlaylistPullResult:
    playlists: int  # stored
    items: int  # video ids stored, over all playlists (also ids not in the catalog)
    units_used: int
    skipped: tuple[str, ...]  # titles of the playlists YouTube refused


def _refused(exc: HttpError) -> bool:
    """A refusal of one playlist: 404, or 403 with a reason that is about the playlist (never quota or
    sign-in reasons, which stop the pull)."""
    info = api_error_info(exc)
    if info.status == 404 or "playlistNotFound" in info.reasons:
        return True
    return info.status == 403 and info.reason in SKIP_REASONS


def pages_for(item_count: object) -> int:
    """playlistItems.list pages a playlist of this many items needs: ceil(count / 50), at least 1."""
    try:
        count = int(item_count)  # type: ignore[call-overload]
    except (TypeError, ValueError):
        count = 0
    return max(1, math.ceil(count / MAX_RESULTS))


def pull_playlists(reader: YouTubeReader, store: Store, guard: QuotaGuard) -> PlaylistPullResult:
    """Read every playlist of the signed-in channel and replace the stored playlists with them.

    Each call is charged to the guard before it is made. Before any item is read, the item pages are
    estimated from each playlist's item count; if they do not fit in today's remaining budget, QuotaExceeded
    is raised and nothing is stored. Everything is collected first and stored in one go, so a failure part-way
    leaves the stored playlists as they were. The channel's uploads playlist is never read here.
    """
    units = guard.charge("channels.list", note=NOTE)
    channels = reader.channels_mine("id,contentDetails")
    found = channels.get("items") or []
    if not found:
        raise NoChannelError("this Google account has no YouTube channel")
    uploads = ((found[0].get("contentDetails") or {}).get("relatedPlaylists") or {}).get("uploads")

    listed: list[tuple[str, str, int]] = []  # (id, title, pages)
    seen: set[str] = set()
    page_token: str | None = None
    while True:
        units += guard.charge("playlists.list", note=NOTE)
        page = reader.playlists_mine(page_token)
        for entry in page.get("items") or []:
            playlist_id = str(entry.get("id") or "")
            if not playlist_id or playlist_id == uploads or playlist_id in seen:
                continue
            seen.add(playlist_id)
            title = str((entry.get("snippet") or {}).get("title") or playlist_id)
            listed.append((playlist_id, title, pages_for((entry.get("contentDetails") or {}).get("itemCount"))))
        page_token = page.get("nextPageToken")
        if not page_token:
            break

    if listed:
        pages = sum(p for _, _, p in listed)
        preview = guard.preview([("playlistItems.list", pages)])
        if not preview.fits:
            raise QuotaExceeded(
                f"need {preview.total} units for {pages} playlistItems.list pages of {len(listed)} playlists "
                f"but only {preview.remaining} remaining"
            )

    collected: dict[str, tuple[str, list[str]]] = {}
    skipped: list[str] = []
    for playlist_id, title, _ in listed:
        video_ids: list[str] = []
        in_list: set[str] = set()
        page_token = None
        try:
            while True:
                units += guard.charge("playlistItems.list", note=NOTE)
                page = reader.playlist_items(playlist_id, page_token)
                for entry in page.get("items") or []:
                    vid = (entry.get("contentDetails") or {}).get("videoId")
                    if vid and vid not in in_list:
                        in_list.add(vid)
                        video_ids.append(vid)
                page_token = page.get("nextPageToken")
                if not page_token:
                    break
        except HttpError as exc:
            if not _refused(exc):
                raise
            skipped.append(title)
            continue
        collected[playlist_id] = (title, video_ids)

    store.replace_playlists(collected)
    return PlaylistPullResult(len(collected), sum(len(v) for _, v in collected.values()), units,
                              tuple(skipped))
