"""The description template on the video page (phase 2c): the template menu, the Sections / Plain text tabs, the
read-only Sections view with the "Not in the template" tray, and Apply template, which only saves a draft.
Made-up gardening videos only; nothing here calls YouTube or a model."""

from __future__ import annotations

import html as html_mod
from pathlib import Path
from typing import Any

from webhelpers import Ui, make_ui, make_video, seed_catalog
from yezatas.store import Store

PAID = "Some links below are paid links; they support the channel."
SUPPORT = "Support the garden channel:\nhttps://shop.test/support"
OLD_SUPPORT = "Support us:\nhttps://shop.test/old-support"
OPENING = "We build a raised garden bed in one day and plant the first seeds of the season."
ABOUT = "The soil mix is one part compost to two parts topsoil."
CHAPTERS = "0:00 Intro\n1:30 Soil mix\n4:10 Planting"
WATCH = "Watch next: https://youtu.be/abc123XYZ00"
PAID_LINKS = PAID + "\nhttps://shop.test/trowel\nhttps://shop.test/gloves"
HASHTAGS = "#gardening #raisedbed"
EXTRA = "See more at https://links.invalid/more"

VID = "vid000001"
URL = f"/videos/{VID}"


def doc(*parts: str) -> str:
    return "\n\n".join(parts)


FULL = doc(OPENING, ABOUT, CHAPTERS, WATCH, PAID_LINKS, SUPPORT, HASHTAGS)
# the support block missing, an extra line in the tray
NEEDS_WORK = doc(OPENING, ABOUT, CHAPTERS, WATCH, PAID_LINKS, EXTRA, HASHTAGS)


def boom(*args: Any, **kwargs: Any) -> Any:
    raise AssertionError("no model or YouTube client may be called")


def make_tp_ui(tmp_path: Path, description: str = NEEDS_WORK, *, templates: bool = True) -> Ui:
    """One made-up video (and a second one) and, with `templates`, the template "Garden" with two shared blocks."""
    ui = make_ui(tmp_path, suggest=boom, copilot=boom, chapters=boom, writer_factory=boom,
                 write_credentials_loader=boom)
    seed_catalog(ui.config, [
        make_video(1, title="How to build a raised garden bed", description=description,
                   tags=["garden bed", "raised bed"], duration="PT10M3S", privacy_status="public"),
        make_video(2, title="Watering tomatoes", description="Water in the morning.", duration="PT8M",
                   privacy_status="public"),
    ])
    if templates:
        with Store(ui.config.catalog_db) as store:
            paid = store.add_template_block("Paid links", PAID, link_slot=True)
            support = store.add_template_block("Support and follow", OLD_SUPPORT)
            store.update_template_block(support, text=SUPPORT)  # OLD_SUPPORT becomes an older version
            store.add_description_template("Garden", [
                {"kind": "opening"}, {"kind": "about"}, {"kind": "chapters"}, {"kind": "watch_next"},
                {"kind": "block", "block_id": paid}, {"kind": "block", "block_id": support},
                {"kind": "hashtags"}], is_default=True)
            store.add_description_template("Shorts", [{"kind": "opening"}, {"kind": "hashtags"}])
    ui.reader.calls.clear()
    return ui


def page(ui: Ui, query: str = "") -> str:
    resp = ui.client.get(URL + query)
    assert resp.status_code == 200
    return html_mod.unescape(resp.text)


def template_ids(ui: Ui) -> dict[str, int]:
    with Store(ui.config.catalog_db) as store:
        return {t.name: t.id for t in store.list_description_templates()}


def draft(ui: Ui, vid: str = VID) -> Any:
    with Store(ui.config.catalog_db) as store:
        return store.get_draft(vid)


def choices(ui: Ui) -> dict[str, int | None]:
    with Store(ui.config.catalog_db) as store:
        return store.template_choices()


def apply(ui: Ui, remove: list[str] | None = None) -> Any:
    return ui.post(URL + "/template/apply", data={"remove": remove or []}, follow_redirects=False)


def no_outside_calls(ui: Ui) -> None:
    assert ui.reader.calls == []


# --- the menu, the state and the tabs ---------------------------------------------------------------------

def test_the_menu_and_state(tmp_path: Path) -> None:
    ui = make_tp_ui(tmp_path)
    html = page(ui)
    menu = html[html.index('id="tp-choice"'):html.index("</select>")]
    ids = template_ids(ui)
    assert '<option value="auto" selected>Automatic: Garden (' in menu
    assert f'<option value="{ids["Garden"]}">Garden</option>' in menu
    assert f'<option value="{ids["Shorts"]}">Shorts</option>' in menu
    assert '<option value="exclude">Exclude this video</option>' in menu
    state = html[html.index('id="tp-vstate"'):]
    state = state[:state.index("</p>")]
    assert "<strong>Garden</strong>" in state
    assert 'id="tp-vpoints">' in state and " of 10</span>" in state


