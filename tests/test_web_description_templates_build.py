"""Build from my channel (/description-templates/build): the page with and without candidates, an empty catalog,
adding a candidate with the server's own text, older versions, a stale key, a refused name, CSRF, escaping and the
two links on the main page. Made-up gardening videos only; nothing here calls YouTube or a model."""

from __future__ import annotations

import html as html_mod
from pathlib import Path
from typing import Any

from webhelpers import Ui, make_ui, make_video, seed_catalog
from yezatas import descriptiontemplate, templatebuild
from yezatas.store import Store

URL = "/description-templates/build"
PAID = "Some links below are affiliate links; I earn a small commission."
SUPPORT_NEW = "Support the garden:\nhttps://support.invalid/garden-new"
SUPPORT_OLD = "Support the garden:\nhttps://support.invalid/garden-old"
SUPPORT_OLDEST = "Support the garden:\nhttps://support.invalid/garden-oldest"


def own(n: int) -> str:
    return f"How I grow raised bed number {n} this spring."


def garden_videos() -> list[Any]:
    """13 made-up videos: the disclosure on all, the support block in three versions (6, 4 and 3 videos)."""
    videos = []
    for n in range(1, 14):
        support = SUPPORT_NEW if n <= 6 else SUPPORT_OLD if n <= 10 else SUPPORT_OLDEST
        videos.append(make_video(n, title=f"Raised bed {n}", description=f"{own(n)}\n\n{PAID}\n{own(n + 50)}\n\n{support}",
                                 view_count=n * 100, duration="PT6M", privacy_status="public"))
    return videos


def make_build_ui(tmp_path: Path, videos: list[Any] | None = None) -> Ui:
    ui = make_ui(tmp_path)
    if videos is None:
        videos = garden_videos()
    if videos:
        seed_catalog(ui.config, videos)
    return ui


def get(ui: Ui, query: str = "") -> str:
    resp = ui.client.get(URL + query)
    assert resp.status_code == 200
    return html_mod.unescape(resp.text)


def blocks_of(ui: Ui) -> list[Any]:
    with Store(ui.config.catalog_db) as store:
        return store.list_template_blocks()


def key_of(text: str) -> str:
    return templatebuild.make_key(text)


def add(ui: Ui, key: str, **data: str) -> Any:
    return ui.post(URL + "/add", data={"key": key, **data}, follow_redirects=False)


# --- the page ----------------------------------------------------------------------------------------------

def test_the_page_lists_the_candidates(tmp_path: Path) -> None:
    ui = make_build_ui(tmp_path)
    page = get(ui)
    assert "Build from my channel" in page
    assert "Read 13 videos." in page
    paid = page[page.index(f'id="build-{key_of(PAID)}"'):]
    paid = paid[:paid.index("</section>")]
    assert PAID in paid and "On 13 of 13 videos" in paid
    assert 'value="Paid links"' in paid
    assert 'name="link_slot" value="1" checked' in paid
    assert "Add as shared block" in paid
    assert 'href="/videos/vid000013"' in paid  # most viewed first
    support = page[page.index(f'id="build-{key_of(SUPPORT_NEW)}"'):]
    support = support[:support.index("</section>")]
    assert "On 6 of 13 videos" in support and "Older versions" in support
    assert support.index(SUPPORT_OLD) < support.index(SUPPORT_OLDEST)
    assert "On 4 videos" in support and "On 3 videos" in support
    assert 'name="remember" value="1" checked' in support
    assert "Remember the older versions, so videos that carry them show as out of date" in support
    assert "The links right below this are each video's own" in support
    assert 'name="link_slot" value="1" checked' not in support


def test_the_nav_entry_is_active(tmp_path: Path) -> None:
    ui = make_build_ui(tmp_path)
    page = ui.client.get(URL).text
    assert '<a class="nav-item active" href="/description-templates" aria-current="page">' in page


def test_the_page_without_candidates(tmp_path: Path) -> None:
    ui = make_build_ui(tmp_path, [make_video(n, description=own(n), duration="PT6M") for n in range(1, 5)])
    page = get(ui)
    assert 'id="build-none"' in page
    assert "No text repeats on enough of your videos yet. Read 4 videos." in page
    assert "Add as shared block" not in page


