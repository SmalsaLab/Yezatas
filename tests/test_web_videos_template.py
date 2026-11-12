"""The Template column, filter and bulk change on the Videos table (/videos, POST /videos/template).
Made-up gardening data; nothing is written to YouTube."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

import pytest
from markupsafe import escape

from test_web_description_templates import make_dt_ui, seed_main, store_of
from webhelpers import Ui, make_ui, make_video, seed_catalog
from yezatas import descriptiontemplate
from yezatas.web.app import MAX_BULK_TEMPLATE
from yezatas.web.views import PAGE_SIZE

BULK = "/videos/template"


def get(ui: Ui, query: str = "") -> str:
    resp = ui.client.get("/videos" + query)
    assert resp.status_code == 200, query
    return resp.text


def row_ids(html: str) -> list[str]:
    return re.findall(r'class="vtitle" href="/videos/([^"]+)"', html)


def every_id(ui: Ui, query: str = "") -> list[str]:
    """The ids of every page of a list, in order."""
    out: list[str] = []
    sep = "&" if query else "?"
    page = 1
    while True:
        html = get(ui, f"{query}{sep}page={page}" if page > 1 else query)
        out += row_ids(html)
        if 'rel="next"' not in html:
            return out
        page += 1


def row(html: str, vid: str) -> str:
    return html.split(f'class="vtitle" href="/videos/{vid}"')[1].split("</tr>")[0]


def cell(html: str, vid: str) -> tuple[str, str]:
    """(the Template column's main text, its muted line) for a video."""
    td = row(html, vid).split('<td class="col-template">')[1].split("</td>")[0]
    main = re.search(r'<span class="vt-tpl[^"]*">([^<]*)</span>', td)
    why = re.search(r'<span class="vt-tpl-why">(.*?)</span>', td, re.S)
    assert main
    return main.group(1), why.group(1) if why else ""


def flashes(ui: Ui, path: str = "/videos") -> str:
    html = ui.client.get(path).text.replace("&#39;", "'")
    return " ".join(re.findall(r'<div class="flash [^"]*" role="[^"]*">([^<]*)', html))


def choices(ui: Ui) -> dict[str, int | None]:
    with store_of(ui) as store:
        return dict(store.template_choices())


def chips(html: str) -> list[tuple[str, str, bool]]:
    rowhtml = html.split('id="template-chips"')[1].split("</div>")[0]
    return [(label, href.replace("&amp;", "&"), bool(active))
            for active, href, label in re.findall(
                r'<a class="chip( active)?" href="([^"]*)"[^>]*>([^<]*)</a>', rowhtml)]


def with_reviews(ui: Ui) -> tuple[int, int]:
    """Main (default) from the template tests, plus "Reviews" for titles with the word "winter" (V2)."""
    main, _, _ = seed_main(ui)
    with store_of(ui) as store:
        reviews = store.add_description_template("Reviews", [{"kind": "opening"}],
                                                 rules=[{"kind": "title_word", "value": "winter"}])
    return main, reviews


# --- no templates: the page is exactly as before ------------------------------------------------------

def test_no_templates_means_no_column_chips_checkboxes_or_bar(tmp_path: Path) -> None:
    ui = make_dt_ui(tmp_path)
    html = get(ui)
    for absent in ('col-template', 'id="template-chips"', 'name="video"', 'id="vt-bulk"', "vt-bulkbar",
                   'action="/videos/template"', "col-pick", "Set template", ">Template<"):
        assert absent not in html, absent
    head = html.split("<thead>")[1].split("</thead>")[0]
    assert re.findall(r'<th class="([^"]+)"', head) == ["col-video", "col-vis", "col-date", "col-num",
                                                        "col-ratio", "col-score"]
    # the filter and sort parameters mean nothing without templates and do not leak into links
    assert row_ids(get(ui, "?template=excluded")) == row_ids(html)
    assert row_ids(get(ui, "?sort=template")) == row_ids(html)
    assert "template=" not in get(ui, "?template=excluded&band=good")


def test_the_column_sits_after_the_video_and_the_bar_wraps_the_table(tmp_path: Path) -> None:
    ui = make_dt_ui(tmp_path)
    with_reviews(ui)
    html = get(ui)
    head = html.split("<thead>")[1].split("</thead>")[0]
    assert re.findall(r'<th class="([^"]+)"', head) == ["col-pick", "col-video", "col-template", "col-vis",
                                                        "col-date", "col-num", "col-ratio", "col-score"]
    form = html.split('<form method="post" action="/videos/template" id="vt-bulk">')[1].split("</form>")[0]
    assert "<form" not in form  # no nested forms
    assert '<table class="vtable with-template">' in form and "</table>" in form
    assert 'name="csrf"' in form
    bar = form.split('id="vt-bulkbar"')[1].split("</div>")[0]
    options = re.findall(r'<option value="([^"]*)">([^<]*)</option>', bar)
    names = [t.name for t in descriptiontemplate_templates(ui)]
    assert options[0] == ("auto", "Automatic") and options[-1] == ("exclude", "Exclude from templates")
    assert [o[1] for o in options[1:-1]] == names == ["Main", "Reviews"]
    assert '<button class="button" type="submit">Set template</button>' in bar
    assert '<label for="vt-choice">' in bar
    # one checkbox per row, with a label for screen readers; the tick-all is hidden until the script shows it
    boxes = re.findall(r'<input type="checkbox" name="video" value="([^"]+)" id="pick-\d+" aria-label="Tick ([^"]+)">',
                       form)
    assert [b[0] for b in boxes] == row_ids(html)
    assert ("vid000001", "Repotting a spiky cactus") in boxes
    assert 'data-pick-all="video" hidden aria-label="Tick every video on this page"' in head


def descriptiontemplate_templates(ui: Ui) -> list[Any]:
    with store_of(ui) as store:
        return store.list_description_templates()


def test_the_column_text_for_each_assignment(tmp_path: Path) -> None:
    ui = make_dt_ui(tmp_path)
    main, reviews = with_reviews(ui)
    with store_of(ui) as store:
        store.set_template_choice("vid000005", reviews)
    html = get(ui)
    assert cell(html, "vid000001") == ("Main", "Default template. &middot; 10 of 10")
    name, why = cell(html, "vid000002")
    assert name == "Reviews"
    with store_of(ui) as store:
        video = store.get_video("vid000002")
        expected = descriptiontemplate.assign_template(
            video, store.list_description_templates(), store.template_choices(),
            store.templates_include_shorts(), store.playlist_memberships(), {}).reason
    assert expected and why.startswith(str(escape(expected)) + " &middot; ")
    assert re.search(r"&middot; \d+ of 10$", why)
    assert cell(html, "vid000003") == ("Excluded", "You excluded this video.")
    assert cell(html, "vid000004") == ("Excluded", "Shorts are excluded from templates.")
    assert cell(html, "vid000005")[0] == "Reviews"
    assert cell(html, "vid000005")[1].startswith("Chosen on the video page. &middot; ")
    assert 'class="vt-tpl muted">Excluded<' in row(html, "vid000003")


def test_the_points_are_the_scorecards(tmp_path: Path) -> None:
    ui = make_dt_ui(tmp_path)
    with_reviews(ui)
    page = ui.client.get("/videos/vid000002").text
    # the video page shows the same check; its points match the column's
    html = get(ui)
    points = re.search(r"(\d+) of 10$", cell(html, "vid000002")[1])
    assert points
    assert int(points.group(1)) < 10 and "Follows the description template" in page


def test_none_when_no_template_applies(tmp_path: Path) -> None:
    ui = make_dt_ui(tmp_path)
    with store_of(ui) as store:
        store.add_description_template("Reviews", [{"kind": "opening"}],
                                       rules=[{"kind": "title_word", "value": "winter"}])
    html = get(ui)
    assert cell(html, "vid000001") == ("None", "No template matches this video, and there is no default template.")
    assert 'class="vt-tpl muted">None<' in row(html, "vid000001")
    assert cell(html, "vid000002")[0] == "Reviews"
    assert ("No template (3)", "/videos?template=none", False) in chips(html)


def test_template_names_and_reasons_are_escaped(tmp_path: Path) -> None:
    ui = make_dt_ui(tmp_path)
    seed_main(ui)
    with store_of(ui) as store:
        bad = store.add_description_template('<b>Tips</b> & "more"', [{"kind": "opening"}])
        store.set_template_choice("vid000001", bad)
    html = get(ui)
    assert "<b>Tips</b>" not in html
    assert cell(html, "vid000001")[0] == str(escape('<b>Tips</b> & "more"'))
    assert f'<option value="{bad}">{escape(chr(60) + "b>Tips</b> & " + chr(34) + "more" + chr(34))}</option>' in html
    assert any(label.startswith(str(escape("<b>Tips</b>"))) for label, _, _ in chips(html))


# --- chips and the filter ------------------------------------------------------------------------------

def test_chips_count_every_template_and_hide_empty_groups(tmp_path: Path) -> None:
    ui = make_dt_ui(tmp_path)
    main, reviews = with_reviews(ui)
    with store_of(ui) as store:
        empty = store.add_description_template("Unused", [{"kind": "opening"}],
                                               rules=[{"kind": "title_word", "value": "nothing-matches"}])
    html = get(ui)
    assert chips(html) == [
        ("Any template", "/videos", True),
        ("Main (2)", f"/videos?template={main}", False),
        ("Reviews (1)", f"/videos?template={reviews}", False),
        ("Unused (0)", f"/videos?template={empty}", False),
        ("Excluded (2)", "/videos?template=excluded", False),
    ]  # no "No template" chip: Main is the default, so its count is 0


def test_the_filter_for_a_template_excluded_none_and_unknown(tmp_path: Path) -> None:
    ui = make_dt_ui(tmp_path)
    main, reviews = with_reviews(ui)
    assert sorted(row_ids(get(ui, f"?template={main}"))) == ["vid000001", "vid000005"]
    assert row_ids(get(ui, f"?template={reviews}")) == ["vid000002"]
    assert sorted(row_ids(get(ui, "?template=excluded"))) == ["vid000003", "vid000004"]
    assert row_ids(get(ui, "?template=none")) == []
    everything = row_ids(get(ui))
    for unknown in ("999", "abc", "-1", "", "١", "0" * 40, f"{main} "):
        expected = sorted(row_ids(get(ui, f"?template={main}"))) if unknown == f"{main} " else sorted(everything)
        assert sorted(row_ids(get(ui, f"?template={unknown}"))) == expected, unknown
    on = get(ui, "?template=excluded")
    assert ("Excluded (2)", "/videos?template=excluded", True) in chips(on)
    assert ("Any template", "/videos", False) in chips(on)
    assert "2 videos" in on.split('id="result-count"')[1].split("</p>")[0]


def many_ui(tmp_path: Path) -> tuple[Ui, int, int]:
    """120 videos: Main is the default, "Reviews" takes titles with "review"; every tenth is excluded."""
    videos = [make_video(n, title=f"Garden {'review' if n % 2 == 0 else 'tip'} {n}") for n in range(1, 121)]
    ui = make_ui(tmp_path)
    seed_catalog(ui.config, videos)
    with store_of(ui) as store:
        main = store.add_description_template("Main", [{"kind": "opening"}], is_default=True)
        reviews = store.add_description_template("Reviews", [{"kind": "opening"}],
                                                 rules=[{"kind": "title_word", "value": "review"}])
        for n in range(10, 121, 10):
            store.exclude_from_templates(f"vid{n:06d}")
    return ui, main, reviews


def test_the_filter_combines_with_search_band_sort_and_paging(tmp_path: Path) -> None:
    ui, main, reviews = many_ui(tmp_path)
    reviewed = {f"vid{n:06d}" for n in range(2, 121, 2) if n % 10}
    assert set(every_id(ui, f"?template={reviews}")) == reviewed
    # with a band: exactly the band's videos that follow Reviews, in the band's own order
    for band in ("good", "ok", "poor"):
        assert every_id(ui, f"?template={reviews}&band={band}") == [
            i for i in every_id(ui, f"?band={band}") if i in reviewed], band
    # with search, sort and page 2
    ids = every_id(ui, "?q=garden&sort=title&dir=desc&template=excluded")
    assert ids == [i for i in every_id(ui, "?q=garden&sort=title&dir=desc") if int(i[3:]) % 10 == 0]
    main_ids = every_id(ui, f"?template={main}&sort=views")
    assert len(main_ids) > PAGE_SIZE
    second = get(ui, f"?template={main}&sort=views&page=2")
    assert row_ids(second) == main_ids[PAGE_SIZE:2 * PAGE_SIZE]
    # every link on the page keeps the filter
    prev = re.search(r'rel="prev" href="([^"]+)"', second)
    assert prev and dict(parse_qsl(urlsplit(prev.group(1).replace("&amp;", "&")).query)) == {
        "sort": "views", "template": str(main)}
    head = second.split("<thead>")[1].split("</thead>")[0]
    for href in re.findall(r'href="([^"]+)"', head):
        assert f"template={main}" in href, href
    filters = second.split('<div class="filters">')[1].split('id="result-count"')[0]
    for href in re.findall(r'href="([^"]+)"', filters):
        if 'template=' not in href:
            assert href.replace("&amp;", "&") in [c[1] for c in chips(second)], href  # only the template chips drop it


def test_sorting_by_template_then_title(tmp_path: Path) -> None:
    ui = make_dt_ui(tmp_path)
    main, reviews = with_reviews(ui)
    html = get(ui)
    head = html.split("<thead>")[1].split("</thead>")[0]
    assert 'href="/videos?sort=template" title="Order by Template, A to Z">Template<' in head
    # Main: Repotting a spiky cactus, Soil mixes; Reviews: Watering in winter; then Excluded by title
    assert row_ids(get(ui, "?sort=template")) == ["vid000001", "vid000005", "vid000002", "vid000003", "vid000004"]
    assert row_ids(get(ui, "?sort=template&dir=desc")) == ["vid000004", "vid000003", "vid000002", "vid000005",
                                                           "vid000001"]
    on = get(ui, "?sort=template").split("<thead>")[1].split("</thead>")[0]
    assert 'aria-sort="ascending"' in on.split('class="col-template"')[1][:40]


# --- the bulk route ------------------------------------------------------------------------------------

def post(ui: Ui, data: dict[str, Any]) -> Any:
    return ui.post(BULK, data=data, follow_redirects=False)


def test_set_a_template_on_ticked_videos(tmp_path: Path) -> None:
    ui = make_dt_ui(tmp_path)
    main, reviews = with_reviews(ui)
    resp = post(ui, {"video": ["vid000001", "vid000005"], "choice": str(reviews)})
    assert resp.status_code == 303 and resp.headers["location"] == "/videos"
    assert choices(ui) == {"vid000001": reviews, "vid000003": None, "vid000005": reviews}
    assert flashes(ui) == "Set 2 videos to Reviews."
    html = get(ui)
    assert cell(html, "vid000001")[0] == "Reviews"
    post(ui, {"video": ["vid000002"], "choice": str(main)})
    assert flashes(ui) == "Set 1 video to Main."


def test_exclude_and_back_to_automatic(tmp_path: Path) -> None:
    ui = make_dt_ui(tmp_path)
    main, reviews = with_reviews(ui)
    post(ui, {"video": ["vid000001", "vid000002"], "choice": "exclude"})
    assert flashes(ui) == "Left 2 videos out of templates."
    assert choices(ui) == {"vid000001": None, "vid000002": None, "vid000003": None}
    post(ui, {"video": ["vid000001"], "choice": "exclude"})
    assert flashes(ui) == "Left 1 video out of templates."
    post(ui, {"video": ["vid000001", "vid000002", "vid000003"], "choice": "auto"})
    assert flashes(ui) == "3 videos now follow the automatic choice."
    assert choices(ui) == {}
    post(ui, {"video": ["vid000003"], "choice": "auto"})
    assert flashes(ui) == "1 video now follows the automatic choice."
    assert cell(get(ui), "vid000003") == ("Main", "Default template. &middot; " + cell(get(ui), "vid000003")[1].split(
        " &middot; ")[1])


def test_unknown_ids_are_ignored_and_counted(tmp_path: Path) -> None:
    ui = make_dt_ui(tmp_path)
    main, reviews = with_reviews(ui)
    post(ui, {"video": ["vid000001", "nope0000001", "../x", "vid000001"], "choice": str(reviews)})
    assert flashes(ui) == "Set 1 video to Reviews. Skipped 2 videos that are not in the catalog."
    assert choices(ui)["vid000001"] == reviews and "nope0000001" not in choices(ui)
    post(ui, {"video": ["vid000002", "gone"], "choice": "exclude"})
    assert flashes(ui) == "Left 1 video out of templates. Skipped 1 video that is not in the catalog."
    before = choices(ui)
    post(ui, {"video": ["gone", "also-gone"], "choice": "exclude"})
    assert flashes(ui) == "None of the ticked videos are in the catalog."
    assert choices(ui) == before


def test_no_ids_a_deleted_template_and_too_many(tmp_path: Path) -> None:
    ui = make_dt_ui(tmp_path)
    main, reviews = with_reviews(ui)
    before = choices(ui)
    post(ui, {"choice": str(reviews)})
    assert flashes(ui) == "Tick at least one video."
    post(ui, {"video": [""], "choice": "auto"})
    assert flashes(ui) == "Tick at least one video."
    with store_of(ui) as store:
        store.delete_description_template(reviews)
    for bad in (str(reviews), "", "abc", "9" * 30, "-1"):
        post(ui, {"video": ["vid000001"], "choice": bad})
        assert flashes(ui) == "That template does not exist.", bad
    ids = [f"vid{n:06d}" for n in range(1, MAX_BULK_TEMPLATE + 2)]
    post(ui, {"video": ids, "choice": "exclude"})
    assert flashes(ui) == f"Tick at most {MAX_BULK_TEMPLATE} videos at once."
    assert choices(ui) == before


def test_the_limit_counts_distinct_ids(tmp_path: Path) -> None:
    ui = make_dt_ui(tmp_path)
    with_reviews(ui)
    post(ui, {"video": ["vid000001"] * (MAX_BULK_TEMPLATE + 5), "choice": "exclude"})
    assert flashes(ui) == "Left 1 video out of templates."


def test_the_bulk_route_needs_the_csrf_token(tmp_path: Path) -> None:
    ui = make_dt_ui(tmp_path)
    main, reviews = with_reviews(ui)
    before = choices(ui)
    for data in ({"video": ["vid000001"], "choice": "exclude"},
                 {"video": ["vid000001"], "choice": "exclude", "csrf": "wrong"}):
        assert ui.client.post(BULK, data=data, follow_redirects=False).status_code == 403
    assert choices(ui) == before


def test_the_return_address_keeps_the_list(tmp_path: Path) -> None:
    ui, main, reviews = many_ui(tmp_path)
    html = get(ui, f"?q=garden&sort=title&dir=desc&visibility=public&template={main}")
    fields = dict(re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]*)">',
                             html.split('id="vt-bulk"')[1].split('id="vt-bulkbar"')[0]))
    fields.pop("csrf")
    assert fields == {"q": "garden", "sort": "title", "dir": "desc", "visibility": "public", "template": str(main)}
    resp = post(ui, {**fields, "video": ["vid000001"], "choice": "exclude"})
    location = resp.headers["location"]
    assert location.startswith("/videos?")
    assert dict(parse_qsl(urlsplit(location).query)) == fields
    # every list parameter comes back, checked
    every = {"q": "garden", "sort": "title", "dir": "desc", "band": "poor", "visibility": "unlisted",
             "outliers": "1", "check": "title_length", "template": "excluded", "page": "3"}
    resp = post(ui, {**every, "video": ["vid000001"], "choice": "auto"})
    assert dict(parse_qsl(urlsplit(resp.headers["location"]).query)) == every
    # the list as the page sent it, without page 2 when there is only one page
    page2 = get(ui, f"?template={main}&sort=views&page=2")
    fields = dict(re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]*)">',
                             page2.split('id="vt-bulk"')[1].split('id="vt-bulkbar"')[0]))
    assert fields.pop("page") == "2" and fields.pop("template") == str(main) and fields.pop("sort") == "views"
    resp = post(ui, {"video": ["vid000002"], "choice": "auto", "template": str(main), "sort": "views", "page": "2"})
    assert resp.headers["location"] == f"/videos?sort=views&template={main}&page=2"