def test_the_default_tab_is_sections_with_a_template(tmp_path: Path) -> None:
    html = page(make_tp_ui(tmp_path))
    assert 'id="tp-tab-sections" class="active"' in html
    assert 'id="tp-sections"' in html
    assert 'name="description"' not in html  # the edit form is on Plain text


def test_plain_text_shows_the_edit_form(tmp_path: Path) -> None:
    html = page(make_tp_ui(tmp_path), "?view=text")
    assert 'id="tp-tab-text" class="active"' in html
    assert 'id="tp-sections"' not in html
    assert 'name="description"' in html


def test_an_unknown_view_falls_back_to_the_default(tmp_path: Path) -> None:
    html = page(make_tp_ui(tmp_path), "?view=nonsense")
    assert 'id="tp-tab-sections" class="active"' in html


def test_choosing_a_template_by_hand(tmp_path: Path) -> None:
    ui = make_tp_ui(tmp_path)
    shorts = template_ids(ui)["Shorts"]
    resp = ui.post(URL + "/template", data={"choice": str(shorts), "view": "sections"}, follow_redirects=False)
    assert resp.status_code == 303 and resp.headers["location"] == f"{URL}?view=sections#edit"
    assert choices(ui) == {VID: shorts}
    html = page(ui)
    assert "This video now follows Shorts." in html
    assert f'<option value="{shorts}" selected>Shorts</option>' in html
    assert "<strong>Shorts</strong>" in html


def test_excluding_and_back_to_automatic(tmp_path: Path) -> None:
    ui = make_tp_ui(tmp_path)
    resp = ui.post(URL + "/template", data={"choice": "exclude", "view": "text"}, follow_redirects=False)
    assert resp.headers["location"] == f"{URL}?view=text#edit"
    assert choices(ui) == {VID: None}
    html = page(ui, "?view=sections")
    assert "This video is now left out of description templates." in html
    assert 'id="tp-excluded"' in html and "Choose a template above to include it." in html
    assert '<option value="exclude" selected>' in html
    assert 'id="tp-apply"' not in html
    assert 'id="tp-tab-text" class="active"' in page(ui)  # excluded: Plain text by default
    ui.post(URL + "/template", data={"choice": "auto"}, follow_redirects=False)
    assert choices(ui) == {}
    assert "This video now follows the automatic choice." in page(ui)


def test_a_deleted_template_is_refused(tmp_path: Path) -> None:
    ui = make_tp_ui(tmp_path)
    for choice in ("999", "not-a-number", ""):
        ui.post(URL + "/template", data={"choice": choice}, follow_redirects=False)
        assert "That template does not exist." in page(ui)
    assert choices(ui) == {}


def test_the_menu_on_an_unknown_video(tmp_path: Path) -> None:
    ui = make_tp_ui(tmp_path)
    assert ui.post("/videos/nope/template", data={"choice": "auto"}).status_code == 404
    assert ui.post("/videos/nope/template/apply").status_code == 404


def test_without_templates(tmp_path: Path) -> None:
    ui = make_tp_ui(tmp_path, templates=False)
    assert 'id="tp-choice"' not in page(ui)
    assert 'id="tp-tab-text" class="active"' in page(ui)
    html = page(ui, "?view=sections")
    assert ('You have no description templates yet. <a href="/description-templates">Set up description '
            'templates</a>') in html


# --- the Sections view ------------------------------------------------------------------------------------

def test_the_sections_and_the_tray(tmp_path: Path) -> None:
    html = page(make_tp_ui(tmp_path))
    view = html[html.index('id="tp-sections"'):]
    assert "These sections show the text on YouTube now. To edit the text, use Plain text." in view
    opening = view[view.index('id="tp-sec-opening"'):]
    opening = opening[:opening.index("</li>")]
    assert OPENING in opening and "Per video" in opening and ">OK</span>" in opening
    support = view[view.index(">Support and follow<"):]
    support = support[:support.index("</li>")]
    assert "From template" in support and ">Missing</span>" in support and "None yet" in support
    tray = view[view.index('id="tp-tray"'):]
    assert "Not in the template" in tray
    assert "Each line is kept when you apply the template, unless you tick it." in tray
    assert f"<pre>{EXTRA}</pre>" in tray
    assert 'name="remove"' in tray and 'form="tp-apply"' in tray and "Remove when applying" in tray
    assert "Apply template</button>" in view
    assert "Saves the result as a draft. Nothing is sent to YouTube until you approve it on the Changes page." \
        in view