def test_the_page_with_an_empty_catalog(tmp_path: Path) -> None:
    ui = make_build_ui(tmp_path, [])
    page = get(ui)
    assert 'id="build-no-videos"' in page
    assert "Pull your channel first." in page
    assert 'href="/configuration#step-pull"' in page


def test_more_than_the_cap_says_so(tmp_path: Path) -> None:
    tips = [f"Garden tip number {t} of the season" for t in range(templatebuild.MAX_CANDIDATES + 2)]
    videos = [make_video(n, duration="PT6M", description="\n".join(f"{tip}\n{own(n * 1000 + j)}"
                                                                  for j, tip in enumerate(tips)))
              for n in range(1, 4)]
    page = get(make_build_ui(tmp_path, videos))
    assert f"Showing the {templatebuild.MAX_CANDIDATES} found on the most videos, of " \
           f"{templatebuild.MAX_CANDIDATES + 2}." in page
    assert page.count("Add as shared block") == templatebuild.MAX_CANDIDATES


def test_description_text_is_escaped(tmp_path: Path) -> None:
    evil = '<script>alert("garden")</script>'
    videos = [make_video(n, title=f"Bed <b>{n}</b>", description=f"{own(n)}\n{evil}", duration="PT6M")
              for n in range(1, 4)]
    raw = make_build_ui(tmp_path, videos).client.get(URL).text
    assert "<script>alert" not in raw and "<b>1</b>" not in raw
    assert "&lt;script&gt;alert(" in raw


# --- adding a candidate -----------------------------------------------------------------------------------------

def test_adding_uses_the_servers_text_not_the_forms(tmp_path: Path) -> None:
    ui = make_build_ui(tmp_path)
    resp = add(ui, key_of(PAID), name="Paid links", link_slot="1", text="Forged text from the form")
    assert resp.status_code == 303
    (block,) = blocks_of(ui)
    assert block.name == "Paid links" and block.text == PAID and block.link_slot
    assert block.old_versions == ()
    assert resp.headers["location"] == f"{URL}?added={block.id}#build-{key_of(PAID)}"
    page = get(ui, f"?added={block.id}")
    assert "Added Paid links as a shared block." in page
    assert 'href="/description-templates#blocks"' in page
    card = page[page.index(f'id="build-{key_of(PAID)}"'):]
    card = card[:card.index("</section>")]
    assert "Added as Paid links" in card and "Add as shared block" not in card


def test_older_versions_are_remembered_newest_first(tmp_path: Path) -> None:
    ui = make_build_ui(tmp_path)
    add(ui, key_of(SUPPORT_NEW), name="Support and follow", remember="1")
    (block,) = blocks_of(ui)
    assert block.text == SUPPORT_NEW
    assert block.old_versions == (SUPPORT_OLD, SUPPORT_OLDEST)
    assert not block.link_slot


def test_older_versions_can_be_left_out(tmp_path: Path) -> None:
    ui = make_build_ui(tmp_path)
    add(ui, key_of(SUPPORT_NEW), name="Support and follow")
    (block,) = blocks_of(ui)
    assert block.text == SUPPORT_NEW and block.old_versions == ()


def test_a_stale_key(tmp_path: Path) -> None:
    ui = make_build_ui(tmp_path)
    resp = add(ui, "0123456789ab", name="Paid links")
    assert resp.status_code == 303 and resp.headers["location"] == URL
    assert blocks_of(ui) == []
    assert "That text is no longer repeated on your videos." in get(ui)


def test_an_older_versions_key_is_stale(tmp_path: Path) -> None:
    ui = make_build_ui(tmp_path)
    add(ui, key_of(SUPPORT_OLD), name="Old support")
    assert blocks_of(ui) == []
    assert "That text is no longer repeated on your videos." in get(ui)


def test_a_refused_name_comes_back_with_the_form_kept(tmp_path: Path) -> None:
    ui = make_build_ui(tmp_path)
    long_name = "Raised bed links " * 7
    resp = add(ui, key_of(SUPPORT_NEW), name=long_name, link_slot="1")
    assert resp.status_code == 303
    assert blocks_of(ui) == []
    page = get(ui)
    assert "The block name can be at most 100 characters." in page
    card = page[page.index(f'id="build-{key_of(SUPPORT_NEW)}"'):]
    card = card[:card.index("</section>")]
    assert f'value="{long_name}"' in card
    assert 'name="link_slot" value="1" checked' in card
    assert 'name="remember" value="1" checked' not in card  # it was unticked in the refused form
    # shown once, like a flash
    assert f'value="{long_name}"' not in get(ui)


