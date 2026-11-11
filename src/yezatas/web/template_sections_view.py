"""View model for the description template on the video page's Edit card: the template menu, the state, the
Sections / Plain text tabs and the read-only Sections view with the "Not in the template" tray."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from .. import descriptiontemplate as dt
from .. import seo
from ..store import DescriptionTemplate, TemplateBlock, Video
from .template_view import STATE_LABELS, STATE_TONES, PreviewLine, preview_lines

AUTO, EXCLUDE = "auto", "exclude"
SECTIONS, TEXT = "sections", "text"
# Tools on this page that make a missing per-video section: (anchor, link text). No others are invented.
SECTION_TOOLS: dict[str, tuple[str, str]] = {
    "chapters": ("#tool-chapters", "Make chapters"),
    "opening": ("#tool-suggest", "Suggestions for the opening"),
}
FULL_POINTS = seo.TEMPLATE_FIXED_POINTS + seo.TEMPLATE_ORDER_POINTS


@dataclass(frozen=True)
class MenuOption:
    value: str  # "auto", "exclude" or a template id
    label: str
    selected: bool


@dataclass(frozen=True)
class SectionRow:
    key: str
    name: str
    kind_label: str  # "Per video" or "Shared block"
    from_template: bool
    label: str  # the state, as on the Description templates preview
    tone: str
    lines: tuple[PreviewLine, ...]  # the section's text, with the "more" marker where it falls
    tool: tuple[str, str] | None  # (anchor, text) of the tool that makes a missing per-video section


@dataclass(frozen=True)
class TrayRow:
    key: str
    text: str
    reason: str


@dataclass(frozen=True)
class SectionsView:
    view: str  # "sections" or "text"
    has_templates: bool
    menu: tuple[MenuOption, ...]
    template_name: str  # the template the video follows, or ""
    excluded: bool
    reason: str  # why, from assign_template
    points: int | None  # the template check on the text on YouTube, None when it does not apply
    max_points: int
    from_draft: bool  # the sections show the draft
    rows: tuple[SectionRow, ...]
    tray: tuple[TrayRow, ...]
    has_draft: bool  # Apply is refused while a draft exists
    draft_points: int | None  # the template check on the draft's text, when there is a draft


def _section_lines(rows: Sequence[PreviewLine], numbers: Sequence[int]) -> tuple[PreviewLine, ...]:
    """The preview rows of these line numbers; a "more" marker or the rest of a cut line goes with the line
    before it."""
    wanted, out, owner = set(numbers), [], 0
    for row in rows:
        if row.number is not None:
            owner = row.number
        if owner in wanted:
            out.append(PreviewLine(row.number, row.text, "more" if row.css == "more" else ""))
    return tuple(out)


def _points(template: DescriptionTemplate, report: dt.SectionReport, reason: str, video: Video) -> int:
    judgment = dt.judge_report(template, report, reason)
    return seo.check_description_template(video, seo.ChannelContext(templates={video.video_id: judgment})).points


def build_sections_view(video: Video, shown_description: str, has_draft: bool,
                        templates: Sequence[DescriptionTemplate], blocks: Sequence[TemplateBlock],
                        choices: Mapping[str, int | None], include_shorts: bool,
                        memberships: Mapping[str, frozenset[str]], playlist_titles: Mapping[str, str],
                        params: Mapping[str, str]) -> SectionsView:
    """Everything the Edit card shows about the video's template. `shown_description` is the text the Edit
    form shows (the draft when there is one, else the text on YouTube)."""
    ordered = sorted(templates, key=lambda t: (t.position, t.id))
    assignment = dt.assign_template(video, ordered, choices, include_shorts, memberships, playlist_titles)
    others = {k: v for k, v in choices.items() if k != video.video_id}
    auto = dt.assign_template(video, ordered, others, include_shorts, memberships, playlist_titles)
    chosen = choices.get(video.video_id, AUTO) if video.video_id in choices else AUTO
    selected = EXCLUDE if video.video_id in choices and chosen is None else str(chosen)
    auto_label = (f"Automatic: {auto.template.name}" if auto.template is not None
                  else "Automatic: left out" if auto.excluded else "Automatic: none")
    menu = ((MenuOption(AUTO, f"{auto_label} ({auto.reason.rstrip('.')})", selected == AUTO),)
            + tuple(MenuOption(str(t.id), t.name, selected == str(t.id)) for t in ordered)
            + (MenuOption(EXCLUDE, "Exclude this video", selected == EXCLUDE),))
    template = assignment.template
    asked = str(params.get("view") or "")
    view = asked if asked in (SECTIONS, TEXT) else (SECTIONS if template is not None else TEXT)
    rows: tuple[SectionRow, ...] = ()
    tray: tuple[TrayRow, ...] = ()
    points = draft_points = None
    if template is not None:
        youtube_report = dt.parse_description(video.description, template, blocks, video)
        points = _points(template, youtube_report, assignment.reason, video)
        report = (dt.parse_description(shown_description, template, blocks, video) if has_draft
                  else youtube_report)
        if has_draft:
            draft_points = _points(template, report, assignment.reason, video)
        lines, _ = preview_lines(shown_description, report)
        rows = tuple(SectionRow(
            s.key, s.name, "Shared block" if s.kind == "block" else "Per video", s.kind == "block",
            STATE_LABELS[s.state], STATE_TONES[s.state], _section_lines(lines, s.lines),
            SECTION_TOOLS.get(s.kind) if s.state == dt.MISSING else None) for s in report.sections)
        tray = tuple(TrayRow(item.key, item.text, item.reason) for item in report.tray)
    return SectionsView(view, bool(templates), menu, template.name if template is not None else "",
                        assignment.excluded, assignment.reason, points, FULL_POINTS, has_draft, rows, tray,
                        has_draft, draft_points)
