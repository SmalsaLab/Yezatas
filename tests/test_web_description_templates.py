"""The Description templates page: the empty state and the starter button, the coverage strip, the template
chips, every action (rename, default, delete, priority, rules, sections, shared blocks, the Shorts setting) with
its happy and error paths, CSRF, the read-only preview and the nav item. Made-up gardening videos only; nothing
here calls YouTube or a model."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from webhelpers import Ui, make_ui, make_video, seed_catalog
from yezatas import descriptiontemplate
from yezatas.store import Store

URL = "/description-templates"
SUPPORT = "Support the channel: https://support.invalid/garden\nFollow: https://links.invalid/follow"
OLD_SUPPORT = "Support the channel: https://support.invalid/old"
PAID = "Paid links. I earn a commission if you buy through them."
CHAPTERS = "0:00 Why repot\n1:10 The soil\n2:30 The pot"


def desc(*parts: str) -> str:
    return "\n\n".join(parts)


OPENING = "How to repot a spiky cactus without gloves, step by step, with the soil mix I use for every plant."

# V1 follows "Main" fully; V2 has the old support text and no paid block; V3 is excluded by hand;
# V4 is a Short (left out by default); V5 has a line that fits no section.
FULL = desc(OPENING, CHAPTERS, PAID + "\nPot: https://shop.invalid/pot", SUPPORT, "#cactus #repotting")
STALE = desc("Watering a cactus in winter is mostly about waiting.", OLD_SUPPORT, "#cactus")
TRAY = desc("Soil mixes for succulents, tested side by side over a whole summer season.", SUPPORT,
            "Thanks for watching!", "#soil")


def make_dt_ui(tmp_path: Path, videos: list[Any] | None = None) -> Ui:
    ui = make_ui(tmp_path)
    seed_catalog(ui.config, videos if videos is not None else [
        make_video(1, video_id="vid000001", title="Repotting a spiky cactus", description=FULL, view_count=900,
                   privacy_status="public"),
        make_video(2, video_id="vid000002", title="Watering in winter", description=STALE, view_count=500,
                   privacy_status="public"),
        make_video(3, video_id="vid000003", title="Cactus soil review", description="A review.", view_count=50,
                   privacy_status="public"),
        make_video(4, video_id="vid000004", title="Quick tip", description="A Short.", duration="PT40S",
                   view_count=5000, privacy_status="public"),
        make_video(5, video_id="vid000005", title="Soil mixes", description=TRAY, view_count=200,
                   privacy_status="public"),
    ])
    return ui


def store_of(ui: Ui) -> Store:
    return Store(ui.config.catalog_db)


def seed_main(ui: Ui) -> tuple[int, int, int]:
    """A default template "Main" with two shared blocks; the support block has one old version."""
    with store_of(ui) as store:
        paid = store.add_template_block("Paid links", PAID, link_slot=True)
        support = store.add_template_block("Support and follow", OLD_SUPPORT)
        store.update_template_block(support, text=SUPPORT)
        main = store.add_description_template("Main", [
            {"kind": "opening"}, {"kind": "about"}, {"kind": "chapters"}, {"kind": "watch_next"},
            {"kind": "block", "block_id": paid}, {"kind": "block", "block_id": support}, {"kind": "hashtags"}],
            is_default=True)
        store.exclude_from_templates("vid000003")
    return main, paid, support


def page(ui: Ui, query: str = "") -> str:
    resp = ui.client.get(URL + query)
    assert resp.status_code == 200
    return resp.text.replace("&#34;", '"')  # Jinja escapes quotes; the sentences are easier to read without


def nf(ui: Ui, path: str, **kwargs: Any) -> Any:
    kwargs.setdefault("follow_redirects", False)
    return ui.post(path, **kwargs)


def section(html: str, element_id: str) -> str:
    start = html.index(f'id="{element_id}"')
    return html[start:html.index("</section>", start)]


def templates_of(ui: Ui) -> list[Any]:
    with store_of(ui) as store:
        return store.list_description_templates()


def blocks_of(ui: Ui) -> list[Any]:
    with store_of(ui) as store:
        return store.list_template_blocks()


def no_inline_script_or_style(html: str) -> bool:
    return not re.search(r"<script(?![^>]*\bsrc=)", html) and "style=" not in html


# --- the empty state and the starter ---------------------------------------------------------------------

def test_the_empty_page_explains_templates_and_offers_the_starter(tmp_path: Path) -> None:
    ui = make_dt_ui(tmp_path)
    html = page(ui)
    assert '<h1 class="page-title">Description templates</h1>' in html
    empty = section(html, "tp-empty")
    assert "A template is the order of the parts of a description" in empty
    assert 'action="/description-templates/start"' in empty and 'name="csrf"' in empty
    assert ">Start with the suggested template</button>" in empty
    assert "Paid links and Support and follow" in empty
    assert 'id="tp-coverage"' not in html and 'id="template"' not in html
    assert templates_of(ui) == [] and blocks_of(ui) == []  # nothing is created by looking
    assert no_inline_script_or_style(html)


def test_the_starter_makes_one_default_template_and_two_placeholder_blocks_once(tmp_path: Path) -> None:
    ui = make_dt_ui(tmp_path)
    resp = nf(ui, URL + "/start")
    assert resp.status_code == 303
    made = templates_of(ui)
    assert len(made) == 1 and made[0].name == "Main" and made[0].is_default
    assert resp.headers["location"] == f"{URL}?template={made[0].id}#template"
    blocks = blocks_of(ui)
    assert [(b.name, b.link_slot) for b in blocks] == [("Paid links", True), ("Support and follow", False)]
    assert all(b.text.startswith("Replace this with") for b in blocks)
    kinds = [s.get("kind") if s["kind"] != "block" else s["block_id"] for s in made[0].sections]
    assert kinds == ["opening", "about", "chapters", "watch_next", blocks[0].id, blocks[1].id, "hashtags"]
    html = page(ui)
    assert "Added the suggested template." in html
    assert "Placeholder text: replace it with your own under Shared blocks." in html
    assert "Takes every video no other template claims." in html

    again = nf(ui, URL + "/start")
    assert again.status_code == 303
    assert len(templates_of(ui)) == 1 and len(blocks_of(ui)) == 2
    assert "You already have a template, so nothing was added." in page(ui)


def test_after_the_starter_placeholder_blocks_are_not_set_up_and_cost_no_points(tmp_path: Path) -> None:
    ui = make_dt_ui(tmp_path)
    nf(ui, URL + "/start")
    with store_of(ui) as store:
        judgments, _ = descriptiontemplate.judge_catalog(store, store.list_videos())
    followed = [j for j in judgments.values() if j.applicable]
    assert len(followed) == 4  # every video but the Short
    assert all(j.points >= 7 and j.missing == () for j in followed)
    assert all(j.not_set_up == ("Paid links", "Support and follow") for j in followed)
    html = page(ui)
    # the preview: a neutral "Not set up" pill, never a warning
    assert states_of(html)["Paid links"] == "Not set up" and states_of(html)["Support and follow"] == "Not set up"
    pills = re.findall(r'<span class="pill ([^"]*)" data-state="not_set_up">Not set up</span>',
                       section(html, "preview"))
    assert pills == ["", ""]
    assert 'Follows "Main": Paid links and Support and follow are not set up yet' in section(html, "preview")
    # the section list keeps the placeholder note but counts nothing for those blocks
    blocks = [b.id for b in blocks_of(ui)]
    rows = section(html, "template")
    for block_id in blocks:
        row = rows[rows.index(f'id="section-block-{block_id}"'):]
        row = row[:row.index("</li>")]
        assert "Placeholder text: replace it with your own under Shared blocks." in row
        assert '<span class="tp-sec-count"></span>' in row and "missing" not in row
    # coverage follows the new scoring
    full = sum(1 for j in followed if j.points == 10)
    cover = html[html.index('id="tp-coverage"'):html.index('id="tp-chips"')]
    def number(tile: str) -> int:
        found = re.search(rf'id="{tile}"[^>]*><span class="tp-stat-k">[^<]*</span><span class="tp-stat-v">(\d+)</span>',
                          cover)
        assert found, tile
        return int(found.group(1))
    assert (number("tp-cover-following"), number("tp-cover-needs")) == (full, len(followed) - full)
    assert full >= 1  # a video with only its own sections, in order, now follows fully


def test_a_block_edited_away_from_the_placeholder_is_scored_again(tmp_path: Path) -> None:
    ui = make_dt_ui(tmp_path)
    nf(ui, URL + "/start")
    support = next(b for b in blocks_of(ui) if b.name == "Support and follow")
    nf(ui, f"{URL}/blocks/{support.id}/edit", data={"name": support.name, "text": SUPPORT})
    html = page(ui, "?video=vid000001")
    assert states_of(html)["Support and follow"] == "OK" and states_of(html)["Paid links"] == "Not set up"
    html = page(ui, "?video=vid000002")
    assert states_of(html)["Support and follow"] == "Missing"
    row = section(html, "template")
    row = row[row.index(f'id="section-block-{support.id}"'):]
    assert "missing" in row[:row.index("</li>")]


def test_the_starter_reuses_a_block_of_the_same_name(tmp_path: Path) -> None:
    ui = make_dt_ui(tmp_path)
    with store_of(ui) as store:
        own = store.add_template_block("Support and follow", SUPPORT)
    nf(ui, URL + "/start")
    blocks = blocks_of(ui)
    assert len(blocks) == 2 and {b.id for b in blocks} >= {own}
    assert next(b for b in blocks if b.id == own).text == SUPPORT


# --- coverage, chips and the chosen template -----------------------------------------------------------

def test_the_coverage_strip_counts_a_small_catalog(tmp_path: Path) -> None:
    ui = make_dt_ui(tmp_path)
    seed_main(ui)
    html = page(ui)
    cover = html[html.index('id="tp-coverage"'):html.index('id="tp-chips"')]
    def number(tile: str) -> str:
        found = re.search(rf'id="{tile}"[^>]*>.*?<span class="tp-stat-v">(\d+)</span>', cover, re.S)
        assert found, tile
        return found.group(1)
    # V1 follows fully; V2 and V5 need changes (V5 lacks the paid block); V3 excluded by hand, V4 a Short
    assert number("tp-cover-following") == "1"
    assert number("tp-cover-needs") == "2"
    assert number("tp-cover-excluded") == "2"
    assert number("tp-cover-none") == "0"
    # only "Need changes" has an existing Videos filter (the failed check)
    assert 'href="/videos?check=description_template"' in cover
    assert cover.count("<a ") == 1


def test_chips_show_each_template_with_its_video_count_and_the_default(tmp_path: Path) -> None:
    ui = make_dt_ui(tmp_path)
    main, _, _ = seed_main(ui)
    with store_of(ui) as store:
        reviews = store.add_description_template("Reviews", [{"kind": "opening"}],
                                                 rules=[{"kind": "title_word", "value": "winter"}])
    html = page(ui)
    chips = html[html.index('id="tp-chips"'):html.index('class="tp-two"')]
    assert re.search(rf'id="chip-template-{main}"[^>]*>Main<span class="tp-chip-n">2</span>'
                     r'<span class="tp-chip-default">Default</span>', chips)
    assert re.search(rf'id="chip-template-{reviews}"[^>]*>Reviews<span class="tp-chip-n">1</span></a>', chips)
    assert f'class="chip active" id="chip-template-{main}"' in chips  # the default is shown first
    html = page(ui, f"?template={reviews}")
    assert f'class="chip active" id="chip-template-{reviews}"' in html
    assert '<h2 id="h-template">Reviews' in html
    assert '"winter" in the title' in html


def test_an_unknown_template_in_the_address_shows_the_first_one_with_a_notice(tmp_path: Path) -> None:
    ui = make_dt_ui(tmp_path)
    seed_main(ui)
    for query in ("?template=9999", "?template=abc"):
        html = page(ui, query)
        assert "That template does not exist. Showing the first one instead." in html
        assert '<h2 id="h-template">Main' in html


def test_the_section_list_marks_per_video_and_shared_blocks_with_counts(tmp_path: Path) -> None:
    ui = make_dt_ui(tmp_path)
    main, paid, support = seed_main(ui)
    with store_of(ui) as store:
        store.add_description_template("Reviews", [{"kind": "opening"}, {"kind": "block", "block_id": support}])
    html = section(page(ui), "template")
    rows = re.findall(r'<li class="tp-sec[^"]*" id="section-([^"]+)"', html)
    assert rows == ["opening", "about", "chapters", "watch_next", f"block-{paid}", f"block-{support}", "hashtags"]
    paid_row = html[html.index(f'id="section-block-{paid}"'):html.index(f'id="section-block-{support}"')]
    assert ">Shared block</span>" in paid_row and "Used by this template only." in paid_row
    assert "2 missing" in paid_row  # V2 and V5 lack it
    support_row = html[html.index(f'id="section-block-{support}"'):html.index('id="section-hashtags"')]
    assert "Also in Reviews." in support_row and "1 out of date" in support_row
    opening_row = html[html.index('id="section-opening"'):html.index('id="section-about"')]
    assert ">Per video</span>" in opening_row and "All 3" in opening_row
    assert 'href="#block-' in paid_row


# --- template actions -------------------------------------------------------------------------------------

def test_new_template_empty_and_as_a_copy(tmp_path: Path) -> None:
    ui = make_dt_ui(tmp_path)
    main, _, _ = seed_main(ui)
    resp = nf(ui, URL + "/new", data={"name": "Tutorials", "start": "empty", "template": str(main)})
    made = {t.name: t for t in templates_of(ui)}
    assert resp.status_code == 303 and resp.headers["location"] == f"{URL}?template={made['Tutorials'].id}#template"
    assert made["Tutorials"].sections == () and not made["Tutorials"].is_default
    nf(ui, URL + "/new", data={"name": "Reviews", "start": "copy", "template": str(main)})
    made = {t.name: t for t in templates_of(ui)}
    assert made["Reviews"].sections == made["Main"].sections and made["Reviews"].rules == ()
    assert "Template added." in page(ui)


@pytest.mark.parametrize("name", ["", "   ", "x" * 101])
def test_new_template_with_a_bad_name_flashes_and_keeps_what_was_typed(tmp_path: Path, name: str) -> None:
    ui = make_dt_ui(tmp_path)
    main, _, _ = seed_main(ui)
    resp = nf(ui, URL + "/new", data={"name": name, "start": "copy", "template": str(main)})
    assert resp.status_code == 303 and resp.headers["location"].endswith("#new-template")
    assert len(templates_of(ui)) == 1
    html = page(ui)
    assert ("Give the template a name." in html) or ("at most 100 characters" in html)
    new = section(html, "new-template")
    assert f'name="name" value="{name}"' in new
    assert 'value="copy" checked' in new
    # shown once, like a flash
    assert f'name="name" value="{name}"' not in section(page(ui), "new-template") or not name


def test_rename_and_its_error_path(tmp_path: Path) -> None:
    ui = make_dt_ui(tmp_path)
    main, _, _ = seed_main(ui)
    resp = nf(ui, f"{URL}/{main}/rename", data={"name": "Everything", "template": str(main)})
    assert resp.status_code == 303 and resp.headers["location"] == f"{URL}?template={main}#template"
    assert templates_of(ui)[0].name == "Everything"
    assert "Template renamed." in page(ui)
    nf(ui, f"{URL}/{main}/rename", data={"name": "  ", "template": str(main)})
    html = page(ui)
    assert "Give the template a name." in html and templates_of(ui)[0].name == "Everything"
    assert 'id="tp-rename" open' in html and 'id="rename-name" name="name" value="  "' in html


def test_make_default_moves_the_flag(tmp_path: Path) -> None:
    ui = make_dt_ui(tmp_path)
    main, _, _ = seed_main(ui)
    with store_of(ui) as store:
        other = store.add_description_template("Reviews", [{"kind": "opening"}])
    assert ">Make default</button>" in section(page(ui, f"?template={other}"), "template")
    assert ">Make default</button>" not in section(page(ui, f"?template={main}"), "template")
    resp = nf(ui, f"{URL}/{other}/default", data={"template": str(other)})
    assert resp.status_code == 303
    assert {t.name: t.is_default for t in templates_of(ui)} == {"Main": False, "Reviews": True}
    assert '"Reviews" is now the default template.' in page(ui)


def test_delete_says_how_many_hand_picked_choices_go_with_it(tmp_path: Path) -> None:
    ui = make_dt_ui(tmp_path)
    seed_main(ui)
    with store_of(ui) as store:
        other = store.add_description_template("Reviews", [{"kind": "opening"}])
        store.set_template_choice("vid000001", other)
        store.set_template_choice("vid000002", other)
    note = section(page(ui, f"?template={other}"), "template")
    assert "2 videos have it chosen by hand; those choices go with it." in note
    assert f'action="/description-templates/{other}/delete"' in note and "<details" in note
    resp = nf(ui, f"{URL}/{other}/delete", data={"template": str(other)})
    assert resp.status_code == 303 and resp.headers["location"] == URL + "#template"
    assert [t.name for t in templates_of(ui)] == ["Main"]
    with store_of(ui) as store:
        assert "vid000001" not in store.template_choices()
    assert 'Deleted "Reviews". 2 hand-picked choices went with it.' in page(ui)


@pytest.mark.parametrize("action", ["rename", "default", "delete", "move", "rules/add", "rules/remove",
                                    "sections/add", "sections/move", "sections/remove"])
@pytest.mark.parametrize("raw_id", ["9999", "abc"])
def test_a_missing_template_id_flashes_instead_of_failing(tmp_path: Path, action: str, raw_id: str) -> None:
    ui = make_dt_ui(tmp_path)
    seed_main(ui)
    before = templates_of(ui)
    resp = nf(ui, f"{URL}/{raw_id}/{action}",
              data={"name": "X", "kind": "tag", "value": "x", "section": "opening", "direction": "up",
                    "template": raw_id})
    assert resp.status_code == 303 and resp.headers["location"].startswith(URL)
    assert "That template does not exist." in page(ui)
    assert templates_of(ui) == before


def test_priority_moves_a_template_up_and_refuses_past_the_edge(tmp_path: Path) -> None:
    ui = make_dt_ui(tmp_path)
    main, _, _ = seed_main(ui)
    with store_of(ui) as store:
        other = store.add_description_template("Reviews", [{"kind": "opening"}])
    html = section(page(ui, f"?template={main}"), "template")
    assert re.search(r'value="up">\s*<button type="submit" class="button secondary tp-small" disabled>Move up', html)
    nf(ui, f"{URL}/{other}/move", data={"direction": "up", "template": str(other)})
    assert [t.name for t in templates_of(ui)] == ["Reviews", "Main"]
    assert "Priority changed." in page(ui)
    nf(ui, f"{URL}/{other}/move", data={"direction": "up", "template": str(other)})
    assert [t.name for t in templates_of(ui)] == ["Reviews", "Main"]
    assert "That cannot move any further." in page(ui)


# --- rules ------------------------------------------------------------------------------------------------

def test_rules_add_and_remove_for_a_word_and_a_tag(tmp_path: Path) -> None:
    ui = make_dt_ui(tmp_path)
    seed_main(ui)
    with store_of(ui) as store:
        other = store.add_description_template("Reviews", [{"kind": "opening"}])
    resp = nf(ui, f"{URL}/{other}/rules/add", data={"kind": "title_word", "value": "review", "template": str(other)})
    assert resp.status_code == 303 and resp.headers["location"] == f"{URL}?template={other}#rules"
    nf(ui, f"{URL}/{other}/rules/add", data={"kind": "tag", "value": "soil", "template": str(other)})
    rules = next(t for t in templates_of(ui) if t.id == other).rules
    assert rules == ({"kind": "title_word", "value": "review"}, {"kind": "tag", "value": "soil"})
    html = section(page(ui, f"?template={other}"), "template")
    assert '"review" in the title' in html and "Tagged soil" in html
    assert "The first template that matches wins." in html
    nf(ui, f"{URL}/{other}/rules/remove", data={"kind": "tag", "value": "soil", "template": str(other)})
    assert next(t for t in templates_of(ui) if t.id == other).rules == ({"kind": "title_word", "value": "review"},)
    nf(ui, f"{URL}/{other}/rules/remove", data={"kind": "tag", "value": "gone", "template": str(other)})
    assert "That rule is not in this template." in page(ui)


@pytest.mark.parametrize("value", ["", "   "])
def test_an_empty_rule_value_is_refused_and_kept(tmp_path: Path, value: str) -> None:
    ui = make_dt_ui(tmp_path)
    seed_main(ui)
    with store_of(ui) as store:
        other = store.add_description_template("Reviews", [{"kind": "opening"}])
    resp = nf(ui, f"{URL}/{other}/rules/add", data={"kind": "tag", "value": value, "template": str(other)})
    assert resp.status_code == 303 and resp.headers["location"] == f"{URL}?template={other}#rules"
    html = page(ui, f"?template={other}")
    assert "A rule needs a value." in html
    assert '<option value="tag" selected>A tag</option>' in html
    assert next(t for t in templates_of(ui) if t.id == other).rules == ()


def test_no_playlists_stored_disables_the_playlist_rule(tmp_path: Path) -> None:
    ui = make_dt_ui(tmp_path)
    seed_main(ui)
    with store_of(ui) as store:
        other = store.add_description_template("Reviews", [{"kind": "opening"}])
    form = section(page(ui, f"?template={other}"), "template")
    form = form[form.index('id="rule-playlist-form"'):form.index('id="rule-word-form"')]
    assert '<select id="rule-playlist" name="value" disabled><option>Playlists are not pulled yet</option></select>' in form
    assert 'class="button secondary" disabled>Add</button>' in form
    resp = nf(ui, f"{URL}/{other}/rules/add", data={"kind": "playlist", "value": "PLfake", "template": str(other)})
    assert resp.status_code == 303 and "Choose one of your playlists." in page(ui)


def test_a_stored_playlist_can_be_a_rule(tmp_path: Path) -> None:
    ui = make_dt_ui(tmp_path)
    seed_main(ui)
    with store_of(ui) as store:
        other = store.add_description_template("Reviews", [{"kind": "opening"}])
        store.replace_playlists({"PLgarden01": ("Soil reviews", ["vid000005"])})
    form = section(page(ui, f"?template={other}"), "template")
    assert '<option value="PLgarden01">Soil reviews (1 video)</option>' in form
    nf(ui, f"{URL}/{other}/rules/add", data={"kind": "playlist", "value": "PLgarden01", "template": str(other)})
    html = page(ui, f"?template={other}")
    assert "In the playlist Soil reviews" in html
    assert re.search(rf'id="chip-template-{other}"[^>]*>Reviews<span class="tp-chip-n">1</span>', html)


def test_the_default_template_says_its_rules_are_not_used(tmp_path: Path) -> None:
    ui = make_dt_ui(tmp_path)
    main, _, _ = seed_main(ui)
    with store_of(ui) as store:
        store.update_description_template(main, rules=[{"kind": "tag", "value": "cactus"}])
    html = section(page(ui), "template")
    assert "Takes every video no other template claims." in html
    assert "These rules are not used while this is the default template." in html
    assert 'id="rule-add"' not in html  # no rule form: the default's rules are not used


# --- sections ---------------------------------------------------------------------------------------------

def test_sections_move_remove_and_add(tmp_path: Path) -> None:
    ui = make_dt_ui(tmp_path)
    main, paid, support = seed_main(ui)
    resp = nf(ui, f"{URL}/{main}/sections/move", data={"section": "hashtags", "direction": "up",
                                                         "template": str(main)})
    assert resp.status_code == 303 and resp.headers["location"] == f"{URL}?template={main}#sections"
    keys = lambda: [s["kind"] if s["kind"] != "block" else f"block:{s['block_id']}" for s in templates_of(ui)[0].sections]
    assert keys()[-2:] == ["hashtags", f"block:{support}"]
    nf(ui, f"{URL}/{main}/sections/remove", data={"section": "about", "template": str(main)})
    assert "about" not in keys()
    html = section(page(ui), "template")
    assert '<option value="about">About the video</option>' in html
    nf(ui, f"{URL}/{main}/sections/add", data={"section": "about", "template": str(main)})
    assert keys()[-1] == "about"
    assert "Section added at the end." in page(ui)
    nf(ui, f"{URL}/{main}/sections/remove", data={"section": f"block:{paid}", "template": str(main)})
    assert f"block:{paid}" not in keys()
    assert f'<option value="block:{paid}">Paid links</option>' in section(page(ui), "template")


def test_section_errors_flash_the_stores_sentence(tmp_path: Path) -> None:
    ui = make_dt_ui(tmp_path)
    main, paid, _ = seed_main(ui)
    before = templates_of(ui)[0].sections
    nf(ui, f"{URL}/{main}/sections/add", data={"section": f"block:{paid}", "template": str(main)})
    assert "A block can be in a template only once." in page(ui)
    nf(ui, f"{URL}/{main}/sections/add", data={"section": "opening", "template": str(main)})
    assert 'The section "opening" can be in a template only once.' in page(ui)
    nf(ui, f"{URL}/{main}/sections/add", data={"section": "block:999", "template": str(main)})
    assert "A section uses a block that does not exist." in page(ui)
    nf(ui, f"{URL}/{main}/sections/add", data={"section": "footer", "template": str(main)})
    assert "Choose a section or a shared block to add." in page(ui)
    nf(ui, f"{URL}/{main}/sections/move", data={"section": "opening", "direction": "up", "template": str(main)})
    assert "That cannot move any further." in page(ui)
    nf(ui, f"{URL}/{main}/sections/remove", data={"section": "block:999", "template": str(main)})
    assert "That section is not in this template." in page(ui)
    assert templates_of(ui)[0].sections == before


# --- shared blocks ----------------------------------------------------------------------------------------

def test_blocks_add_edit_and_old_versions(tmp_path: Path) -> None:
    ui = make_dt_ui(tmp_path)
    main, _, support = seed_main(ui)
    resp = nf(ui, URL + "/blocks/add", data={"name": "Gear", "text": "My gear list:", "link_slot": "1",
                                             "template": str(main)})
    assert resp.status_code == 303 and resp.headers["location"] == f"{URL}?template={main}#blocks"
    gear = next(b for b in blocks_of(ui) if b.name == "Gear")
    assert gear.link_slot and gear.text == "My gear list:"
    assert "Shared block added." in page(ui)

    nf(ui, f"{URL}/blocks/{support}/edit", data={"name": "Support and follow", "text": SUPPORT + "\nShop: https://shop.invalid/",
                                                 "template": str(main)})
    block = next(b for b in blocks_of(ui) if b.id == support)
    assert block.old_versions == (SUPPORT, OLD_SUPPORT)
    html = page(ui)
    assert "The old text is remembered, so videos that still carry it show as out of date" in html
    blocks = section(html, "blocks")
    assert f'id="block-{support}-old">2 old versions</span>' in blocks
    assert "Used by Main." in blocks and "Not used by any template." in blocks

    nf(ui, f"{URL}/blocks/{gear.id}/edit", data={"name": "Gear list", "text": "My gear list:", "template": str(main)})
    gear = next(b for b in blocks_of(ui) if b.id == gear.id)
    assert gear.name == "Gear list" and not gear.link_slot and gear.old_versions == ()
    assert "The old text is remembered" not in page(ui)


@pytest.mark.parametrize("data, sentence", [
    ({"name": "", "text": "Some text"}, "Give the block a name."),
    ({"name": "Gear", "text": "  "}, "The block needs some text."),
    ({"name": "Gear", "text": "Use <b> here"}, "The block text cannot contain &lt; or &gt;"),
    ({"name": "Gear", "text": "x" * 5001}, "A block can be at most 5,000 characters."),
])
def test_block_add_errors_flash_and_keep_what_was_typed(tmp_path: Path, data: dict[str, str], sentence: str) -> None:
    ui = make_dt_ui(tmp_path)
    seed_main(ui)
    resp = nf(ui, URL + "/blocks/add", data={**data, "link_slot": "1"})
    assert resp.status_code == 303 and resp.headers["location"].endswith("#block-add")
    html = page(ui)
    assert sentence in html
    add = html[html.index('id="block-add"'):]
    assert f'name="name" value="{data["name"]}"' in add
    assert 'name="link_slot" value="1" checked' in add
    assert len(blocks_of(ui)) == 2


def test_block_edit_error_keeps_the_typed_text(tmp_path: Path) -> None:
    ui = make_dt_ui(tmp_path)
    main, paid, _ = seed_main(ui)
    nf(ui, f"{URL}/blocks/{paid}/edit", data={"name": "Paid links", "text": "Bad <text>", "template": str(main)})
    html = page(ui)
    assert "The block text cannot contain &lt; or &gt;" in html
    assert f'id="block-text-{paid}" name="text" rows="4">Bad &lt;text&gt;</textarea>' in html
    assert next(b for b in blocks_of(ui) if b.id == paid).text == PAID
    nf(ui, f"{URL}/blocks/999/edit", data={"name": "X", "text": "Y"})
    assert "That block does not exist." in page(ui)


def test_deleting_a_used_block_is_refused_and_an_unused_one_goes(tmp_path: Path) -> None:
    ui = make_dt_ui(tmp_path)
    main, paid, _ = seed_main(ui)
    resp = nf(ui, f"{URL}/blocks/{paid}/delete", data={"template": str(main)})
    assert resp.status_code == 303 and resp.headers["location"] == f"{URL}?template={main}#blocks"
    assert 'The template "Main" uses this block. Take it out of the template first.' in page(ui)
    assert any(b.id == paid for b in blocks_of(ui))
    with store_of(ui) as store:
        spare = store.add_template_block("Spare", "Spare text.")
    nf(ui, f"{URL}/blocks/{spare}/delete", data={"template": str(main)})
    assert all(b.id != spare for b in blocks_of(ui))
    assert "Shared block deleted." in page(ui)
    nf(ui, f"{URL}/blocks/{spare}/delete", data={"template": str(main)})
    assert "That block does not exist." in page(ui)


def test_blocks_can_be_made_before_any_template(tmp_path: Path) -> None:
    ui = make_dt_ui(tmp_path)
    html = page(ui)
    assert 'id="blocks"' in html and "No shared blocks yet." in html
    nf(ui, URL + "/blocks/add", data={"name": "Gear", "text": "My gear list:"})
    assert [b.name for b in blocks_of(ui)] == ["Gear"]
    assert templates_of(ui) == []


# --- the Shorts setting -----------------------------------------------------------------------------------

def test_the_shorts_setting_is_off_by_default_and_saves(tmp_path: Path) -> None:
    ui = make_dt_ui(tmp_path)
    seed_main(ui)
    html = section(page(ui), "settings")
    assert "Shorts follow templates too" in html and 'id="include-shorts" name="include_shorts" value="1">' in html
    resp = nf(ui, URL + "/settings", data={"include_shorts": "1"})
    assert resp.status_code == 303 and resp.headers["location"] == URL + "#settings"
    with store_of(ui) as store:
        assert store.templates_include_shorts()
    html = page(ui)
    assert "Shorts now follow templates." in html and 'name="include_shorts" value="1" checked>' in html
    assert re.search(r'id="tp-cover-excluded"[^>]*>.*?<span class="tp-stat-v">1</span>', html, re.S)  # only the hand-excluded video
    nf(ui, URL + "/settings", data={})
    with store_of(ui) as store:
        assert not store.templates_include_shorts()
    assert "Shorts are now left out of templates." in page(ui)


# --- the preview ------------------------------------------------------------------------------------------

def states_of(html: str) -> dict[str, str]:
    part = section(html, "preview")
    part = part[part.index('id="tp-states"'):]
    return dict(re.findall(r'<li><span>([^<]+?) <span class="muted">[^<]+</span></span><span class="pill[^"]*"[^>]*>([^<]+)</span>',
                           part))


def test_the_preview_defaults_to_the_most_viewed_video_that_follows(tmp_path: Path) -> None:
    ui = make_dt_ui(tmp_path)
    seed_main(ui)
    html = page(ui)
    preview = section(html, "preview")
    assert '<option value="vid000001" selected>Repotting a spiky cactus</option>' in preview
    assert '<optgroup label="Follow this template">' in preview and '<optgroup label="Other videos">' in preview
    assert "10 of 10 points" in preview
    assert states_of(html) == {"Opening": "OK", "About the video": "Missing", "Chapters": "OK",
                               "Watch next": "Missing", "Paid links": "OK", "Support and follow": "OK",
                               "Hashtags": "OK"}
    size = len(FULL.encode("utf-8"))
    assert f'id="tp-bytes">{size} of 5,000 bytes</span>' in preview
    assert '"more" falls about here' in preview
    assert 'method="get"' in preview and 'name="video"' in preview
    assert no_inline_script_or_style(html)


def test_the_preview_shows_out_of_date_missing_and_tray_text(tmp_path: Path) -> None:
    ui = make_dt_ui(tmp_path)
    seed_main(ui)
    html = page(ui, "?video=vid000002")
    preview = section(html, "preview")
    assert '<option value="vid000002" selected>' in preview
    states = states_of(html)
    assert states["Support and follow"] == "Out of date" and states["Paid links"] == "Missing"
    assert "Every line belongs to a section." in preview
    assert re.search(r"\d+ of 10 points", preview) and "10 of 10 points" not in preview
    assert "Support and follow is an old version" in preview and "Paid links is missing" in preview
    assert 'class="fix"' in preview

    tray = section(page(ui, "?video=vid000005"), "preview")
    assert 'id="tp-tray"' in tray and "Thanks for watching!" in tray and "Not in the template" in tray


def test_the_preview_marks_where_more_falls_and_block_lines(tmp_path: Path) -> None:
    ui = make_dt_ui(tmp_path)
    seed_main(ui)
    preview = section(page(ui, "?video=vid000001"), "preview")
    desc = preview[preview.index('id="tp-text"'):preview.index('id="tp-tray"')]
    before = desc[:desc.index('class="tp-more"')]
    assert OPENING in before  # the opening (99 characters) is above the line
    # the 150th character falls inside the paid-links line, so the marker splits it; both halves stay block lines
    assert '<div class="tp-line block">Paid li</div><div class="tp-more">' in desc
    assert '<div class="tp-line block">Pot: https://shop.invalid/pot</div>' in desc
    assert '<div class="tp-line block">Follow: https://links.invalid/follow</div>' in desc
    assert '<div class="tp-line">#cactus #repotting</div>' in desc
    short = section(page(ui, "?video=vid000003"), "preview")
    assert 'class="tp-more"' not in short and 'The whole description shows before "more".' in short
    assert "You excluded this video." in short  # the note says it is not one of this template's videos


def test_the_more_line_cuts_a_long_line_at_150_characters() -> None:
    from yezatas.web.template_view import preview_lines
    text = "a" * 140 + " " + "b" * 30 + "\nnext"
    lines, fits = preview_lines(text, None)
    assert not fits
    assert [(l.number, l.css) for l in lines] == [(1, ""), (None, "more"), (None, ""), (2, "")]
    assert len(lines[0].text) == 150 and lines[0].text + lines[2].text == text.split("\n")[0]


def test_an_unknown_video_in_the_address_falls_back_to_the_default(tmp_path: Path) -> None:
    ui = make_dt_ui(tmp_path)
    seed_main(ui)
    assert '<option value="vid000001" selected>' in section(page(ui, "?video=nope"), "preview")


def test_the_preview_with_no_videos(tmp_path: Path) -> None:
    ui = make_dt_ui(tmp_path, videos=[])
    seed_main(ui)
    preview = section(page(ui), "preview")
    assert "No videos are stored yet." in preview and 'id="preview-video"' not in preview


# --- CSRF, escaping and the shell --------------------------------------------------------------------------

def test_every_post_needs_the_csrf_token(tmp_path: Path) -> None:
    ui = make_dt_ui(tmp_path)
    main, paid, _ = seed_main(ui)
    with store_of(ui) as store:
        before = (store.list_description_templates(), store.list_template_blocks(), store.templates_include_shorts())
    attempts = [
        ("/start", {}), ("/new", {"name": "New"}), ("/settings", {"include_shorts": "1"}),
        (f"/{main}/rename", {"name": "Changed"}), (f"/{main}/default", {}), (f"/{main}/delete", {}),
        (f"/{main}/move", {"direction": "down"}), (f"/{main}/rules/add", {"kind": "tag", "value": "x"}),
        (f"/{main}/rules/remove", {"kind": "tag", "value": "x"}), (f"/{main}/sections/add", {"section": "about"}),
        (f"/{main}/sections/move", {"section": "hashtags", "direction": "up"}),
        (f"/{main}/sections/remove", {"section": "opening"}),
        ("/blocks/add", {"name": "B", "text": "T"}), (f"/blocks/{paid}/edit", {"name": "B", "text": "T"}),
        (f"/blocks/{paid}/delete", {}),
    ]
    for path, data in attempts:
        assert ui.client.post(URL + path, data=data, follow_redirects=False).status_code == 403, path
        assert ui.client.post(URL + path, data={**data, "csrf": "wrong"},
                              follow_redirects=False).status_code == 403, path
    with store_of(ui) as store:
        after = (store.list_description_templates(), store.list_template_blocks(), store.templates_include_shorts())
    assert after == before


def test_hostile_names_and_texts_are_escaped(tmp_path: Path) -> None:
    ui = make_dt_ui(tmp_path)
    with store_of(ui) as store:
        block = store.add_template_block("<script>alert(1)</script>", "Plain text")
        store.add_description_template("<img src=x onerror=alert(1)>", [{"kind": "block", "block_id": block}],
                                       rules=[{"kind": "tag", "value": "<b>"}], is_default=True)
    html = page(ui)
    assert "<script>alert" not in html and "<img src=x" not in html and "<b>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert no_inline_script_or_style(html)


def test_the_nav_has_the_item_right_after_bulk_edit(tmp_path: Path) -> None:
    ui = make_dt_ui(tmp_path)
    html = page(ui)
    items = re.findall(r'<a class="nav-item[^"]*" href="([^"]+)"', html)
    assert items.index(URL) == items.index("/bulk") + 1
    assert re.findall(r'class="nav-item active" href="([^"]+)"', html) == [URL]
    assert re.search(r'class="nav-item active" href="/description-templates"[^>]*>.*?<span>Description templates</span>',
                     html)


# --- Pull playlists (phase 2d, read only) ----------------------------------------------------------------

from fakes import make_http_error  # noqa: E402
from webhelpers import write_client_file, write_token  # noqa: E402
from yezatas.quota import QuotaGuard, _utc_now, quota_day  # noqa: E402

PULL = f"{URL}/playlists/pull"


@pytest.fixture
def no_real_client(monkeypatch: pytest.MonkeyPatch) -> None:
    import googleapiclient.discovery

    def refuse(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("no real YouTube client may be built in these tests")

    monkeypatch.setattr(googleapiclient.discovery, "build", refuse)


def pull_ui(tmp_path: Path, signed_in: bool = True) -> tuple[Ui, int]:
    """The usual five videos, "Main" as the default and "Reviews" without rules; two playlists on the fake
    channel: one with two catalog videos and one from another channel."""
    ui = make_dt_ui(tmp_path)
    seed_main(ui)
    with store_of(ui) as store:
        reviews = store.add_description_template("Reviews", [{"kind": "opening"}])
    if signed_in:
        write_client_file(ui.config)
        write_token(ui.config)
    ui.reader.playlists = [
        {"id": "PLgarden01", "title": "Soil reviews", "video_ids": ["vid000003", "vid000005"]},
        {"id": "PLfriends", "title": "Friends' gardens", "video_ids": ["otherchan01"]},
    ]
    ui.reader.calls.clear()
    return ui, reviews


def flashes(ui: Ui, query: str = "") -> str:
    html = page(ui, query).replace("&#39;", "'")
    return " ".join(re.findall(r'<div class="flash [^"]*" role="[^"]*">([^<]*)', html))


def ledger_notes(ui: Ui) -> list[tuple[str, str]]:
    with store_of(ui) as store:
        return [(r["operation"], r["note"]) for r in store.quota_entries(quota_day(_utc_now()))]


def test_the_pull_button_and_its_line(tmp_path: Path, no_real_client: None) -> None:
    ui, reviews = pull_ui(tmp_path)
    form = section(page(ui, f"?template={reviews}"), "template")
    pull = form[form.index('id="playlists-pull"'):]
    pull = pull[:pull.index("</form>")]
    assert f'action="{PULL}"' in pull and "Pull playlists</button>" in pull
    assert "No playlists stored yet. Pulling only reads from YouTube." in pull
    assert "Playlists are not pulled yet" in form
    with store_of(ui) as store:
        store.replace_playlists({"PLa": ("A", []), "PLb": ("B", [])})
    assert "2 playlists stored. Pulling only reads from YouTube." in page(ui, f"?template={reviews}")
    assert 'id="playlists-pull"' not in section(page(ui), "template")  # the default template has no rules


def test_pulling_stores_the_playlists(tmp_path: Path, no_real_client: None) -> None:
    ui, reviews = pull_ui(tmp_path)
    resp = nf(ui, PULL, data={"template": str(reviews)})
    assert resp.status_code == 303 and resp.headers["location"] == f"{URL}?template={reviews}#rules"
    with store_of(ui) as store:
        stored = {p.playlist_id: (p.title, p.video_ids) for p in store.playlists()}
    assert stored == {"PLgarden01": ("Soil reviews", ("vid000003", "vid000005")),
                      "PLfriends": ("Friends' gardens", ("otherchan01",))}
    assert "Read 2 playlists with 3 videos. Used 4 quota units." in flashes(ui, f"?template={reviews}")
    assert ledger_notes(ui) == [("channels.list", "playlist pull"), ("playlists.list", "playlist pull"),
                                ("playlistItems.list", "playlist pull"), ("playlistItems.list", "playlist pull")]
    assert {c[0] for c in ui.reader.calls} == {"channels_mine", "playlists_mine", "playlist_items"}


def test_the_select_lists_playlists_with_catalog_counts(tmp_path: Path, no_real_client: None) -> None:
    ui, reviews = pull_ui(tmp_path)
    nf(ui, PULL, data={"template": str(reviews)})
    form = section(page(ui, f"?template={reviews}"), "template").replace("&#39;", "'")
    assert '<option value="PLgarden01">Soil reviews (2 videos)</option>' in form
    assert '<option value="PLfriends">Friends\' gardens (0 videos)</option>' in form
    assert "2 playlists stored. Pulling only reads from YouTube." in form


def test_a_refused_playlist_is_named(tmp_path: Path, no_real_client: None) -> None:
    ui, reviews = pull_ui(tmp_path)
    ui.reader.playlist_errors["PLfriends"] = make_http_error(404, "playlistNotFound")
    nf(ui, PULL, data={"template": str(reviews)})
    assert ("Read 1 playlist with 2 videos. Used 4 quota units. YouTube did not let Yezatas read 1 playlist: "
            "Friends' gardens.") in flashes(ui, f"?template={reviews}")


def test_pulling_when_not_signed_in(tmp_path: Path, no_real_client: None) -> None:
    ui, reviews = pull_ui(tmp_path, signed_in=False)
    nf(ui, PULL, data={"template": str(reviews)})
    assert ("You are not signed in, or the saved sign-in is no longer valid. Sign in again (step 6)."
            in flashes(ui, f"?template={reviews}"))
    assert ui.reader.calls == [] and ledger_notes(ui) == []


def test_pulling_with_too_little_quota(tmp_path: Path, no_real_client: None) -> None:
    ui, reviews = pull_ui(tmp_path)
    with store_of(ui) as store:
        store.replace_playlists({"PLold": ("Old list", ["vid000001"])})
        QuotaGuard(store, ui.config.daily_quota_budget).charge("videos.list", ui.config.daily_quota_budget - 3)
    nf(ui, PULL, data={"template": str(reviews)})
    assert ("Not enough quota left: need 2 units for 2 playlistItems.list pages of 2 playlists but only 1 "
            "remaining. It resets at midnight Pacific time.") in flashes(ui, f"?template={reviews}")
    with store_of(ui) as store:
        assert [p.playlist_id for p in store.playlists()] == ["PLold"]


def test_pulling_when_youtube_fails(tmp_path: Path, no_real_client: None) -> None:
    ui, reviews = pull_ui(tmp_path)
    ui.reader.playlists_error = make_http_error(500, "backendError", "The service is resting.")
    nf(ui, PULL, data={"template": str(reviews)})
    assert ("YouTube returned an error: HTTP 500 (backendError). The service is resting."
            in flashes(ui, f"?template={reviews}"))
    ui.reader.playlists_error = make_http_error(403, "quotaExceeded", "Quota.")
    nf(ui, PULL, data={"template": str(reviews)})
    assert ("Today's YouTube quota is used up. It resets at midnight Pacific time, so try again then."
            in flashes(ui, f"?template={reviews}"))


def test_the_pull_needs_the_csrf_token(tmp_path: Path, no_real_client: None) -> None:
    ui, _ = pull_ui(tmp_path)
    assert ui.client.post(PULL, data={}, follow_redirects=False).status_code == 403
    assert ui.client.post(PULL, data={"csrf": "wrong"}, follow_redirects=False).status_code == 403
    assert ui.reader.calls == []


def test_a_pulled_playlist_rule_claims_its_videos(tmp_path: Path, no_real_client: None) -> None:
    ui, reviews = pull_ui(tmp_path)
    nf(ui, PULL, data={"template": str(reviews)})
    nf(ui, f"{URL}/{reviews}/rules/add", data={"kind": "playlist", "value": "PLgarden01", "template": str(reviews)})
    assert "In the playlist Soil reviews" in section(page(ui, f"?template={reviews}"), "template")
    video = ui.client.get("/videos/vid000005").text
    state = video[video.index('id="tp-vstate"'):]
    state = state[:state.index("</p>")]
    assert "<strong>Reviews</strong>" in state and "In the playlist Soil reviews." in state


def test_a_rule_whose_playlist_is_gone(tmp_path: Path, no_real_client: None) -> None:
    ui, reviews = pull_ui(tmp_path)
    nf(ui, PULL, data={"template": str(reviews)})
    nf(ui, f"{URL}/{reviews}/rules/add", data={"kind": "playlist", "value": "PLgarden01", "template": str(reviews)})
    ui.reader.playlists = [p for p in ui.reader.playlists if p["id"] != "PLgarden01"]
    nf(ui, PULL, data={"template": str(reviews)})
    form = section(page(ui, f"?template={reviews}"), "template")
    rule = form[form.index('class="tp-rule"'):]
    rule = rule[:rule.index("</li>")]
    assert "<span>Playlist no longer on YouTube</span>" in rule
    assert 'name="value" value="PLgarden01"' in rule and ">Remove</button>" in rule
    video = ui.client.get("/videos/vid000005").text
    state = video[video.index('id="tp-vstate"'):]
    assert "<strong>Main</strong>" in state[:state.index("</p>")]  # the rule matches nothing now
    nf(ui, f"{URL}/{reviews}/rules/remove", data={"kind": "playlist", "value": "PLgarden01",
                                                   "template": str(reviews)})
    assert 'class="tp-rule"' not in section(page(ui, f"?template={reviews}"), "template")