def test_an_empty_name_is_refused(tmp_path: Path) -> None:
    ui = make_build_ui(tmp_path)
    add(ui, key_of(PAID), name="   ")
    assert blocks_of(ui) == []
    assert "Give the block a name." in get(ui)


def test_adding_the_same_text_again_is_refused(tmp_path: Path) -> None:
    ui = make_build_ui(tmp_path)
    add(ui, key_of(PAID), name="Paid links")
    add(ui, key_of(PAID), name="Paid links again")
    assert [b.name for b in blocks_of(ui)] == ["Paid links"]
    assert "That text is already the shared block Paid links." in get(ui)


def test_every_post_needs_the_csrf_token(tmp_path: Path) -> None:
    ui = make_build_ui(tmp_path)
    data = {"key": key_of(PAID), "name": "Paid links"}
    assert ui.client.post(URL + "/add", data=data, follow_redirects=False).status_code == 403
    assert ui.client.post(URL + "/add", data={**data, "csrf": "wrong"}, follow_redirects=False).status_code == 403
    assert blocks_of(ui) == []


# --- the main page ------------------------------------------------------------------------------------------------

def test_the_two_links_on_the_main_page(tmp_path: Path) -> None:
    ui = make_build_ui(tmp_path)
    empty = ui.client.get("/description-templates").text
    assert 'id="tp-empty"' in empty
    link = 'href="/description-templates/build">Build from my channel</a>'
    start = empty.index('id="tp-empty"')
    assert link in empty[start:empty.index("</section>", start)]
    blocks = empty[empty.index('id="blocks"'):]
    assert link in blocks[:blocks.index("</section>")]
    ui.post("/description-templates/start")
    full = ui.client.get("/description-templates").text
    assert 'id="tp-empty"' not in full
    blocks = full[full.index('id="blocks"'):]
    assert link in blocks[:blocks.index("</section>")]


# --- filling a starter placeholder ---------------------------------------------------------------------------------

def start_main(ui: Ui) -> dict[str, Any]:
    """The suggested template "Main", whose two shared blocks still hold their placeholder text."""
    ui.post("/description-templates/start")
    return {b.name: b for b in blocks_of(ui)}


def test_the_card_says_it_will_fill_the_placeholder(tmp_path: Path) -> None:
    ui = make_build_ui(tmp_path)
    start_main(ui)
    page = get(ui)
    paid = page[page.index(f'id="build-{key_of(PAID)}"'):]
    paid = paid[:paid.index("</section>")]
    assert 'value="Paid links"' in paid  # not "Paid links 2"
    assert "Added under this name, it fills your shared block Paid links, which still has its placeholder text." \
        in paid


def test_adding_fills_the_placeholder_block(tmp_path: Path) -> None:
    ui = make_build_ui(tmp_path)
    before = start_main(ui)
    paid_id = before["Paid links"].id
    resp = add(ui, key_of(PAID), name="  paid   LINKS ")
    assert resp.status_code == 303
    assert resp.headers["location"] == f"{URL}?added={paid_id}&filled=1#build-{key_of(PAID)}"
    after = {b.id: b for b in blocks_of(ui)}
    assert sorted(after) == sorted(b.id for b in before.values())  # no new block
    filled = after[paid_id]
    assert filled.name == "Paid links" and filled.text == PAID
    assert not filled.link_slot  # from the form: the box was unticked
    assert "Filled your shared block Paid links with this text." in get(ui, f"?added={paid_id}&filled=1")
    with Store(ui.config.catalog_db) as store:
        (main,) = store.list_description_templates()
    assert {"kind": "block", "block_id": paid_id} in [dict(s) for s in main.sections]