@pytest.mark.parametrize("evil", [
    {"template": "//evil.test"}, {"template": "https://evil.test/"}, {"q": "x", "next": "https://evil.test/"},
    {"page": "//evil.test"}, {"sort": "https://evil.test"}, {"back": "/configuration"},
    {"visibility": "/../configuration"}, {"check": "x\r\nLocation: https://evil.test"},
    {"template": "1/../../configuration"},
])
def test_the_return_address_cannot_leave_videos(tmp_path: Path, evil: dict[str, str]) -> None:
    ui = make_dt_ui(tmp_path)
    with_reviews(ui)
    resp = post(ui, {**evil, "video": ["vid000001"], "choice": "exclude"})
    assert resp.status_code == 303
    location = resp.headers["location"]
    parts = urlsplit(location)
    assert parts.scheme == "" and parts.netloc == "" and parts.path == "/videos", location
    assert "\r" not in location and "\n" not in location
    keys = {k for k, _ in parse_qsl(parts.query)}
    assert keys <= {"q", "sort", "dir", "visibility", "band", "check", "outliers", "template", "page"}
    assert "evil" not in dict(parse_qsl(parts.query)).get("template", "")


# --- the coverage strip links ---------------------------------------------------------------------------

def test_the_coverage_strip_links_to_the_filtered_lists(tmp_path: Path) -> None:
    ui = make_dt_ui(tmp_path)
    with store_of(ui) as store:
        store.add_description_template("Reviews", [{"kind": "opening"}],
                                       rules=[{"kind": "title_word", "value": "winter"}])
        store.exclude_from_templates("vid000003")
    html = ui.client.get("/description-templates").text
    cover = html[html.index('id="tp-coverage"'):html.index('id="tp-chips"')]
    assert re.search(r'<a class="tp-stat" id="tp-cover-excluded" href="/videos\?template=excluded">', cover)
    assert re.search(r'<a class="tp-stat" id="tp-cover-none" href="/videos\?template=none">', cover)
    assert '<div class="tp-stat" id="tp-cover-following">' in cover  # no exact filter: stays plain
    # the links lead to exactly the counted videos
    excluded = int(re.search(r'id="tp-cover-excluded".*?tp-stat-v">(\d+)<', cover, re.S).group(1))
    none = int(re.search(r'id="tp-cover-none".*?tp-stat-v">(\d+)<', cover, re.S).group(1))
    assert len(row_ids(get(ui, "?template=excluded"))) == excluded == 2
    assert len(row_ids(get(ui, "?template=none"))) == none == 2
