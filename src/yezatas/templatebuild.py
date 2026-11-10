"""Build from my channel: find the text that repeats across the catalog's descriptions.

Pure code: no I/O, no model, nothing is written anywhere. The page offers each candidate as a shared block.

How lines become candidates (deterministic, the same input always gives the same output):

1. Lines. Each description is read line by line, whitespace collapsed and trimmed, case kept (the rule the block
   parser uses). Blank lines, lines that start with a timestamp and lines made only of hashtags are ignored. A line
   counts once per video.
2. Repeated. A line is repeated when it is on at least `MIN_VIDEOS` videos and on at least `MIN_SHARE` of the
   videos read.
3. Runs. In each description, repeated lines that follow each other (blank lines between them allowed) form a run;
   any other non-blank line ends it.
4. Joins. Two neighbours a, b of a run stay in one candidate when (i) on at least `JOIN_SHARE` of the videos that
   carry the rarer of the two, b sits right after a, and (ii) a is followed by some repeated line on at least
   `CONTINUE_SHARE` of its videos and b is preceded by one on at least `CONTINUE_SHARE` of its videos. Rule (i)
   keeps lines that only meet by chance apart; rule (ii) keeps two blocks apart when one is usually alone. A run
   is cut wherever a join fails; each piece is one candidate text.
5. Candidates. A piece on at least as many videos as a repeated line needs (counted once per video) is a candidate.
   The candidate text is its lines joined by single line breaks (blank lines between them are not kept; the block
   parser skips blank lines anyway).

Versions: two candidates are versions of one block when they have the same number of lines (two or more) and
differ in exactly one line. Candidates are taken in order (most videos first, then most lines, then text); each
one either joins the first earlier candidate it is a version of, as an older version, or starts a new candidate.
So the version on the most videos is the candidate's text, and the older versions are listed with their counts.
A one-line candidate has no versions: one changed line would be the whole text.
"""

from __future__ import annotations

import hashlib
import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from . import seo
from .descriptiontemplate import is_placeholder
from .store import TemplateBlock, Video

MIN_VIDEOS = 3  # a line must be on at least this many videos...
MIN_SHARE = 0.05  # ...and on at least this share of the videos read
JOIN_SHARE = 0.8  # neighbours stay together when the rarer one sits beside the other this often
CONTINUE_SHARE = 0.5  # and each side has a repeated neighbour on that side this often
MAX_CANDIDATES = 30  # the page shows at most this many
KEY_LENGTH = 12

# Cues for the suggested name, checked on the text with case ignored; a disclosure wins over support.
DISCLOSURE_CUES = ("affiliate", "commission", "paid link", "sponsored")
SUPPORT_CUES = ("support", "follow", "subscribe", "patreon")
DISCLOSURE_NAME = "Paid links"
SUPPORT_NAME = "Support and follow"


@dataclass(frozen=True)
class Candidate:
    key: str  # a short stable hash of the text, for the form
    text: str
    line_count: int
    video_count: int
    share: float  # 0..1 of the videos read
    examples: tuple[str, ...]  # up to 3 video ids that carry this text, most viewed first
    versions: tuple[tuple[str, int], ...] = ()  # older versions: (text, video count), most carried first
    has_urls: bool = False
    already: str = ""  # the shared block that already holds this text, or ""
    suggested_name: str = ""
    suggested_link_slot: bool = False


@dataclass(frozen=True)
class BuildResult:
    candidates: list[Candidate]  # at most MAX_CANDIDATES, in order
    found: int  # how many were found before the cap
    videos_read: int


@dataclass
class _Piece:
    lines: tuple[str, ...]
    videos: set[str] = field(default_factory=set)


def _norm(line: str) -> str:
    return " ".join(line.split())


def _counted(line: str) -> bool:
    return bool(line) and seo.timestamp_seconds(line) is None and not seo.is_hashtag_line(line)


def make_key(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:KEY_LENGTH]


def needed_videos(videos_read: int) -> int:
    """How many videos a line (or a candidate) must be on to count as repeated."""
    return max(MIN_VIDEOS, math.ceil(MIN_SHARE * videos_read - 1e-9))


def _description_lines(video: Video) -> list[str]:
    """The video's lines, normalised; "" for a line that is blank (it never breaks a run)."""
    return [_norm(line) for line in (video.description or "").replace("\r\n", "\n").split("\n")]


def _runs(lines: Sequence[str], repeated: set[str]) -> list[list[str]]:
    runs: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        if not line:
            continue
        if line in repeated:
            current.append(line)
        else:
            if current:
                runs.append(current)
            current = []
    if current:
        runs.append(current)
    return runs


def _block_lines(text: str) -> tuple[str, ...]:
    return tuple(t for t in (_norm(line) for line in text.split("\n")) if t)


