"""View model for "Build from my channel" (/description-templates/build): candidate blocks as cards."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from .. import templatebuild
from ..store import TemplateBlock, Video
from .template_view import PAGE_URL

BUILD_URL = PAGE_URL + "/build"


@dataclass(frozen=True)
class BuildCard:
    key: str
    text: str
    on_text: str  # "On 12 of 40 videos"
    examples: list[tuple[str, str]]  # (video id, title)
    versions: list[tuple[str, str]]  # (text, "On 3 videos")
    already: str
    name: str  # prefilled: the suggestion, or what was typed before a refusal
    link_slot: bool
    remember: bool


@dataclass(frozen=True)
class BuildPage:
    has_videos: bool  # the catalog holds any video at all
    videos_read: int
    cards: list[BuildCard]
    found: int  # before the cap
    shown: int
    added: str  # the block just added, or ""


def _on(count: int, total: int | None = None) -> str:
    noun = "video" if (total if total is not None else count) == 1 else "videos"
    return f"On {count} of {total} {noun}" if total is not None else f"On {count} {noun}"


def build_page(videos: Sequence[Video], result: templatebuild.BuildResult, blocks: Sequence[TemplateBlock],
               query: Mapping[str, str], typed: Mapping[str, Mapping[str, str]]) -> BuildPage:
    titles = {v.video_id: v.title for v in videos}
    cards = []
    for c in result.candidates:
        kept = typed.get(f"build:{c.key}")
        cards.append(BuildCard(
            key=c.key, text=c.text, on_text=_on(c.video_count, result.videos_read),
            examples=[(vid, titles.get(vid) or vid) for vid in c.examples],
            versions=[(text, _on(count)) for text, count in c.versions],
            already=c.already,
            name=kept["name"] if kept else c.suggested_name,
            link_slot=(kept.get("link_slot") == "1") if kept else c.suggested_link_slot,
            remember=(kept.get("remember") == "1") if kept else True))
    added = ""
    raw = str(query.get("added") or "")
    if raw.isdigit():
        added = next((b.name for b in blocks if b.id == int(raw)), "")
    return BuildPage(has_videos=bool(videos), videos_read=result.videos_read, cards=cards, found=result.found,
                     shown=len(result.candidates), added=added)