def test_filling_remembers_older_versions_and_hides_the_placeholder(tmp_path: Path) -> None:
    ui = make_build_ui(tmp_path)
    support_id = start_main(ui)["Support and follow"].id
    add(ui, key_of(SUPPORT_NEW), name="Support and follow", remember="1", link_slot="1")
    block = next(b for b in blocks_of(ui) if b.id == support_id)
    assert block.text == SUPPORT_NEW and block.link_slot
    assert block.old_versions[:2] == (SUPPORT_OLD, SUPPORT_OLDEST)
    # the main page counts only the real old versions
    main = html_mod.unescape(ui.client.get("/description-templates").text)
    assert f'<span id="block-{support_id}-old">2 old versions</span>' in main


def test_a_filled_block_is_found_on_the_videos(tmp_path: Path) -> None:
    ui = make_build_ui(tmp_path)
    paid_id = start_main(ui)["Paid links"].id
    add(ui, key_of(PAID), name="Paid links")
    with Store(ui.config.catalog_db) as store:
        judgments, _ = descriptiontemplate.judge_catalog(store, store.list_videos())
        (main,) = store.list_description_templates()
        blocks = store.list_template_blocks()
        report = descriptiontemplate.parse_description(store.get_video("vid000001").description, main, blocks,
                                                       store.get_video("vid000001"))
    states = {s.block_id: s.state for s in report.sections if s.kind == "block"}
    assert states[paid_id] == descriptiontemplate.OK
    assert judgments["vid000001"] is not None


def test_a_different_name_adds_a_new_block_and_leaves_the_placeholder(tmp_path: Path) -> None:
    ui = make_build_ui(tmp_path)
    before = start_main(ui)
    add(ui, key_of(PAID), name="Disclosure")
    after = {b.name: b for b in blocks_of(ui)}
    assert after["Paid links"].text == before["Paid links"].text
    assert after["Disclosure"].text == PAID


# --- an older version that is already a block ------------------------------------------------------------------------

def test_an_older_version_already_in_the_library_is_information_only(tmp_path: Path) -> None:
    ui = make_build_ui(tmp_path)
    with Store(ui.config.catalog_db) as store:
        store.add_template_block("Garden support", SUPPORT_OLD)
    page = get(ui)
    card = page[page.index(f'id="build-{key_of(SUPPORT_NEW)}"'):]
    card = card[:card.index("</section>")]
    assert ("An older version of this text is your shared block Garden support. Most of these videos carry this "
            "newer version. To use it, change the block's text under") in card
    assert "Add as shared block" not in card and "Added as" not in card
    add(ui, key_of(SUPPORT_NEW), name="Support again")
    assert [b.name for b in blocks_of(ui)] == ["Garden support"]
    assert "An older version of that text is already the shared block Garden support." in get(ui)


# --- blank lines inside a block --------------------------------------------------------------------------------------

def test_blank_lines_are_stored_and_the_block_is_still_found(tmp_path: Path) -> None:
    seeds = "Seeds I use this year:\n\nGreen Row heirloom tomatoes"
    videos = [make_video(n, title=f"Bed {n}", description=f"{own(n)}\n\n{seeds}", duration="PT6M",
                         privacy_status="public") for n in range(1, 5)]
    videos.append(make_video(5, title="Bed 5", duration="PT6M", privacy_status="public",
                             description=f"{own(5)}\n\nSeeds I use this year:\nGreen Row heirloom tomatoes"))
    ui = make_build_ui(tmp_path, videos)
    key = key_of("Seeds I use this year:\nGreen Row heirloom tomatoes")
    assert "Seeds I use this year:\n\nGreen Row heirloom tomatoes</pre>" in get(ui)
    add(ui, key, name="Seeds")
    (block,) = blocks_of(ui)
    assert block.text == seeds
    with Store(ui.config.catalog_db) as store:
        tid = store.add_description_template("Beds", [{"kind": "opening"}, {"kind": "block", "block_id": block.id}],
                                             is_default=True)
        template = store.get_description_template(tid)
        for n in (1, 5):  # with and without the blank line
            v = store.get_video(f"vid{n:06d}")
            report = descriptiontemplate.parse_description(v.description, template, [block], v)
            assert [s.state for s in report.sections if s.kind == "block"] == [descriptiontemplate.OK]
    preview = html_mod.unescape(ui.client.get(f"/description-templates?template={tid}&video=vid000001").text)
    assert '<div class="tp-line block">Seeds I use this year:</div>' in preview