def _versions_of(a: tuple[str, ...], b: tuple[str, ...]) -> bool:
    return len(a) == len(b) >= 2 and sum(x != y for x, y in zip(a, b)) == 1


def _suggest(text: str) -> str:
    low = text.casefold()
    if any(cue in low for cue in DISCLOSURE_CUES):
        return DISCLOSURE_NAME
    if any(cue in low for cue in SUPPORT_CUES):
        return SUPPORT_NAME
    return ""


def scan(videos: Iterable[Video], blocks: Sequence[TemplateBlock], include_shorts: bool = False) -> BuildResult:
    """The candidates and how many videos were read; see the module docstring for the rules."""
    read = sorted((v for v in videos if include_shorts or not seo.is_short(v)),
                  key=lambda v: (-v.view_count, v.video_id))
    total = len(read)
    if not total:
        return BuildResult([], 0, 0)
    need = needed_videos(total)
    views = {v.video_id: i for i, v in enumerate(read)}  # rank: most viewed first

    # A line that is not counted (timestamp, hashtags) still ends a run: it becomes a marker no line equals.
    per_video = {v.video_id: [line if (not line or _counted(line)) else "\0" for line in _description_lines(v)]
                 for v in read}

    on: dict[str, set[str]] = defaultdict(set)
    for vid, lines in per_video.items():
        for line in lines:
            if line and line != "\0":
                on[line].add(vid)
    repeated = {line for line, vids in on.items() if len(vids) >= need}
    if not repeated:
        return BuildResult([], 0, total)

    runs = {vid: _runs(lines, repeated) for vid, lines in per_video.items()}
    pair: dict[tuple[str, str], set[str]] = defaultdict(set)
    has_next: dict[str, set[str]] = defaultdict(set)
    has_prev: dict[str, set[str]] = defaultdict(set)
    for vid, vruns in runs.items():
        for run in vruns:
            for a, b in zip(run, run[1:]):
                pair[(a, b)].add(vid)
                has_next[a].add(vid)
                has_prev[b].add(vid)

    def joins(a: str, b: str) -> bool:
        together = len(pair[(a, b)])
        return (together >= JOIN_SHARE * min(len(on[a]), len(on[b]))
                and len(has_next[a]) >= CONTINUE_SHARE * len(on[a])
                and len(has_prev[b]) >= CONTINUE_SHARE * len(on[b]))

    pieces: dict[tuple[str, ...], _Piece] = {}
    for vid, vruns in runs.items():
        for run in vruns:
            start = 0
            for i in range(1, len(run) + 1):
                if i == len(run) or not joins(run[i - 1], run[i]):
                    lines = tuple(run[start:i])
                    pieces.setdefault(lines, _Piece(lines)).videos.add(vid)
                    start = i

    kept = sorted((p for p in pieces.values() if len(p.videos) >= need),
                  key=lambda p: (-len(p.videos), -len(p.lines), "\n".join(p.lines)))

    groups: list[tuple[_Piece, list[_Piece]]] = []
    for piece in kept:
        for main, older in groups:
            if _versions_of(main.lines, piece.lines):
                older.append(piece)
                break
        else:
            groups.append((piece, []))

    existing: dict[tuple[str, ...], str] = {}
    for block in blocks:
        if is_placeholder(block):
            continue
        for text in (block.text, *block.old_versions):
            existing.setdefault(_block_lines(text), block.name)

    candidates: list[Candidate] = []
    used: dict[str, int] = defaultdict(int)
    numbered = 0
    for main, older in groups:
        text = "\n".join(main.lines)
        already = existing.get(main.lines, "")
        name = _suggest(text)
        disclosure = name == DISCLOSURE_NAME
        if not name:
            numbered += 1
            name = f"Block {numbered}"
        else:
            used[name] += 1
            if used[name] > 1:
                name = f"{name} {used[name]}"
        has_urls = seo.has_url(text)
        candidates.append(Candidate(
            key=make_key(text), text=text, line_count=len(main.lines), video_count=len(main.videos),
            share=len(main.videos) / total,
            examples=tuple(sorted(main.videos, key=views.__getitem__)[:3]),
            versions=tuple(("\n".join(p.lines), len(p.videos)) for p in older),
            has_urls=has_urls, already=already, suggested_name=name,
            suggested_link_slot=disclosure and not has_urls))
    return BuildResult(candidates[:MAX_CANDIDATES], len(candidates), total)


def find_candidates(videos: Iterable[Video], blocks: Sequence[TemplateBlock],
                    include_shorts: bool = False) -> list[Candidate]:
    """At most `MAX_CANDIDATES` candidate blocks, most videos first, then longest, then text."""
    return scan(videos, blocks, include_shorts).candidates