def test_a_missing_chapters_section_links_to_the_tool(tmp_path: Path) -> None:
    html = page(make_tp_ui(tmp_path, doc(OPENING, ABOUT, WATCH, PAID_LINKS, SUPPORT, HASHTAGS)))
    chapters = html[html.index('id="tp-sec-chapters"'):]
    chapters = chapters[:chapters.index("</li>")]
    assert "None yet" in chapters and '<a href="#tool-chapters">Make chapters</a>' in chapters


def test_text_is_escaped(tmp_path: Path) -> None:
    ui = make_tp_ui(tmp_path, doc(OPENING, "<script>alert(1)</script> https://links.invalid/x", PAID_LINKS))
    raw = ui.client.get(URL).text
    assert "<script>alert(1)</script>" not in raw
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in raw


def test_the_what_to_fix_row_links_to_the_sections_view(tmp_path: Path) -> None:
    html = page(make_tp_ui(tmp_path))
    assert f'<a class="vfix-go" href="/videos/{VID}?view=sections#edit">Apply template</a>' in html


# --- Apply template ---------------------------------------------------------------------------------------

def test_apply_saves_a_draft_and_nothing_else(tmp_path: Path) -> None:
    ui = make_tp_ui(tmp_path)
    resp = apply(ui)
    assert resp.status_code == 303 and resp.headers["location"] == f"{URL}?view=sections#edit"
    row = draft(ui)
    assert row is not None and row.source == "template"
    assert row.description == doc(OPENING, ABOUT, CHAPTERS, WATCH, PAID_LINKS, SUPPORT, EXTRA, HASHTAGS)
    assert row.title == "How to build a raised garden bed"
    assert list(row.tags) == ["garden bed", "raised bed"]
    html = page(ui)
    assert ("Applied Garden: Added Support and follow. Saved as a draft. Nothing is sent to YouTube until you "
            "approve it and push.") in html
    assert 'id="video-draft-template">With this draft: ' in html and " of 10 on this check" in html
    assert 'id="tp-apply-blocked">This video already has a draft. Push or discard it first.' in html
    assert "Apply template</button>" in html and "disabled>Apply template" in html
    assert "Description template applied" in ui.client.get("/changes").text
    with Store(ui.config.catalog_db) as store:
        assert store.get_video(VID).description == NEEDS_WORK  # the catalog copy is untouched
    no_outside_calls(ui)


def test_apply_with_a_draft_is_refused(tmp_path: Path) -> None:
    ui = make_tp_ui(tmp_path)
    apply(ui)
    first = draft(ui)
    apply(ui)
    assert "This video already has a draft. Push or discard it first." in page(ui)
    assert draft(ui).description == first.description


def test_apply_when_nothing_changes(tmp_path: Path) -> None:
    ui = make_tp_ui(tmp_path, FULL)
    apply(ui)
    assert draft(ui) is None
    assert "This description already follows Garden." in page(ui)


def test_apply_removes_ticked_tray_lines(tmp_path: Path) -> None:
    ui = make_tp_ui(tmp_path)
    html = ui.client.get(URL).text
    key = html[html.index('id="tp-tray-') + len('id="tp-tray-'):]
    key = key[:key.index('"')]
    apply(ui, remove=[key])
    assert draft(ui).description == FULL
    assert "Removed 1 line that was not in the template" in page(ui)


def test_apply_ignores_unknown_remove_keys(tmp_path: Path) -> None:
    ui = make_tp_ui(tmp_path)
    apply(ui, remove=["0000000000", "<x>"])
    assert EXTRA in draft(ui).description


def test_apply_over_the_byte_limit_makes_no_draft(tmp_path: Path) -> None:
    ui = make_tp_ui(tmp_path, doc(OPENING, "Soil " * 990, PAID_LINKS, HASHTAGS))
    apply(ui)
    assert draft(ui) is None
    html = page(ui)
    assert "With the template the description would be " in html
    assert "bytes, over YouTube's limit of 5,000. No draft was made." in html


def test_apply_on_an_excluded_video(tmp_path: Path) -> None:
    ui = make_tp_ui(tmp_path)
    ui.post(URL + "/template", data={"choice": "exclude"})
    apply(ui)
    assert draft(ui) is None
    assert "You excluded this video. No template was applied." in page(ui)


def test_apply_without_templates(tmp_path: Path) -> None:
    ui = make_tp_ui(tmp_path, templates=False)
    apply(ui)
    assert draft(ui) is None
    assert "No template was applied." in page(ui)


def test_both_posts_need_the_csrf_token(tmp_path: Path) -> None:
    ui = make_tp_ui(tmp_path)
    for path, data in ((URL + "/template", {"choice": "exclude"}), (URL + "/template/apply", {})):
        assert ui.client.post(path, data=data, follow_redirects=False).status_code == 403
        assert ui.client.post(path, data={**data, "csrf": "wrong"}, follow_redirects=False).status_code == 403
    assert choices(ui) == {} and draft(ui) is None
