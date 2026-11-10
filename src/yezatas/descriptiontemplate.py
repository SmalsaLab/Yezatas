"""Description templates: which template a video follows and how well its description follows it.

A channel defines templates (an ordered list of sections) and a shared library of fixed text blocks. This
module picks the template for each video, reads the description line by line into those sections and
scores the result for the SEO check "Follows the description template". Pure code, no model calls; the
only I/O is through the `Store` passed to `judge_catalog`.

INTERFACE:
- `assign_template(video, templates, choices, include_shorts, memberships, playlist_titles)` -> Assignment.
- `parse_description(description, template, blocks, video)` -> SectionReport. Lines are numbered from 1.
- `judge_report(template, report, reason)` -> seo.TemplateJudgment.
- `judge_catalog(store, videos)` -> (judgments, own_chars), the two inputs of `seo.score_catalog`.
- `coverage(videos, judgments, templates)` -> Coverage: how many videos follow each template.
- `STARTER_SECTIONS`: the suggested first template, as data for the editor.
- `STARTER_PLACEHOLDERS`, `is_placeholder(block)`: the starter blocks' text the user must replace. A block that
  still holds it is "not set up": it is never looked for in a description and earns or costs no points.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

from . import seo
from .seo import TemplateJudgment
from .store import DescriptionTemplate, Store, TemplateBlock, Video

SECTION_LABELS: dict[str, str] = {
    "opening": "Opening",
    "about": "About the video",
    "chapters": "Chapters",
    "watch_next": "Watch next",
    "hashtags": "Hashtags",
}

# The suggested first template, in order. A block entry names a block the editor offers to create; nothing
# is created automatically.
STARTER_SECTIONS: tuple[dict[str, object], ...] = (
    {"kind": "opening"},
    {"kind": "about"},
    {"kind": "chapters"},
    {"kind": "watch_next"},
    {"kind": "block", "name": "Paid links", "link_slot": True},
    {"kind": "block", "name": "Support and follow", "link_slot": False},
    {"kind": "hashtags"},
)

# The text the starter button gives each starter block, for the user to replace.
STARTER_PLACEHOLDERS: dict[str, str] = {
    "Paid links": "Replace this with your paid-links disclosure.",
    "Support and follow": "Replace this with your support links.",
}

# States of a template section in a description.
OK, MISSING, OUT_OF_DATE, WRONG_PLACE, NOT_APPLICABLE, NOT_SET_UP = (
    "ok", "missing", "out_of_date", "wrong_place", "not_applicable", "not_set_up")

TRAY_REASON = "Not in the template"
HEADING_MAX = 60  # characters: a longer line ending in ":" is a sentence, not a heading

NO_TEMPLATE_YET = "No description template yet."


_PLACEHOLDER_KEYS = frozenset(" ".join(text.split()) for text in STARTER_PLACEHOLDERS.values())


def is_placeholder(block: TemplateBlock) -> bool:
    """The block still holds a starter placeholder text (whitespace aside), so it is not set up yet."""
    return " ".join(block.text.split()) in _PLACEHOLDER_KEYS


def _real_blocks(blocks: Sequence[TemplateBlock]) -> list[TemplateBlock]:
    """The blocks that are looked for in descriptions: every block but the placeholders."""
    return [b for b in blocks if not is_placeholder(b)]


# --- Which template a video follows ---------------------------------------------------------------

@dataclass(frozen=True)
class Assignment:
    """The template a video follows (None for none), whether it is left out, and why, in plain English."""

    template: DescriptionTemplate | None
    excluded: bool
    reason: str


def _squash(text: str) -> str:
    return " ".join(text.split())


def _title_has(title: str, phrase: str) -> bool:
    """The whole word or phrase is in the title, ignoring case; "pot" does not match "potato"."""
    words = _squash(phrase).split()
    if not words:
        return False
    pattern = r"(?<!\w)" + r"\s+".join(re.escape(w) for w in words) + r"(?!\w)"
    return re.search(pattern, title, re.IGNORECASE) is not None


def _rule_reason(rule: Mapping[str, object], video: Video, playlists_of_video: frozenset[str],
                 playlist_titles: Mapping[str, str]) -> str:
    """Why the rule applies to the video, or "" when it does not."""
    kind, value = rule.get("kind"), str(rule.get("value") or "")
    if kind == "playlist" and value in playlists_of_video:
        return f"In the playlist {playlist_titles.get(value) or value}."
    if kind == "title_word" and _title_has(video.title, value):
        return f"The title has the word {_squash(value)}."
    if kind == "tag":
        wanted = _squash(value).casefold()
        if any(_squash(tag).casefold() == wanted for tag in video.tags):
            return f"Tagged {_squash(value)}."
    return ""


def assign_template(video: Video, templates: Sequence[DescriptionTemplate],
                    choices: Mapping[str, int | None], include_shorts: bool,
                    memberships: Mapping[str, frozenset[str]],
                    playlist_titles: Mapping[str, str]) -> Assignment:
    """Pick the template for one video; the first step that applies wins.

    1. excluded by hand; 2. a template chosen by hand (if it still exists); 3. a Short while Shorts are off;
    4. the first template, in priority order and not counting the default, with a rule that matches;
    5. the default template; 6. none. `choices` maps video id to a template id, or None for excluded."""
    if video.video_id in choices and choices[video.video_id] is None:
        return Assignment(None, True, "You excluded this video.")
    by_id = {t.id: t for t in templates}
    chosen = choices.get(video.video_id)
    if chosen is not None and chosen in by_id:
        return Assignment(by_id[chosen], False, "Chosen on the video page.")
    if seo.is_short(video) and not include_shorts:
        return Assignment(None, True, "Shorts are excluded from templates.")
    ordered = sorted(templates, key=lambda t: (t.position, t.id))
    in_playlists = memberships.get(video.video_id, frozenset())
    for template in ordered:
        if template.is_default:
            continue
        for rule in template.rules:
            reason = _rule_reason(rule, video, in_playlists, playlist_titles)
            if reason:
                return Assignment(template, False, reason)
    default = next((t for t in ordered if t.is_default), None)
    if default is not None:
        return Assignment(default, False, "Default template.")
    if not ordered:
        return Assignment(None, False, NO_TEMPLATE_YET)
    return Assignment(None, False, "No template matches this video, and there is no default template.")


# --- Reading a description into sections ------------------------------------------------------------

@dataclass(frozen=True)
class SectionState:
    """One section of the template, as found in one description."""

    key: str  # "opening", "about", "chapters", "watch_next", "hashtags" or "block:<id>"
    kind: str  # the section kind; "block" for a shared block
    name: str  # what to call it: "Opening", ... or the block's name
    block_id: int | None
    state: str  # OK, MISSING, OUT_OF_DATE, WRONG_PLACE, NOT_APPLICABLE or NOT_SET_UP
    in_order: bool  # False when it was found, but not where the template puts it
    lines: tuple[int, ...]  # line numbers (from 1) it covers; () when it was not found
    link_lines: tuple[int, ...] = ()  # of a link-slot block: the video's own links below it


@dataclass(frozen=True)
class TrayItem:
    """Text in the description that belongs to no section of the template. Information only."""

    lines: tuple[int, ...]
    text: str
    reason: str


@dataclass(frozen=True)
class SectionReport:
    template_id: int
    template_name: str
    sections: tuple[SectionState, ...]  # one per template section, in the template's order
    tray: tuple[TrayItem, ...]
    block_lines: frozenset[int]  # line numbers of every library block found, its links and extra copies

    @property
    def found(self) -> tuple[SectionState, ...]:
        return tuple(s for s in self.sections if s.lines)

    @property
    def in_order(self) -> bool:
        return all(s.in_order for s in self.sections)


@dataclass(frozen=True)
class _Hit:
    block: TemplateBlock
    lines: tuple[int, ...]  # 0-based indexes of the block's own lines
    old: bool  # found only as an old version
    first: bool  # the first copy; later copies go to the tray
    links: tuple[int, ...] = ()  # link-slot block: the lines of the video's own links under it


def _norm(line: str) -> str:
    return " ".join(line.split())


def _block_target(text: str) -> list[str]:
    """The non-empty lines of a block text, whitespace collapsed."""
    return [t for t in (_norm(line) for line in text.split("\n")) if t]


def _line_kind(line: str) -> str:
    """"chapters", "hashtags", "watch_next", or "" for any other line."""
    if seo.timestamp_seconds(line) is not None:
        return "chapters"
    if seo.is_hashtag_line(line):
        return "hashtags"
    if seo.has_related_link(line):
        return "watch_next"
    return ""


def _scan(texts: list[str], filled: list[int], want: list[str], taken: set[int]) -> list[tuple[int, ...]]:
    """Every run of `want` among the non-empty lines (blank lines may lie between them), not overlapping."""
    found: list[tuple[int, ...]] = []
    size = len(want)
    i = 0
    while i + size <= len(texts):
        if texts[i:i + size] == want and not any(filled[k] in taken for k in range(i, i + size)):
            idx = tuple(filled[k] for k in range(i, i + size))
            found.append(idx)
            taken.update(idx)
            i += size
        else:
            i += 1
    return found


def _find_hits(lines: list[str], blocks: Sequence[TemplateBlock]) -> tuple[list[_Hit], set[int]]:
    """Where each library block is: its current text, else an old version. Returns the hits in line order
    and every line index they (and a link-slot block's links) cover."""
    normed = [_norm(line) for line in lines]
    filled = [i for i, text in enumerate(normed) if text]
    texts = [normed[i] for i in filled]
    taken: set[int] = set()
    hits: list[_Hit] = []
    # longer blocks first, so a block that holds another block's text wins the lines
    for block in sorted(blocks, key=lambda b: (-len(_block_target(b.text)), b.id)):
        # every version is scanned, the current text first so it wins lines it shares with an old one;
        # the first copy in line order is the block's hit, later copies (of any version) are extras
        runs: list[tuple[tuple[int, ...], bool]] = []
        for version, text in enumerate((block.text, *block.old_versions)):
            want = _block_target(text)
            if want:
                runs.extend((run, version > 0) for run in _scan(texts, filled, want, taken))
        runs.sort(key=lambda r: r[0][0])
        hits.extend(_Hit(block, run, old, n == 0) for n, (run, old) in enumerate(runs))
    hits.sort(key=lambda h: h.lines[0])
    for n, hit in enumerate(hits):
        if not (hit.first and hit.block.link_slot):
            continue
        links: list[int] = []
        j = hit.lines[-1] + 1
        while (j < len(lines) and normed[j] and j not in taken and not _line_kind(lines[j])
               and seo.has_url(normed[j])):
            links.append(j)
            taken.add(j)
            j += 1
        hits[n] = _Hit(hit.block, hit.lines, hit.old, hit.first, tuple(links))
    return hits, taken


def find_block_lines(description: str, blocks: Sequence[TemplateBlock]) -> frozenset[int]:
    """Line numbers (from 1) of every library block found in the description: current text, old versions,
    extra copies and the links under a link-slot block. Placeholder blocks are not looked for."""
    blocks = _real_blocks(blocks)
    if not blocks:
        return frozenset()
    _, taken = _find_hits(description.split("\n"), blocks)
    return frozenset(i + 1 for i in taken)


def own_text_chars(description: str, block_lines: Iterable[int]) -> int:
    """Characters of the description without the lines in `block_lines` (numbers from 1)."""
    skip = set(block_lines)
    kept = [line for number, line in enumerate(description.split("\n"), 1) if number not in skip]
    return len("\n".join(kept).strip())


def _is_heading(line: str) -> bool:
    text = line.strip()
    return bool(text) and text.endswith(":") and len(text) <= HEADING_MAX and not seo.has_url(text)


def _keep_in_order(sequence: list[int]) -> set[int]:
    """Positions of the longest strictly rising run in `sequence`; of equal runs, the one that keeps the
    sections found earliest."""
    n = len(sequence)
    longest = [1] * n
    for i in range(n - 1, -1, -1):
        for j in range(i + 1, n):
            if sequence[j] > sequence[i]:
                longest[i] = max(longest[i], 1 + longest[j])
    keep: set[int] = set()
    need = max(longest, default=0)
    last = -1
    for i in range(n):
        if need and longest[i] == need and (last < 0 or sequence[i] > sequence[last]):
            keep.add(i)
            last = i
            need -= 1
    return keep


def _tray_items(lines: list[str], reasons: dict[int, str]) -> list[TrayItem]:
    """Group tray lines (index -> reason) that follow each other with the same reason."""
    items: list[TrayItem] = []
    run: list[int] = []
    for i in sorted(reasons):
        if run and (i != run[-1] + 1 or reasons[i] != reasons[run[0]]):
            items.append(TrayItem(tuple(n + 1 for n in run), "\n".join(lines[n] for n in run), reasons[run[0]]))
            run = []
        run.append(i)
    if run:
        items.append(TrayItem(tuple(n + 1 for n in run), "\n".join(lines[n] for n in run), reasons[run[0]]))
    return items


def parse_description(description: str, template: DescriptionTemplate, blocks: Sequence[TemplateBlock],
                      video: Video) -> SectionReport:
    """Read a description line by line into the sections of `template`. The text is never changed.

    `blocks` is the whole shared library. Fixed blocks are found by their text (the current one, else an
    old version); the rest is sorted by what each line is: timestamps are chapters, a line of only hashtags
    is the hashtags, a YouTube video or playlist link is Watch next. Plain paragraphs are the opening (the
    first, if the description starts with it) or about (before the first fixed block or hashtags line).
    Anything else goes to the tray. A block that still holds its starter placeholder is not looked for; its
    section is NOT_SET_UP."""
    lines = description.split("\n")
    library = {b.id: b for b in blocks}
    wanted_blocks = {s["block_id"] for s in template.sections if s.get("kind") == "block"}
    wanted_kinds = {s["kind"] for s in template.sections}
    hits, taken = _find_hits(lines, _real_blocks(blocks))

    owner: dict[int, str] = {}  # line index -> section key, for lines that belong to a section
    tray_lines: dict[int, str] = {}  # line index -> why it is in the tray
    tray: list[TrayItem] = []
    found_block: dict[int, _Hit] = {}
    for hit in hits:
        covered = hit.lines + hit.links
        if not hit.first:
            tray.append(TrayItem(tuple(i + 1 for i in covered), "\n".join(lines[i] for i in covered),
                                 f"A second copy of {hit.block.name}"))
        elif hit.block.id not in wanted_blocks:
            tray.append(TrayItem(tuple(i + 1 for i in covered), "\n".join(lines[i] for i in covered),
                                 f"{hit.block.name} is not in this template"))
        else:
            found_block[hit.block.id] = hit
            for i in covered:
                owner[i] = f"block:{hit.block.id}"

    # lines that are chapters, hashtags or Watch next links
    kinds: dict[int, str] = {}
    for i, line in enumerate(lines):
        if i not in taken and line.strip():
            kind = _line_kind(line)
            if kind:
                kinds[i] = kind
    for i in sorted(kinds):
        # a short heading ending in ":" directly above a chapters or Watch next run belongs to it
        if (kinds[i] in ("chapters", "watch_next") and i > 0
                and i - 1 not in taken and i - 1 not in kinds and _is_heading(lines[i - 1])):
            kinds[i - 1] = kinds[i]
    for i, kind in kinds.items():
        if kind in wanted_kinds:
            owner[i] = kind
        else:
            tray_lines[i] = TRAY_REASON

    # plain text and links that belong to nothing
    plain: list[int] = []
    for i, line in enumerate(lines):
        if i in taken or i in kinds or not line.strip():
            continue
        if seo.has_url(line):
            tray_lines[i] = TRAY_REASON
        else:
            plain.append(i)
    first_line = next((i for i, line in enumerate(lines) if line.strip()), -1)
    stops = [h.lines[0] for h in hits] + [i for i, kind in kinds.items() if kind == "hashtags"]
    boundary = min(stops, default=len(lines))
    paragraphs: list[list[int]] = []
    for i in plain:
        if paragraphs and paragraphs[-1][-1] == i - 1:
            paragraphs[-1].append(i)
        else:
            paragraphs.append([i])
    for paragraph in paragraphs:
        if paragraph[0] == first_line:
            kind = "opening"
        elif paragraph[0] < boundary:
            kind = "about"
        else:
            kind = ""
        for i in paragraph:
            if kind and kind in wanted_kinds:
                owner[i] = kind
            else:
                tray_lines[i] = TRAY_REASON
    tray.extend(_tray_items(lines, tray_lines))
    tray.sort(key=lambda item: item.lines[0])

    # one state per template section
    seconds = seo.parse_duration(video.duration)
    short_video = seconds is not None and seconds < seo.CHAPTERS_MIN_DURATION
    rows: list[tuple[str, str, str, int | None, str, tuple[int, ...], tuple[int, ...]]] = []
    for section in template.sections:
        kind = section["kind"]
        if kind == "block":
            block = library.get(section["block_id"])
            if block is None:
                continue
            if is_placeholder(block):
                rows.append((f"block:{block.id}", "block", block.name, block.id, NOT_SET_UP, (), ()))
                continue
            hit = found_block.get(block.id)
            lines_of = tuple(sorted(i + 1 for i in (hit.lines + hit.links))) if hit else ()
            state = MISSING if hit is None else OUT_OF_DATE if hit.old else OK
            rows.append((f"block:{block.id}", "block", block.name, block.id, state, lines_of,
                         tuple(i + 1 for i in hit.links) if hit else ()))
        else:
            lines_of = tuple(sorted(i + 1 for i, key in owner.items() if key == kind))
            state = OK if lines_of else NOT_APPLICABLE if kind == "chapters" and short_video else MISSING
            rows.append((kind, kind, SECTION_LABELS[kind], None, state, lines_of, ()))

    # order: the longest run of found sections that follows the template; the rest is out of place
    present = sorted((r for r in range(len(rows)) if rows[r][5]), key=lambda r: rows[r][5][0])
    keep = {present[p] for p in _keep_in_order(present)}
    sections: list[SectionState] = []
    for r, (key, kind, name, block_id, state, lines_of, links) in enumerate(rows):
        in_order = not lines_of or r in keep
        if not in_order and state == OK:
            state = WRONG_PLACE
        sections.append(SectionState(key, kind, name, block_id, state, in_order, lines_of, links))
    return SectionReport(template.id, template.name, tuple(sections), tuple(tray),
                         frozenset(i + 1 for i in taken))


# --- Scoring ---------------------------------------------------------------------------------------

def judge_report(template: DescriptionTemplate, report: SectionReport, reason: str = "") -> TemplateJudgment:
    """Turn a parsed description into what the SEO check needs: 7 points for the shared blocks (1 for
    current, half for an old version, none for missing; rounded down) and 3 for the order. A block that is
    not set up (it still holds the starter placeholder) counts in neither, as if the template did not have it."""
    block_states = [s for s in report.sections if s.kind == "block" and s.state != NOT_SET_UP]
    halves = sum(2 if s.state in (OK, WRONG_PLACE) else 1 if s.state == OUT_OF_DATE else 0
                 for s in block_states)
    if block_states:
        fixed = seo.TEMPLATE_FIXED_POINTS * halves // (2 * len(block_states))
    else:
        fixed = seo.TEMPLATE_FIXED_POINTS
    found_blocks = sum(1 for s in block_states if s.lines)
    enough = found_blocks >= 1 if block_states else len(report.found) >= 2
    return TemplateJudgment(
        template_name=template.name,
        applicable=True,
        reason=reason,
        fixed_points=fixed,
        order_ok=report.in_order and enough,
        blocks_total=len(block_states),
        blocks_current=sum(1 for s in block_states if s.state in (OK, WRONG_PLACE)),
        missing=tuple(s.name for s in block_states if s.state == MISSING),
        out_of_date=tuple(s.name for s in block_states if s.state == OUT_OF_DATE),
        wrong_place=tuple(s.name for s in report.sections if not s.in_order),
        not_set_up=tuple(s.name for s in report.sections if s.state == NOT_SET_UP),
        template_id=template.id,
    )


def judge_catalog(store: Store, videos: Iterable[Video]) -> tuple[dict[str, TemplateJudgment], dict[str, int]]:
    """Judge every video against the template it follows. Returns (judgments, own_chars), the inputs of
    `seo.score_catalog`. `own_chars` has an entry only for a video that has shared blocks in it."""
    blocks = store.list_template_blocks()
    templates = store.list_description_templates()
    choices = store.template_choices()
    include_shorts = store.templates_include_shorts()
    memberships = store.playlist_memberships()
    titles = {p.playlist_id: p.title for p in store.playlists()}
    judgments: dict[str, TemplateJudgment] = {}
    own_chars: dict[str, int] = {}
    for video in videos:
        assignment = assign_template(video, templates, choices, include_shorts, memberships, titles)
        if assignment.template is None:
            judgments[video.video_id] = TemplateJudgment(
                applicable=False, reason=assignment.reason, excluded=assignment.excluded)
            block_lines = find_block_lines(video.description, blocks)
        else:
            report = parse_description(video.description, assignment.template, blocks, video)
            judgments[video.video_id] = judge_report(assignment.template, report, assignment.reason)
            block_lines = report.block_lines
        if block_lines:
            own_chars[video.video_id] = own_text_chars(video.description, block_lines)
    return judgments, own_chars


# --- Coverage ----------------------------------------------------------------------------------------

@dataclass(frozen=True)
class TemplateCoverage:
    template_id: int
    name: str
    following: int  # videos that follow it fully (10 of 10)
    needs_changes: int


@dataclass(frozen=True)
class Coverage:
    templates: tuple[TemplateCoverage, ...]
    excluded: int  # videos left out of templates
    no_template: int  # videos no template applies to


def coverage(videos: Iterable[Video], judgments: Mapping[str, TemplateJudgment],
             templates: Sequence[DescriptionTemplate] = ()) -> Coverage:
    """Count the videos per template: those that follow it fully and those that need changes, plus the
    videos left out and the videos with no template. `templates` lists the rows to show, in order (also
    those with no videos); templates with videos that it does not list come after it, by id, so every
    video is counted once."""
    full = seo.TEMPLATE_FIXED_POINTS + seo.TEMPLATE_ORDER_POINTS
    names = {t.id: t.name for t in templates}
    following: dict[int, int] = {t.id: 0 for t in templates}
    needs: dict[int, int] = {t.id: 0 for t in templates}
    excluded = no_template = 0
    for video in videos:
        judgment = judgments.get(video.video_id)
        if judgment is None or (not judgment.applicable and not judgment.excluded):
            no_template += 1
        elif not judgment.applicable:
            excluded += 1
        else:
            names.setdefault(judgment.template_id, judgment.template_name)
            bucket = following if judgment.points >= full else needs
            bucket[judgment.template_id] = bucket.get(judgment.template_id, 0) + 1
    listed = [t.id for t in templates]
    # a template a judgment names but `templates` does not list (deleted since?) comes after, by id
    order = listed + sorted(i for i in names if i not in set(listed))
    rows = tuple(TemplateCoverage(i, names[i], following.get(i, 0), needs.get(i, 0)) for i in order)
    return Coverage(rows, excluded, no_template)
