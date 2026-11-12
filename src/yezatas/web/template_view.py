"""The Description templates page: the coverage strip, the template chips, the chosen template's rules and
sections, the shared blocks and a read-only preview of one video against the template.

Pure functions over what the route read from the Store; nothing here writes or calls a model.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from .. import descriptiontemplate as dt
from .. import seo
from ..store import PER_VIDEO_SECTIONS, DescriptionTemplate, Playlist, TemplateBlock, Video
from ..validate import DESCRIPTION_BYTE_LIMIT

PAGE_URL = "/description-templates"
STARTER_NAME = "Main"
# One line under each per-video section, as in the mock-up.
SECTION_HINTS: dict[str, str] = {
    "opening": 'First lines, with the main words before "more".',
    "about": "The video's own text, kept word for word.",
    "chapters": "From real captions only.",
    "watch_next": "A related video from your catalog.",
    "hashtags": "1 to 3 about the video, always last.",
}
STATE_LABELS: dict[str, str] = {
    dt.OK: "OK", dt.MISSING: "Missing", dt.OUT_OF_DATE: "Out of date", dt.WRONG_PLACE: "Wrong place",
    dt.NOT_APPLICABLE: "Not applicable", dt.NOT_SET_UP: "Not set up",
}
STATE_TONES: dict[str, str] = {
    dt.OK: "done", dt.MISSING: "warn", dt.OUT_OF_DATE: "warn", dt.WRONG_PLACE: "warn", dt.NOT_APPLICABLE: "",
    dt.NOT_SET_UP: "",
}


def section_key(section: Mapping[str, Any]) -> str:
    """"opening", ... or "block:<id>": how a section is named in forms."""
    return f"block:{section.get('block_id')}" if section.get("kind") == "block" else str(section.get("kind"))


GONE_PLAYLIST = "Playlist no longer on YouTube"  # a playlist rule whose playlist is not stored any more


def rule_text(rule: Mapping[str, Any], playlist_titles: Mapping[str, str]) -> str:
    kind, value = rule.get("kind"), str(rule.get("value") or "")
    if kind == "playlist":
        title = playlist_titles.get(value)
        return f"In the playlist {title}" if title is not None else GONE_PLAYLIST
    if kind == "title_word":
        return f'"{value}" in the title'
    return f"Tagged {value}"


# --- the view model ------------------------------------------------------------------------------------

@dataclass(frozen=True)
class CoverView:
    following: int
    needs_changes: int
    excluded: int
    no_template: int
    needs_url: str  # the Videos filter for videos that fail the template check
    excluded_url: str = "/videos?template=excluded"  # the Videos filter for videos left out
    none_url: str = "/videos?template=none"  # the Videos filter for videos no template applies to


@dataclass(frozen=True)
class ChipView:
    id: int
    name: str
    count: int  # videos that follow it (fully or with changes needed)
    is_default: bool
    active: bool
    url: str


@dataclass(frozen=True)
class RuleView:
    kind: str
    value: str
    text: str


@dataclass(frozen=True)
class SectionRow:
    key: str
    name: str
    hint: str
    is_block: bool
    block_id: int | None
    block_lines: tuple[str, ...]  # the block's current text, line by line
    placeholder: bool
    shared_with: str  # "Used by this template only." or "Also in Reviews and Tutorials."
    count_text: str  # "All 12", "3 missing, 1 out of date" or "No videos yet"
    warn: bool
    can_up: bool
    can_down: bool


@dataclass(frozen=True)
class TemplateView:
    id: int
    name: str
    is_default: bool
    video_count: int
    hand_picked: int  # videos with this template chosen by hand: they lose the choice on delete
    can_up: bool
    can_down: bool
    rules: tuple[RuleView, ...]
    sections: tuple[SectionRow, ...]
    addable_kinds: tuple[tuple[str, str], ...]  # (kind, label)
    addable_blocks: tuple[tuple[int, str], ...]  # (block id, name)


@dataclass(frozen=True)
class BlockView:
    id: int
    name: str
    text: str
    link_slot: bool
    old_count: int
    used_by: tuple[str, ...]
    placeholder: bool


@dataclass(frozen=True)
class PreviewSection:
    name: str
    kind_label: str
    state: str
    label: str
    tone: str


@dataclass(frozen=True)
class PreviewLine:
    number: int | None  # None for the "more" marker and for the rest of a line it cut
    text: str
    css: str  # "", "block", "tray" or "more"


@dataclass(frozen=True)
class PreviewView:
    video_id: str
    note: str  # when the video follows another template, or none
    sections: tuple[PreviewSection, ...]
    tray: tuple[dt.TrayItem, ...]
    points: int
    max_points: int
    detail: str
    fix: str
    bytes_used: int
    bytes_limit: int
    over: bool
    lines: tuple[PreviewLine, ...]
    fits_before_more: bool  # the whole description shows before "more"


@dataclass(frozen=True)
class PreviewChoice:
    video_id: str
    title: str


@dataclass(frozen=True)
class TemplatesPage:
    empty: bool
    cover: CoverView
    chips: tuple[ChipView, ...]
    chosen: TemplateView | None
    blocks: tuple[BlockView, ...]
    include_shorts: bool
    playlists: tuple[tuple[str, str, int], ...]  # (playlist id, title, how many of the catalog's videos it holds)
    following_videos: tuple[PreviewChoice, ...]
    other_videos: tuple[PreviewChoice, ...]
    preview: PreviewView | None
    notice: str  # a sentence about the address (an unknown template id)
    typed: dict[str, dict[str, str]] = field(default_factory=dict)
    starter_blocks: tuple[str, ...] = ()


def _int(raw: Any) -> int | None:
    text = str(raw or "")
    return int(text) if text.isascii() and text.isdigit() and len(text) <= 12 else None


def _names(names: Sequence[str]) -> str:
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


def _count_text(states: Counter[str], total: int) -> tuple[str, bool]:
    if total == 0:
        return "No videos yet", False
    parts = [f"{states[s]} {label}" for s, label in ((dt.MISSING, "missing"), (dt.OUT_OF_DATE, "out of date"),
                                                      (dt.WRONG_PLACE, "in the wrong place")) if states[s]]
    if not parts:
        return f"All {total}", False
    return ", ".join(parts), True


def preview_lines(description: str, report: dt.SectionReport | None, window: int = seo.HOOK_WINDOW
                  ) -> tuple[tuple[PreviewLine, ...], bool]:
    """The description line by line, with block and tray lines marked and a marker where the first
    `window` characters end. Returns (lines, the whole text fits before the marker)."""
    css: dict[int, str] = {}
    if report is not None:
        for section in report.sections:
            if section.kind == "block":
                for n in section.lines:
                    css[n] = "block"
        for item in report.tray:
            for n in item.lines:
                css[n] = "tray"
    rows: list[PreviewLine] = []
    placed = len(description) <= window
    pos = 0
    marker = PreviewLine(None, '"more" falls about here', "more")
    for number, line in enumerate(description.split("\n"), 1):
        start, end = pos, pos + len(line)
        if not placed and start >= window:
            rows.append(marker)
            placed = True
        if not placed and end > window:
            cut = window - start
            rows.append(PreviewLine(number, line[:cut], css.get(number, "")))
            rows.append(marker)
            rows.append(PreviewLine(None, line[cut:], css.get(number, "")))
            placed = True
        else:
            rows.append(PreviewLine(number, line, css.get(number, "")))
        pos = end + 1
    return tuple(rows), len(description) <= window


def build_preview(video: Video, template: DescriptionTemplate, blocks: Sequence[TemplateBlock],
                  judgment: seo.TemplateJudgment | None, templates_by_id: Mapping[int, DescriptionTemplate]
                  ) -> PreviewView:
    """One video read against `template`: each section's state, the tray, the check's points and the text."""
    report = dt.parse_description(video.description, template, blocks, video)
    reason = judgment.reason if judgment is not None and judgment.template_id == template.id else ""
    mine = dt.judge_report(template, report, reason)
    result = seo.check_description_template(video, seo.ChannelContext(templates={video.video_id: mine}))
    note = ""
    if judgment is not None and not judgment.applicable:
        note = f"{judgment.reason} The preview shows it against \"{template.name}\" anyway."
    elif judgment is not None and judgment.template_id != template.id:
        other = templates_by_id.get(judgment.template_id)
        name = other.name if other is not None else judgment.template_name
        note = f"This video follows \"{name}\". The preview shows it against \"{template.name}\"."
    sections = tuple(PreviewSection(s.name, "Shared block" if s.kind == "block" else "Per video", s.state,
                                    STATE_LABELS[s.state], STATE_TONES[s.state]) for s in report.sections)
    size = len(video.description.encode("utf-8"))
    lines, fits = preview_lines(video.description, report)
    return PreviewView(video.video_id, note, sections, report.tray, result.points, result.max_points,
                       result.detail, result.fix, size, DESCRIPTION_BYTE_LIMIT, size > DESCRIPTION_BYTE_LIMIT,
                       lines, fits)


def build_templates_page(videos: Sequence[Video], blocks: Sequence[TemplateBlock],
                         templates: Sequence[DescriptionTemplate], choices: Mapping[str, int | None],
                         include_shorts: bool, playlists: Sequence[Playlist],
                         judgments: Mapping[str, seo.TemplateJudgment], params: Mapping[str, str],
                         typed: Mapping[str, dict[str, str]] | None = None) -> TemplatesPage:
    """Everything the page shows. `params` is the query string (`template`, `video`)."""
    cover_data = dt.coverage(videos, judgments, templates)
    cover = CoverView(sum(t.following for t in cover_data.templates),
                      sum(t.needs_changes for t in cover_data.templates),
                      cover_data.excluded, cover_data.no_template,
                      "/videos?check=description_template")
    counts = {t.template_id: t.following + t.needs_changes for t in cover_data.templates}
    by_id = {t.id: t for t in templates}
    titles = {p.playlist_id: p.title for p in playlists}
    catalog_ids = {v.video_id for v in videos}

    notice = ""
    wanted = params.get("template")
    chosen_t: DescriptionTemplate | None = None
    if wanted:
        chosen_t = by_id.get(_int(wanted) or -1)
        if chosen_t is None:
            notice = "That template does not exist. Showing the first one instead."
    if chosen_t is None and templates:
        chosen_t = next((t for t in templates if t.is_default), templates[0])

    chips = tuple(ChipView(t.id, t.name, counts.get(t.id, 0), t.is_default,
                           chosen_t is not None and t.id == chosen_t.id, f"{PAGE_URL}?template={t.id}#template")
                  for t in templates)

    used_by: dict[int, list[tuple[int, str]]] = {}
    for t in templates:
        for s in t.sections:
            if s.get("kind") == "block":
                used_by.setdefault(int(s["block_id"]), []).append((t.id, t.name))
    block_views = tuple(BlockView(b.id, b.name, b.text, b.link_slot, len(dt.real_old_versions(b)),
                                  tuple(name for _, name in used_by.get(b.id, ())), dt.is_placeholder(b))
                        for b in blocks)

    by_views = sorted(videos, key=lambda v: (-v.view_count, v.title.casefold(), v.video_id))
    chosen: TemplateView | None = None
    following: list[Video] = []
    preview: PreviewView | None = None
    if chosen_t is not None:
        following = [v for v in by_views if (j := judgments.get(v.video_id)) is not None
                     and j.applicable and j.template_id == chosen_t.id]
        chosen = _template_view(chosen_t, templates, blocks, following, choices, titles, used_by)
        video_param = str(params.get("video") or "")
        pick = next((v for v in videos if v.video_id == video_param), None) if video_param else None
        if pick is None and following:
            pick = following[0]
        if pick is not None:
            preview = build_preview(pick, chosen_t, blocks, judgments.get(pick.video_id), by_id)
    follow_ids = {v.video_id for v in following}
    return TemplatesPage(
        empty=not templates, cover=cover, chips=chips, chosen=chosen, blocks=block_views,
        include_shorts=include_shorts,
        playlists=tuple((p.playlist_id, p.title, sum(1 for vid in p.video_ids if vid in catalog_ids))
                        for p in playlists),
        following_videos=tuple(PreviewChoice(v.video_id, v.title) for v in following),
        other_videos=tuple(PreviewChoice(v.video_id, v.title) for v in by_views if v.video_id not in follow_ids),
        preview=preview, notice=notice, typed=dict(typed or {}),
        starter_blocks=tuple(str(s["name"]) for s in dt.STARTER_SECTIONS if s["kind"] == "block"))


def _template_view(template: DescriptionTemplate, templates: Sequence[DescriptionTemplate],
                   blocks: Sequence[TemplateBlock], following: Sequence[Video],
                   choices: Mapping[str, int | None], titles: Mapping[str, str],
                   used_by: Mapping[int, list[tuple[int, str]]]) -> TemplateView:
    library = {b.id: b for b in blocks}
    states: dict[str, Counter[str]] = {}
    for video in following:
        report = dt.parse_description(video.description, template, blocks, video)
        for s in report.sections:
            states.setdefault(s.key, Counter())[s.state] += 1
    rows: list[SectionRow] = []
    sections = [s for s in template.sections if s.get("kind") != "block" or s.get("block_id") in library]
    for n, section in enumerate(sections):
        key = section_key(section)
        text, warn = _count_text(states.get(key, Counter()), len(following))
        if section.get("kind") == "block":
            block = library[int(section["block_id"])]
            others = [name for tid, name in used_by.get(block.id, []) if tid != template.id]
            shared = f"Also in {_names(others)}." if others else "Used by this template only."
            hint = "Disclosure directly above the links; the links are each video's own." if block.link_slot else ""
            placeholder = dt.is_placeholder(block)
            if placeholder:  # not looked for in descriptions, so there is nothing to count
                text, warn = "", False
            rows.append(SectionRow(key, block.name, hint, True, block.id, tuple(block.text.split("\n")),
                                   placeholder, shared, text, warn, n > 0, n < len(sections) - 1))
        else:
            kind = str(section.get("kind"))
            rows.append(SectionRow(key, dt.SECTION_LABELS[kind], SECTION_HINTS[kind], False, None, (), False, "",
                                   text, warn, n > 0, n < len(sections) - 1))
    have = {section_key(s) for s in sections}
    index = [t.id for t in templates].index(template.id)
    return TemplateView(
        id=template.id, name=template.name, is_default=template.is_default, video_count=len(following),
        hand_picked=sum(1 for v in choices.values() if v == template.id),
        can_up=index > 0, can_down=index < len(templates) - 1,
        rules=tuple(RuleView(str(r.get("kind")), str(r.get("value") or ""), rule_text(r, titles))
                    for r in template.rules),
        sections=tuple(rows),
        addable_kinds=tuple((k, dt.SECTION_LABELS[k]) for k in PER_VIDEO_SECTIONS if k not in have),
        addable_blocks=tuple((b.id, b.name) for b in blocks if f"block:{b.id}" not in have),
    )


# --- turning a form into the store's input -------------------------------------------------------------

def moved(items: Sequence[Any], index: int, direction: str) -> list[Any]:
    """`items` with the item at `index` moved one place up or down. Raises ValueError at an edge."""
    out = list(items)
    other = index - 1 if direction == "up" else index + 1 if direction == "down" else -1
    if not (0 <= index < len(out)) or not (0 <= other < len(out)):
        raise ValueError("That cannot move any further.")
    out[index], out[other] = out[other], out[index]
    return out


def parse_section(value: str) -> dict[str, Any]:
    """A section from a form value: "opening", ... or "block:<id>". Raises ValueError otherwise."""
    if value.startswith("block:"):
        block_id = _int(value[len("block:"):])
        if block_id is None:
            raise ValueError("Choose a section or a shared block to add.")
        return {"kind": "block", "block_id": block_id}
    if value in PER_VIDEO_SECTIONS:
        return {"kind": value}
    raise ValueError("Choose a section or a shared block to add.")
