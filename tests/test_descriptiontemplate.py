"""Description templates: assignment, the line parser, the 7/3 scoring, judge_catalog and coverage.

All channel data is invented (a gardening channel); fixed blocks link to `shop.test` / `links.invalid`."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from yezatas import seo
from yezatas.descriptiontemplate import (MISSING, NOT_APPLICABLE, NOT_SET_UP, OK, OUT_OF_DATE,
                                         STARTER_PLACEHOLDERS, STARTER_SECTIONS, TRAY_REASON, WRONG_PLACE,
                                         assign_template, coverage, find_block_lines, is_placeholder,
                                         judge_catalog, judge_report, own_text_chars, parse_description)
from yezatas.seo import TemplateJudgment
from yezatas.store import DescriptionTemplate, Store, TemplateBlock, Video

PAID = TemplateBlock(1, "Paid links", "Some links below are paid links; they support the channel.", (), True)
SUPPORT = TemplateBlock(2, "Support and follow",
                        "Support the garden channel:\nhttps://shop.test/support",
                        ("Support us:\nhttps://shop.test/old-support",), False)
NEWSLETTER = TemplateBlock(3, "Newsletter", "Get the weekly seed list: https://links.invalid/seeds", (), False)
LIBRARY = [PAID, SUPPORT, NEWSLETTER]
OLD_SUPPORT = "Support us:\nhttps://shop.test/old-support"

FULL_SECTIONS = ({"kind": "opening"}, {"kind": "about"}, {"kind": "chapters"}, {"kind": "watch_next"},
                 {"kind": "block", "block_id": 1}, {"kind": "block", "block_id": 2}, {"kind": "hashtags"})

OPENING = "We build a raised garden bed in one day and plant the first seeds of the season."
ABOUT = "The soil mix is one part compost to two parts topsoil."
CHAPTER_LINES = "0:00 Intro\n1:30 Soil mix\n4:10 Planting"
WATCH = "Watch next: https://youtu.be/abc123XYZ00"
PAID_TEXT = PAID.text + "\nhttps://shop.test/trowel\nhttps://shop.test/gloves"
HASHTAGS = "#gardening #raisedbed"


def template(id: int = 1, name: str = "Main", position: int = 0, is_default: bool = False,
             sections=FULL_SECTIONS, rules=()) -> DescriptionTemplate:
    return DescriptionTemplate(id, name, position, is_default, tuple(sections), tuple(rules))


def make_video(vid: str = "v1", **kw) -> Video:
    base = Video(video_id=vid, title="How to build a raised garden bed", description="",
                 tags=["garden bed", "raised bed"], duration="PT10M3S")
    return replace(base, **kw)


def doc(*parts: str) -> str:
    """Paragraphs joined by a blank line."""
    return "\n\n".join(parts)


FULL = doc(OPENING, ABOUT, CHAPTER_LINES, WATCH, PAID_TEXT, SUPPORT.text, HASHTAGS)


def parse(description: str, tpl: DescriptionTemplate | None = None, blocks=LIBRARY, video: Video | None = None):
    return parse_description(description, tpl or template(), blocks, video or make_video())


def section(report, key: str):
    return next(s for s in report.sections if s.key == key)


def states(report) -> dict[str, str]:
    return {s.key: s.state for s in report.sections}


def assign(video: Video, templates, choices=None, include_shorts=False, memberships=None, titles=None):
    return assign_template(video, templates, choices or {}, include_shorts, memberships or {}, titles or {})


# --- which template a video follows ---

def test_step1_excluded_by_hand_wins_over_everything():
    tpl = template(is_default=True)
    result = assign(make_video(), [tpl], choices={"v1": None})
    assert (result.template, result.excluded, result.reason) == (None, True, "You excluded this video.")


def test_step2_chosen_by_hand_wins_over_rules_and_default():
    rule_tpl = template(1, "Raised beds", 0, rules=[{"kind": "title_word", "value": "raised"}])
    chosen = template(2, "Compost", 1)
    result = assign(make_video(), [rule_tpl, chosen], choices={"v1": 2})
    assert result.template == chosen and not result.excluded
    assert result.reason == "Chosen on the video page."


def test_step2_a_deleted_chosen_template_falls_through_to_the_rules():
    rule_tpl = template(1, "Raised beds", 0, rules=[{"kind": "title_word", "value": "raised"}])
    result = assign(make_video(), [rule_tpl], choices={"v1": 99})
    assert result.template == rule_tpl and result.reason == "The title has the word raised."


def test_step2_a_hand_choice_brings_in_a_short():
    tpl = template(1, "Shorts")
    result = assign(make_video(duration="PT45S"), [tpl], choices={"v1": 1})
    assert result.template == tpl and not result.excluded


def test_step3_shorts_are_excluded_by_default():
    result = assign(make_video(duration="PT45S"), [template(is_default=True)])
    assert (result.template, result.excluded, result.reason) == (
        None, True, "Shorts are excluded from templates.")


def test_step3_the_setting_lets_shorts_follow_templates():
    tpl = template(is_default=True)
    result = assign(make_video(duration="PT45S"), [tpl], include_shorts=True)
    assert result.template == tpl and result.reason == "Default template."


def test_step4_playlist_rule_names_the_playlist():
    tpl = template(1, "Beds", rules=[{"kind": "playlist", "value": "PLbeds"}])
    result = assign(make_video(), [tpl], memberships={"v1": frozenset({"PLbeds"})},
                    titles={"PLbeds": "Raised bed builds"})
    assert result.template == tpl and result.reason == "In the playlist Raised bed builds."


def test_step4_playlist_rule_without_a_title_names_the_id_and_needs_membership():
    tpl = template(1, "Beds", rules=[{"kind": "playlist", "value": "PLbeds"}])
    assert assign(make_video(), [tpl], memberships={"v1": frozenset({"PLbeds"})}).reason == \
        "In the playlist PLbeds."
    assert assign(make_video(), [tpl], memberships={"v2": frozenset({"PLbeds"})}).template is None


def test_step4_title_word_ignores_case():
    tpl = template(rules=[{"kind": "title_word", "value": "GARDEN"}])
    result = assign(make_video(), [tpl])
    assert result.template == tpl and result.reason == "The title has the word GARDEN."


def test_step4_title_word_matches_a_phrase_with_any_spacing():
    tpl = template(rules=[{"kind": "title_word", "value": "raised   garden"}])
    assert assign(make_video(), [tpl]).reason == "The title has the word raised garden."
    reversed_phrase = template(rules=[{"kind": "title_word", "value": "garden raised"}])
    assert assign(make_video(), [reversed_phrase]).template is None


@pytest.mark.parametrize("title, matches", [
    ("Potting mix for beginners", False),  # "pot" inside "Potting"
    ("Spud pot: grow potatoes", True),
    ("Which pot?", True),
    ("pot", True),
    ("teapot garden", False),
])
def test_step4_title_word_matches_whole_words_only(title, matches):
    tpl = template(rules=[{"kind": "title_word", "value": "pot"}])
    assert (assign(make_video(title=title), [tpl]).template is not None) is matches


def test_step4_title_word_escapes_the_value():
    tpl = template(rules=[{"kind": "title_word", "value": "c++"}])
    assert assign(make_video(title="Raised bed c++ timer"), [tpl]).template == tpl
    assert assign(make_video(title="Raised bed cxx timer"), [tpl]).template is None


def test_step4_tag_matches_ignoring_case_and_spacing():
    tpl = template(rules=[{"kind": "tag", "value": "Raised  Bed"}])
    result = assign(make_video(tags=["compost", " raised   bed "]), [tpl])
    assert result.template == tpl and result.reason == "Tagged Raised Bed."
    assert assign(make_video(tags=["raised beds"]), [tpl]).template is None


def test_step4_first_template_in_position_order_wins():
    later = template(1, "Later", 1, rules=[{"kind": "title_word", "value": "garden"}])
    first = template(2, "First", 0, rules=[{"kind": "tag", "value": "raised bed"}])
    assert assign(make_video(), [later, first]).template == first
    # by position, not by list order or id
    assert assign(make_video(), [first, replace(later, position=-1)]).template.name == "Later"


def test_step4_the_default_template_is_skipped():
    default = template(1, "Default", 0, is_default=True, rules=[{"kind": "title_word", "value": "garden"}])
    other = template(2, "Other", 1, rules=[{"kind": "title_word", "value": "bed"}])
    result = assign(make_video(), [default, other])
    assert result.template == other and result.reason == "The title has the word bed."


def test_step5_default_template_when_no_rule_matches():
    default = template(1, "Default", 1, is_default=True)
    other = template(2, "Compost", 0, rules=[{"kind": "title_word", "value": "compost"}])
    result = assign(make_video(), [other, default])
    assert result.template == default and result.reason == "Default template."


def test_step6_no_templates_at_all():
    result = assign(make_video(), [])
    assert (result.template, result.excluded, result.reason) == (None, False, "No description template yet.")


def test_no_rule_matches_and_no_default():
    result = assign(make_video(), [template(rules=[{"kind": "tag", "value": "compost"}])])
    assert result.template is None and not result.excluded
    assert result.reason == "No template matches this video, and there is no default template."


# --- the parser: blocks and old versions ---

def test_a_full_description_follows_the_template():
    report = parse(FULL)
    assert set(states(report).values()) == {OK}
    assert report.in_order and report.tray == ()
    assert section(report, "opening").lines == (1,)
    assert section(report, "about").lines == (3,)
    assert section(report, "chapters").lines == (5, 6, 7)
    assert section(report, "watch_next").lines == (9,)
    assert section(report, "block:1").lines == (11, 12, 13)
    assert section(report, "block:2").lines == (15, 16)
    assert section(report, "hashtags").lines == (18,)
    assert [s.key for s in report.sections] == [
        "opening", "about", "chapters", "watch_next", "block:1", "block:2", "hashtags"]


def test_line_numbers_point_at_the_unchanged_text():
    report = parse(FULL)
    assert FULL.split("\n")[section(report, "hashtags").lines[0] - 1] == HASHTAGS
    assert report.block_lines == frozenset({11, 12, 13, 15, 16})


def test_block_lines_match_with_collapsed_spacing_and_blank_lines_between_but_not_another_case():
    spaced = "  Support   the garden channel:  \n\nhttps://shop.test/support   "
    report = parse(doc(OPENING, spaced))
    assert section(report, "block:2").state == OK and section(report, "block:2").lines == (3, 5)
    shouted = parse(doc(OPENING, SUPPORT.text.upper()))
    assert section(shouted, "block:2").state == MISSING


def test_a_block_split_by_other_text_is_not_found():
    broken = "Support the garden channel:\nBuy seeds too.\nhttps://shop.test/support"
    assert section(parse(doc(OPENING, broken)), "block:2").state == MISSING


def test_an_old_version_is_out_of_date():
    report = parse(doc(OPENING, OLD_SUPPORT))
    assert section(report, "block:2").state == OUT_OF_DATE
    assert section(report, "block:2").lines == (3, 4)


def test_any_old_version_counts():
    block = replace(SUPPORT, old_versions=(OLD_SUPPORT, "Tip jar: https://shop.test/tip"))
    report = parse(doc(OPENING, "Tip jar: https://shop.test/tip"), blocks=[PAID, block])
    assert section(report, "block:2").state == OUT_OF_DATE


def test_a_current_and_an_old_copy_of_one_block_are_both_seen():
    report = parse(doc(OPENING, SUPPORT.text, OLD_SUPPORT))
    assert section(report, "block:2").state == OK and section(report, "block:2").lines == (3, 4)
    assert [(t.lines, t.reason) for t in report.tray] == [((6, 7), "A second copy of Support and follow")]
    assert {6, 7} <= report.block_lines
    assert section(report, "about").state == MISSING  # the old copy is not counted as the video's own text


def test_an_old_copy_before_the_current_one_is_the_hit():
    report = parse(doc(OPENING, OLD_SUPPORT, SUPPORT.text))
    assert section(report, "block:2").state == OUT_OF_DATE and section(report, "block:2").lines == (3, 4)
    assert [(t.lines, t.reason) for t in report.tray] == [((6, 7), "A second copy of Support and follow")]


def test_current_text_wins_lines_it_shares_with_an_old_version():
    block = replace(SUPPORT, old_versions=("Support the garden channel:",))
    report = parse(doc(OPENING, block.text), blocks=[block])
    assert section(report, "block:2").state == OK and section(report, "block:2").lines == (3, 4)
    assert report.tray == ()


def test_a_section_with_a_block_missing_from_the_library_is_left_out():
    tpl = template(sections=[{"kind": "opening"}, {"kind": "block", "block_id": 2}])
    assert [s.key for s in parse(OPENING, tpl, blocks=[PAID]).sections] == ["opening"]


# --- the link slot ---

def test_link_slot_takes_the_url_lines_up_to_a_blank_line():
    report = parse(doc(OPENING, PAID_TEXT, "https://links.invalid/not-mine"))
    paid = section(report, "block:1")
    assert paid.lines == (3, 4, 5) and paid.link_lines == (4, 5)
    assert [t.lines for t in report.tray] == [(7,)]


def test_link_slot_stops_at_hashtags():
    report = parse(doc(OPENING, PAID_TEXT + "\n" + HASHTAGS))
    assert section(report, "block:1").link_lines == (4, 5)
    assert section(report, "hashtags").lines == (6,)


def test_link_slot_stops_at_a_watch_next_link():
    report = parse(doc(OPENING, PAID_TEXT + "\n" + WATCH))
    assert section(report, "block:1").link_lines == (4, 5)
    assert section(report, "watch_next").lines == (6,)


def test_link_slot_stops_at_another_block():
    text = PAID.text + "\nhttps://shop.test/trowel\n" + NEWSLETTER.text
    tpl = template(sections=[{"kind": "opening"}, {"kind": "block", "block_id": 1},
                             {"kind": "block", "block_id": 3}])
    report = parse(doc(OPENING, text), tpl)
    assert section(report, "block:1").link_lines == (4,)
    assert section(report, "block:3").state == OK and section(report, "block:3").lines == (5,)


def test_link_slot_stops_at_a_line_without_a_url():
    report = parse(doc(OPENING, PAID.text + "\nhttps://shop.test/trowel\nThanks for watching."))
    assert section(report, "block:1").link_lines == (4,)


def test_a_block_without_link_slot_takes_no_links():
    report = parse(doc(OPENING, SUPPORT.text + "\nhttps://links.invalid/extra"))
    assert section(report, "block:2").link_lines == ()
    assert [t.lines for t in report.tray] == [(5,)]


# --- the parser: per-video sections and headings ---

def test_chapters_watch_next_and_hashtags_lines():
    text = doc(OPENING, "(0:00) Intro\n- 1:02:03 Long part", "https://www.youtube.com/playlist?list=PLbeds",
               "#garden #compost #diy")
    report = parse(text)
    assert section(report, "chapters").lines == (3, 4)
    assert section(report, "watch_next").lines == (6,)
    assert section(report, "hashtags").lines == (8,)


def test_a_line_with_text_and_hashtags_is_not_the_hashtags():
    report = parse(doc(OPENING, "Tag your photos #garden"))
    assert section(report, "hashtags").state == MISSING


@pytest.mark.parametrize("heading, kind, body, lines", [
    ("Chapters:", "chapters", "0:00 Intro\n1:30 Soil", (3, 4, 5)),
    ("Watch next:", "watch_next", "https://youtu.be/abc123XYZ00", (3, 4)),
])
def test_a_short_heading_belongs_to_the_run_below_it(heading, kind, body, lines):
    report = parse(doc(OPENING, heading + "\n" + body))
    assert section(report, kind).lines == lines
    assert section(report, "about").state == MISSING


def test_a_long_line_ending_in_a_colon_is_not_a_heading():
    sentence = "Here is everything we cover in this video, from the soil to the first harvest:"
    report = parse(doc(OPENING, sentence + "\n0:00 Intro"))
    assert section(report, "chapters").lines == (4,)
    assert section(report, "about").lines == (3,)


def test_a_heading_must_be_directly_above():
    report = parse(doc(OPENING, "Chapters:", "0:00 Intro"))
    assert section(report, "chapters").lines == (5,)


def test_the_opening_must_be_the_first_thing():
    report = parse(doc(CHAPTER_LINES, OPENING))
    assert section(report, "opening").state == MISSING
    assert section(report, "about").lines == (5,)


def test_leading_blank_lines_do_not_stop_the_opening():
    assert section(parse("\n\n" + OPENING), "opening").lines == (3,)


def test_about_is_plain_text_before_the_first_block_or_hashtags():
    text = doc(OPENING, ABOUT, "Another note about soil.", SUPPORT.text, "After the block.")
    report = parse(text)
    assert section(report, "about").lines == (3, 5)
    assert [(t.lines, t.reason) for t in report.tray] == [((10,), TRAY_REASON)]


# --- the tray ---

def test_plain_text_after_hashtags_goes_to_the_tray():
    report = parse(doc(OPENING, HASHTAGS, "Thanks for watching."))
    assert section(report, "about").state == MISSING
    assert [(t.text, t.reason) for t in report.tray] == [("Thanks for watching.", TRAY_REASON)]


def test_stray_links_go_to_the_tray():
    report = parse(doc(OPENING, ABOUT, "https://links.invalid/a\nhttps://links.invalid/b"))
    assert [(t.lines, t.text, t.reason) for t in report.tray] == [
        ((5, 6), "https://links.invalid/a\nhttps://links.invalid/b", TRAY_REASON)]


def test_a_second_copy_goes_to_the_tray():
    report = parse(doc(OPENING, SUPPORT.text, SUPPORT.text))
    assert section(report, "block:2").lines == (3, 4)
    assert [(t.lines, t.text, t.reason) for t in report.tray] == [
        ((6, 7), SUPPORT.text, "A second copy of Support and follow")]


def test_a_block_not_in_this_template_goes_to_the_tray():
    report = parse(doc(OPENING, NEWSLETTER.text))
    assert [(t.lines, t.reason) for t in report.tray] == [((3,), "Newsletter is not in this template")]
    assert 3 in report.block_lines


def test_sections_the_template_does_not_use_go_to_the_tray():
    tpl = template(sections=[{"kind": "opening"}, {"kind": "block", "block_id": 2}])
    report = parse(doc(OPENING, CHAPTER_LINES, SUPPORT.text, HASHTAGS), tpl)
    assert [t.lines for t in report.tray] == [(3, 4, 5), (10,)]
    assert states(report) == {"opening": OK, "block:2": OK}


def test_the_tray_costs_nothing():
    messy_text = FULL + "\n\nThanks for watching.\nhttps://links.invalid/x\n\n" + NEWSLETTER.text
    messy = parse(messy_text)
    assert len(messy.tray) == 2
    assert judge_report(template(), messy).points == judge_report(template(), parse(FULL)).points == 10


# --- states and order ---

def test_missing_sections_are_not_out_of_order():
    report = parse(OPENING)
    assert states(report) == {"opening": OK, "about": MISSING, "chapters": MISSING, "watch_next": MISSING,
                              "block:1": MISSING, "block:2": MISSING, "hashtags": MISSING}
    assert all(s.in_order and s.lines == () for s in report.sections[1:])


@pytest.mark.parametrize("duration, state", [("PT60S", NOT_APPLICABLE), ("PT45S", NOT_APPLICABLE),
                                            ("PT1M1S", MISSING), ("", MISSING)])
def test_chapters_are_not_applicable_on_a_video_of_a_minute_or_less(duration, state):
    assert section(parse(OPENING, video=make_video(duration=duration)), "chapters").state == state


def test_chapters_found_on_a_short_video_are_ok():
    report = parse(doc(OPENING, "0:00 Intro"), video=make_video(duration="PT50S"))
    assert section(report, "chapters").state == OK


def two_blocks() -> DescriptionTemplate:
    return template(sections=[{"kind": "opening"}, {"kind": "block", "block_id": 1},
                              {"kind": "block", "block_id": 2}, {"kind": "hashtags"}])


def test_a_block_out_of_place_is_wrong_place():
    report = parse(doc(OPENING, PAID_TEXT, HASHTAGS, SUPPORT.text), two_blocks())
    assert states(report) == {"opening": OK, "block:1": OK, "block:2": WRONG_PLACE, "hashtags": OK}
    assert not section(report, "block:2").in_order and not report.in_order


def test_an_out_of_date_block_out_of_place_stays_out_of_date():
    tpl = two_blocks()
    report = parse(doc(OPENING, PAID_TEXT, HASHTAGS, OLD_SUPPORT), tpl)
    support = section(report, "block:2")
    assert support.state == OUT_OF_DATE and not support.in_order
    judgment = judge_report(tpl, report)
    assert judgment.wrong_place == ("Support and follow",) and judgment.out_of_date == ("Support and follow",)
    assert not judgment.order_ok


def test_hashtags_first_are_out_of_place():
    report = parse(doc(HASHTAGS, OPENING, PAID_TEXT, SUPPORT.text), two_blocks())
    assert section(report, "hashtags").state == WRONG_PLACE
    assert section(report, "block:1").state == OK and section(report, "block:2").state == OK


def test_order_keeps_the_longest_run_in_template_order():
    # Support before Paid links: only one of the two is out of place; of equal runs the one that keeps
    # the section found earlier wins, so Paid links (found later) is the one out of place
    report = parse(doc(OPENING, ABOUT, CHAPTER_LINES, SUPPORT.text, PAID_TEXT, HASHTAGS))
    assert [s.key for s in report.sections if s.state == WRONG_PLACE] == ["block:1"]


# --- scoring: 7 for the blocks, 3 for the order ---

def test_all_blocks_current_and_in_order_is_10():
    tpl = two_blocks()
    j = judge_report(tpl, parse(doc(OPENING, PAID_TEXT, SUPPORT.text, HASHTAGS), tpl), "Default template.")
    assert (j.fixed_points, j.order_ok, j.points) == (7, True, 10)
    assert (j.template_name, j.template_id, j.reason, j.applicable) == ("Main", 1, "Default template.", True)
    assert (j.blocks_total, j.blocks_current, j.missing, j.out_of_date, j.wrong_place) == (2, 2, (), (), ())


def test_half_credit_for_an_old_version_rounds_down():
    tpl = two_blocks()
    j = judge_report(tpl, parse(doc(OPENING, PAID_TEXT, OLD_SUPPORT), tpl))
    assert j.fixed_points == 5  # floor(7 x 1.5 / 2) = floor(5.25)
    assert j.out_of_date == ("Support and follow",) and j.blocks_current == 1 and j.points == 8


def test_one_of_two_blocks_missing_rounds_down():
    tpl = two_blocks()
    j = judge_report(tpl, parse(doc(OPENING, SUPPORT.text), tpl))
    assert j.fixed_points == 3  # floor(3.5)
    assert j.missing == ("Paid links",) and j.order_ok and j.points == 6


def test_one_of_three_blocks_is_floor_of_seven_thirds():
    tpl = template(sections=[{"kind": "block", "block_id": 1}, {"kind": "block", "block_id": 2},
                             {"kind": "block", "block_id": 3}])
    j = judge_report(tpl, parse(doc(OPENING, NEWSLETTER.text), tpl))
    assert j.fixed_points == 2 and j.order_ok


def test_a_wrong_place_block_earns_its_fixed_point_but_not_the_order():
    tpl = two_blocks()
    j = judge_report(tpl, parse(doc(OPENING, PAID_TEXT, HASHTAGS, SUPPORT.text), tpl))
    assert (j.fixed_points, j.order_ok, j.points) == (7, False, 7)
    assert j.blocks_current == 2 and j.wrong_place == ("Support and follow",)


def test_per_video_sections_missing_cost_nothing():
    tpl = two_blocks()
    assert judge_report(tpl, parse(doc(PAID_TEXT, SUPPORT.text), tpl)).points == 10


def test_order_needs_at_least_one_fixed_block_found():
    tpl = two_blocks()
    j = judge_report(tpl, parse(doc(OPENING, HASHTAGS), tpl))
    assert (j.fixed_points, j.order_ok, j.points) == (0, False, 0)
    assert j.missing == ("Paid links", "Support and follow") and j.wrong_place == ()


def test_without_fixed_blocks_the_7_are_given_and_order_needs_two_sections():
    tpl = template(sections=[{"kind": "opening"}, {"kind": "chapters"}, {"kind": "hashtags"}])
    one = judge_report(tpl, parse(OPENING, tpl))
    assert (one.fixed_points, one.order_ok, one.points, one.blocks_total) == (7, False, 7, 0)
    two = judge_report(tpl, parse(doc(OPENING, HASHTAGS), tpl))
    assert (two.order_ok, two.points) == (True, 10)
    swapped = judge_report(tpl, parse(doc(HASHTAGS, ABOUT, CHAPTER_LINES), tpl))
    assert not swapped.order_ok and swapped.points == 7


def test_wrong_place_names_sections_too():
    tpl = two_blocks()
    j = judge_report(tpl, parse(doc(HASHTAGS, OPENING, PAID_TEXT, SUPPORT.text), tpl))
    assert j.wrong_place == ("Hashtags",)


# --- own text ---

def test_own_text_chars_leaves_out_block_lines():
    text = doc(OPENING, SUPPORT.text)
    assert find_block_lines(text, LIBRARY) == frozenset({3, 4})
    assert own_text_chars(text, find_block_lines(text, LIBRARY)) == len(OPENING)
    assert find_block_lines(text, []) == frozenset()
    assert own_text_chars(text, ()) == len(text)


def test_own_text_leaves_out_link_slot_links_old_versions_and_extra_copies():
    text = doc(OPENING, PAID_TEXT, OLD_SUPPORT, SUPPORT.text)
    assert own_text_chars(text, find_block_lines(text, LIBRARY)) == len(OPENING)


# --- judge_catalog through a real Store ---

@pytest.fixture
def store(tmp_path: Path):
    with Store(tmp_path / "t.sqlite") as s:
        yield s


def setup_library(store: Store) -> tuple[int, int, int]:
    """Paid links (link slot) and Support and follow (with one old version) in a default template "Main"."""
    paid = store.add_template_block("Paid links", PAID.text, link_slot=True)
    support = store.add_template_block("Support and follow", OLD_SUPPORT)
    store.update_template_block(support, text=SUPPORT.text)
    main = store.add_description_template("Main", [{"kind": "opening"}, {"kind": "block", "block_id": paid},
                                                   {"kind": "block", "block_id": support}], is_default=True)
    return paid, support, main


def test_judge_catalog_with_no_templates_says_so_and_leaves_lengths_alone(store: Store):
    judgments, own = judge_catalog(store, [make_video("v1", description=FULL)])
    assert judgments["v1"] == TemplateJudgment(applicable=False, reason="No description template yet.")
    assert own == {}


def test_judge_catalog_judges_each_video(store: Store):
    _, support, main = setup_library(store)
    beds = store.add_description_template("Beds", [{"kind": "block", "block_id": support}],
                                          rules=[{"kind": "playlist", "value": "PLbeds"}])
    store.replace_playlists({"PLbeds": ("Raised bed builds", ["v2"])})
    v1 = make_video("v1", description=doc(OPENING, PAID_TEXT, SUPPORT.text))
    v2 = make_video("v2", description=doc(OPENING, OLD_SUPPORT))
    v3 = make_video("v3", description=doc(OPENING, SUPPORT.text), duration="PT30S")
    v4 = make_video("v4", description=OPENING)
    store.exclude_from_templates("v4")
    judgments, own = judge_catalog(store, [v1, v2, v3, v4])
    assert judgments["v1"].points == 10 and judgments["v1"].template_id == main
    assert judgments["v1"].reason == "Default template."
    assert judgments["v2"].template_id == beds and judgments["v2"].reason == "In the playlist Raised bed builds."
    assert judgments["v2"].out_of_date == ("Support and follow",) and judgments["v2"].fixed_points == 3
    assert judgments["v3"] == TemplateJudgment(applicable=False, reason="Shorts are excluded from templates.",
                                               excluded=True)
    assert judgments["v4"].excluded and judgments["v4"].reason == "You excluded this video."
    # blocks are taken out of the length even on a video left out; no entry where no block was found
    assert own == {"v1": len(OPENING), "v2": len(OPENING), "v3": len(OPENING)}


def test_judge_catalog_follows_the_shorts_setting_and_hand_choices(store: Store):
    _, _, main = setup_library(store)
    short = make_video("v1", description=OPENING, duration="PT30S")
    assert not judge_catalog(store, [short])[0]["v1"].applicable
    store.set_template_choice("v1", main)
    assert judge_catalog(store, [short])[0]["v1"].reason == "Chosen on the video page."
    store.clear_template_choice("v1")
    store.set_templates_include_shorts(True)
    assert judge_catalog(store, [short])[0]["v1"].reason == "Default template."


def test_judge_catalog_feeds_score_catalog(store: Store):
    setup_library(store)
    video = make_video("v1", description=doc(OPENING, PAID_TEXT))
    judgments, own = judge_catalog(store, [video])
    card = seo.score_catalog([video], templates=judgments, own_chars=own)["v1"]
    result = next(c for c in card.checks if c.check_id == "description_template")
    assert result.applicable and result.points == judgments["v1"].points == 3 + 3
    length = next(c for c in card.checks if c.check_id == "description_length")
    assert length.detail == f"Your own text is {len(OPENING)} characters (shared blocks not counted)."


# --- coverage ---

def judged(template_id: int, name: str, points: int) -> TemplateJudgment:
    """A judgment worth `points` (10, 7 or less)."""
    return TemplateJudgment(template_name=name, template_id=template_id,
                            fixed_points=min(points, 7), order_ok=points == 10)


def test_coverage_counts_following_needs_changes_excluded_and_none():
    videos = [make_video(f"v{i}") for i in range(1, 7)]
    judgments = {
        "v1": judged(1, "Main", 10),
        "v2": judged(1, "Main", 7),
        "v3": judged(2, "Beds", 10),
        "v4": TemplateJudgment(applicable=False, reason="You excluded this video.", excluded=True),
        "v5": TemplateJudgment(applicable=False, reason="No description template yet."),
    }  # v6 has no judgment at all
    result = coverage(videos, judgments)
    assert [(t.template_id, t.name, t.following, t.needs_changes) for t in result.templates] == [
        (1, "Main", 1, 1), (2, "Beds", 1, 0)]
    assert (result.excluded, result.no_template) == (1, 2)


def test_coverage_lists_given_templates_in_order_even_without_videos():
    templates = [template(2, "Beds", 0), template(1, "Main", 1), template(3, "Empty", 2)]
    result = coverage([make_video("v1")], {"v1": judged(1, "Main", 10)}, templates)
    assert [(t.template_id, t.following, t.needs_changes) for t in result.templates] == [
        (2, 0, 0), (1, 1, 0), (3, 0, 0)]


def test_coverage_counts_a_template_not_in_the_list_once():
    templates = [template(2, "Beds", 0)]
    videos = [make_video("v1"), make_video("v2"), make_video("v3")]
    judgments = {"v1": judged(5, "Gone", 10), "v2": judged(2, "Beds", 3), "v3": judged(4, "Old", 0)}
    result = coverage(videos, judgments, templates)
    assert [(t.template_id, t.name, t.following, t.needs_changes) for t in result.templates] == [
        (2, "Beds", 0, 1), (4, "Old", 0, 1), (5, "Gone", 1, 0)]
    counted = sum(t.following + t.needs_changes for t in result.templates) + result.excluded + result.no_template
    assert counted == len(videos)


# --- the starter template ---

def test_starter_sections_order():
    assert [s["kind"] if s["kind"] != "block" else s["name"] for s in STARTER_SECTIONS] == [
        "opening", "about", "chapters", "watch_next", "Paid links", "Support and follow", "hashtags"]
    assert next(s for s in STARTER_SECTIONS if s.get("name") == "Paid links")["link_slot"] is True


def test_starter_sections_create_nothing(store: Store):
    judge_catalog(store, [make_video()])
    assert store.list_template_blocks() == [] and store.list_description_templates() == []


# --- a shared block that still holds its starter placeholder is not set up, and not scored ---

PAID_STARTER = replace(PAID, text=STARTER_PLACEHOLDERS["Paid links"])
SUPPORT_STARTER = replace(SUPPORT, text=STARTER_PLACEHOLDERS["Support and follow"], old_versions=())


def test_is_placeholder_ignores_spacing_but_not_other_text():
    assert is_placeholder(PAID_STARTER) and is_placeholder(SUPPORT_STARTER)
    spaced = replace(PAID, text="  Replace this with   your\npaid-links disclosure.  ")
    assert is_placeholder(spaced)
    assert not is_placeholder(PAID)
    assert not is_placeholder(replace(PAID, text=STARTER_PLACEHOLDERS["Paid links"] + " Thanks!"))
    assert not is_placeholder(replace(PAID, text=STARTER_PLACEHOLDERS["Paid links"].lower()))


def test_a_placeholder_block_is_not_set_up_not_missing_and_not_in_the_order():
    tpl = two_blocks()
    blocks = [PAID_STARTER, SUPPORT]
    # the placeholder text sits in the description, even out of place: it is still not looked for
    text = doc(OPENING, SUPPORT.text, PAID_STARTER.text, HASHTAGS)
    report = parse(text, tpl, blocks)
    paid = section(report, "block:1")
    assert (paid.state, paid.lines, paid.in_order) == (NOT_SET_UP, (), True)
    assert states(report) == {"opening": OK, "block:1": NOT_SET_UP, "block:2": OK, "hashtags": OK}
    j = judge_report(tpl, report)
    assert j.not_set_up == ("Paid links",)
    assert (j.blocks_total, j.blocks_current, j.missing, j.wrong_place) == (1, 1, (), ())
    assert (j.fixed_points, j.order_ok, j.points) == (7, True, 10)


def test_a_placeholder_mixed_with_a_missing_real_block():
    tpl = two_blocks()
    j = judge_report(tpl, parse(doc(OPENING, HASHTAGS), tpl, [PAID_STARTER, SUPPORT]))
    assert (j.blocks_total, j.missing, j.not_set_up) == (1, ("Support and follow",), ("Paid links",))
    assert j.fixed_points == 0
    assert not j.order_ok  # no real block found


def test_a_placeholder_mixed_with_an_old_version():
    tpl = two_blocks()
    j = judge_report(tpl, parse(doc(OPENING, OLD_SUPPORT, HASHTAGS), tpl, [PAID_STARTER, SUPPORT]))
    assert (j.blocks_total, j.out_of_date, j.fixed_points) == (1, ("Support and follow",), 3)  # floor(3.5)


def test_all_blocks_placeholders_use_the_no_fixed_blocks_rules():
    tpl = two_blocks()
    blocks = [PAID_STARTER, SUPPORT_STARTER]
    j = judge_report(tpl, parse(doc(OPENING, HASHTAGS), tpl, blocks))
    assert (j.blocks_total, j.fixed_points, j.order_ok, j.points) == (0, 7, True, 10)
    assert j.not_set_up == ("Paid links", "Support and follow") and j.missing == ()
    j = judge_report(tpl, parse(OPENING, tpl, blocks))  # one section found: too little for the order
    assert (j.fixed_points, j.order_ok, j.points) == (7, False, 7)


def test_text_edited_away_from_the_placeholder_is_scored_again():
    tpl = two_blocks()
    edited = replace(PAID_STARTER, text=PAID.text, old_versions=(PAID_STARTER.text,))
    j = judge_report(tpl, parse(doc(OPENING, SUPPORT.text, HASHTAGS), tpl, [edited, SUPPORT]))
    assert j.not_set_up == () and j.missing == ("Paid links",) and (j.blocks_total, j.fixed_points) == (2, 3)
    j = judge_report(tpl, parse(doc(OPENING, PAID.text, SUPPORT.text, HASHTAGS), tpl, [edited, SUPPORT]))
    assert (j.missing, j.fixed_points, j.points) == ((), 7, 10)


def test_find_block_lines_skips_placeholder_blocks():
    text = doc(OPENING, PAID_STARTER.text, SUPPORT.text)
    assert find_block_lines(text, [PAID_STARTER, SUPPORT]) == frozenset({5, 6})
    assert find_block_lines(text, [PAID_STARTER]) == frozenset()


def test_judge_catalog_after_the_starter_does_not_drop_videos_to_zero(store: Store):
    paid = store.add_template_block("Paid links", STARTER_PLACEHOLDERS["Paid links"], link_slot=True)
    support = store.add_template_block("Support and follow", STARTER_PLACEHOLDERS["Support and follow"])
    sections = [{"kind": k} for k in ("opening", "about", "chapters", "watch_next")]
    sections += [{"kind": "block", "block_id": paid}, {"kind": "block", "block_id": support}, {"kind": "hashtags"}]
    store.add_description_template("Main", sections, is_default=True)
    video = make_video("v1", description=doc(OPENING, CHAPTER_LINES, HASHTAGS))
    judgments, own = judge_catalog(store, [video])
    assert judgments["v1"].points == 10 and judgments["v1"].not_set_up == ("Paid links", "Support and follow")
    assert own == {}
