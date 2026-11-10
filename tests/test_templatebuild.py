"""Build from my channel: finding repeated description text (pure code, made-up gardening data)."""

from __future__ import annotations

from yezatas import templatebuild as tb
from yezatas.descriptiontemplate import STARTER_PLACEHOLDERS
from yezatas.store import TemplateBlock, Video

PAID = "Some links below are affiliate links; I earn a small commission at no cost to you."
SUPPORT_NEW = "Support the garden:\nhttps://example.com/garden-new"
SUPPORT_OLD = "Support the garden:\nhttps://example.com/garden-old"
SEEDS = "Seeds I use:\nGreen Row heirloom tomatoes"


def video(n: int, description: str, views: int | None = None, duration: str = "PT6M") -> Video:
    return Video(video_id=f"vid{n:06d}", title=f"Garden video {n}", description=description,
                 duration=duration, view_count=n if views is None else views)


def own(n: int) -> str:
    return f"How I grow bed number {n} this year."


def block(id: int, name: str, text: str, old: tuple[str, ...] = ()) -> TemplateBlock:
    return TemplateBlock(id=id, name=name, text=text, old_versions=old, link_slot=False)


def texts(cands: list[tb.Candidate]) -> list[str]:
    return [c.text for c in cands]


# --- thresholds ----------------------------------------------------------------------------------------

def test_two_videos_are_not_enough() -> None:
    videos = [video(i, f"{own(i)}\n\n{PAID}") for i in range(2)]
    assert tb.find_candidates(videos, []) == []


def test_three_videos_are_enough_in_a_small_catalog() -> None:
    videos = [video(i, f"{own(i)}\n\n{PAID}") for i in range(3)] + [video(i, own(i)) for i in range(3, 10)]
    cands = tb.find_candidates(videos, [])
    assert texts(cands) == [PAID]
    assert cands[0].video_count == 3 and cands[0].share == 0.3


def test_three_of_a_hundred_is_under_five_percent() -> None:
    videos = [video(i, f"{own(i)}\n{PAID}") for i in range(3)] + [video(i, own(i)) for i in range(3, 100)]
    assert tb.find_candidates(videos, []) == []
    videos = [video(i, f"{own(i)}\n{PAID}") for i in range(5)] + [video(i, own(i)) for i in range(5, 100)]
    assert texts(tb.find_candidates(videos, [])) == [PAID]


def test_needed_videos() -> None:
    assert tb.needed_videos(10) == 3
    assert tb.needed_videos(60) == 3
    assert tb.needed_videos(61) == 4
    assert tb.needed_videos(100) == 5


# --- lines -----------------------------------------------------------------------------------------------

def test_a_line_counts_once_per_video() -> None:
    videos = [video(1, f"{PAID}\n{own(1)}\n{PAID}\n{own(2)}\n{PAID}")] + [video(i, own(i)) for i in range(2, 6)]
    assert tb.find_candidates(videos, []) == []


def test_whitespace_is_collapsed_but_case_is_kept() -> None:
    videos = [video(1, f"  Thanks   for watching!  \n{own(1)}"),
              video(2, f"Thanks for watching!\t\n{own(2)}"),
              video(3, f"Thanks for\twatching!\n{own(3)}"),
              video(4, f"thanks for watching!\n{own(4)}")]
    cands = tb.find_candidates(videos, [])
    assert texts(cands) == ["Thanks for watching!"]
    assert cands[0].video_count == 3


def test_timestamps_and_hashtag_lines_are_ignored() -> None:
    desc = "0:00 Intro\n1:30 Planting\n#garden #tomatoes\n" + PAID
    videos = [video(i, f"{own(i)}\n{desc}") for i in range(4)]
    assert texts(tb.find_candidates(videos, [])) == [PAID]


def test_a_timestamp_line_splits_two_runs() -> None:
    videos = [video(i, f"Seeds I use:\n0:{i:02d} Sowing\nGreen Row heirloom tomatoes") for i in range(4)]
    assert sorted(texts(tb.find_candidates(videos, []))) == ["Green Row heirloom tomatoes", "Seeds I use:"]


def test_shorts_are_skipped_unless_included() -> None:
    shorts = [video(i, f"{own(i)}\n{PAID}", duration="PT40S") for i in range(3)]
    longs = [video(i, own(i)) for i in range(3, 6)]
    assert tb.find_candidates(shorts + longs, []) == []
    assert tb.scan(shorts + longs, []).videos_read == 3
    cands = tb.find_candidates(shorts + longs, [], include_shorts=True)
    assert texts(cands) == [PAID]
    assert tb.scan(shorts + longs, [], include_shorts=True).videos_read == 6


# --- grouping ---------------------------------------------------------------------------------------------

def test_adjacent_lines_with_blank_lines_between_form_one_block() -> None:
    text = "Support the garden:\n\n\nhttps://example.com/garden\n\nThank you!"
    videos = [video(i, f"{own(i)}\n\n{text}") for i in range(4)]
    cands = tb.find_candidates(videos, [])
    assert texts(cands) == ["Support the garden:\nhttps://example.com/garden\nThank you!"]
    assert cands[0].line_count == 3


def test_two_separate_blocks_in_one_description() -> None:
    videos = [video(i, f"{PAID}\nhttps://shop.example/item-{i}\n\n{SEEDS}\n\n{own(i)}") for i in range(5)]
    assert texts(tb.find_candidates(videos, [])) == [SEEDS, PAID]


def test_a_block_usually_alone_is_not_merged_with_a_neighbour() -> None:
    # The disclosure is on all 20 videos; the seed list sits right below it on only 4.
    videos = [video(i, f"{own(i)}\n{PAID}" + (f"\n{SEEDS}" if i < 4 else "")) for i in range(20)]
    cands = tb.find_candidates(videos, [])
    assert texts(cands) == [PAID, SEEDS]
    assert [c.video_count for c in cands] == [20, 4]


def test_a_block_in_two_different_places_is_one_candidate() -> None:
    videos = ([video(i, f"{SEEDS}\n\n{own(i)}") for i in range(3)]
              + [video(i, f"{own(i)}\n\n{SEEDS}") for i in range(3, 6)])
    cands = tb.find_candidates(videos, [])
    assert texts(cands) == [SEEDS]
    assert cands[0].video_count == 6


# --- versions -----------------------------------------------------------------------------------------------

def test_versions_with_one_line_changed_are_offered_together() -> None:
    videos = ([video(i, f"{own(i)}\n\n{SUPPORT_NEW}") for i in range(7)]
              + [video(i, f"{own(i)}\n\n{SUPPORT_OLD}") for i in range(7, 10)])
    cands = tb.find_candidates(videos, [])
    assert len(cands) == 1
    assert cands[0].text == SUPPORT_NEW and cands[0].video_count == 7
    assert cands[0].versions == ((SUPPORT_OLD, 3),)


def test_versions_are_listed_most_carried_first() -> None:
    third = "Support the garden:\nhttps://example.com/garden-oldest"
    videos = ([video(i, SUPPORT_NEW) for i in range(10)] + [video(i, SUPPORT_OLD) for i in range(10, 15)]
              + [video(i, third) for i in range(15, 18)])
    (cand,) = tb.find_candidates(videos, [])
    assert cand.versions == ((SUPPORT_OLD, 5), (third, 3))


def test_one_line_texts_and_two_changed_lines_are_not_versions() -> None:
    other = "Support the orchard:\nhttps://example.com/orchard"
    videos = ([video(i, f"{own(i)}\nThanks for watching!") for i in range(4)]
              + [video(i, f"{own(i)}\nThanks for reading!") for i in range(4, 7)]
              + [video(i, SUPPORT_NEW) for i in range(7, 11)] + [video(i, other) for i in range(11, 14)])
    cands = tb.find_candidates(videos, [])
    assert all(c.versions == () for c in cands)
    assert len(cands) == 4


# --- already in the library ---------------------------------------------------------------------------------

def test_already_for_a_current_text_and_for_an_old_version() -> None:
    videos = [video(i, f"{own(i)}\n\n{PAID}\n{own(i + 50)}\n{SEEDS}") for i in range(4)]
    blocks = [block(1, "Paid links", "  " + PAID + "  "),
              block(2, "Seed list", "Seeds I buy:\nother", old=("Seeds I use:\n\nGreen Row  heirloom tomatoes",))]
    cands = {c.text: c for c in tb.find_candidates(videos, blocks)}
    assert cands[PAID].already == "Paid links"
    assert cands[SEEDS].already == "Seed list"


def test_placeholder_blocks_do_not_count_as_existing() -> None:
    placeholder = STARTER_PLACEHOLDERS["Support and follow"]
    videos = [video(i, f"{own(i)}\n{placeholder}") for i in range(4)]
    blocks = [block(1, "Support and follow", placeholder)]
    (cand,) = tb.find_candidates(videos, blocks)
    assert cand.already == ""


# --- order, keys and the cap ----------------------------------------------------------------------------------

def test_order_is_most_videos_then_longest_then_text() -> None:
    videos = ([video(i, f"{own(i)}\nBravo line\n{own(i + 100)}\nAlpha line\n{own(i + 200)}\nOne\nTwo")
               for i in range(5)] + [video(i, f"{own(i)}\nZulu line") for i in range(5, 11)])
    assert texts(tb.find_candidates(videos, [])) == ["Zulu line", "One\nTwo", "Alpha line", "Bravo line"]


def test_same_input_gives_the_same_output_and_stable_keys() -> None:
    videos = [video(i, f"{own(i)}\n{PAID}\n\n{SEEDS}") for i in range(6)]
    first = tb.find_candidates(videos, [])
    again = tb.find_candidates(list(reversed(videos)), [])
    assert first == again
    assert [c.key for c in first] == [tb.make_key(c.text) for c in first]
    assert len({c.key for c in first}) == len(first)
    assert all(len(c.key) == tb.KEY_LENGTH for c in first)


def test_examples_are_the_most_viewed_first() -> None:
    views = [5, 900, 50, 300, 700]
    videos = [video(i, f"{own(i)}\n{PAID}", views=v) for i, v in enumerate(views)]
    (cand,) = tb.find_candidates(videos, [])
    assert cand.examples == ("vid000001", "vid000004", "vid000003")


def test_the_cap() -> None:
    lines = [f"Garden tip number {n} of the season" for n in range(tb.MAX_CANDIDATES + 5)]
    # each tip on 3 videos, separated by each video's own line, so every tip is its own candidate
    videos = []
    for i in range(3):
        videos.append(video(i, "\n".join(f"{line}\n{own(i * 1000 + j)}" for j, line in enumerate(lines))))
    result = tb.scan(videos, [])
    assert result.found == tb.MAX_CANDIDATES + 5
    assert len(result.candidates) == tb.MAX_CANDIDATES
    assert result.videos_read == 3


# --- suggestions ---------------------------------------------------------------------------------------------

def test_suggested_names_and_link_slot() -> None:
    support = "Subscribe for more garden videos and follow me on Patreon"
    videos = [video(i, f"{own(i)}\n{PAID}\n{own(i + 50)}\n{support}\n{own(i + 99)}\n{SEEDS}\n"
                       f"{own(i + 300)}\nhttps://example.com/my-garden-shop") for i in range(4)]
    cands = {c.text: c for c in tb.find_candidates(videos, [])}
    assert cands[PAID].suggested_name == "Paid links" and cands[PAID].suggested_link_slot
    assert cands[support].suggested_name == "Support and follow" and not cands[support].suggested_link_slot
    numbered = [c for c in tb.find_candidates(videos, []) if c.suggested_name.startswith("Block ")]
    assert [c.suggested_name for c in numbered] == ["Block 1", "Block 2"]
    assert cands["https://example.com/my-garden-shop"].has_urls


def test_a_disclosure_with_its_own_link_gets_no_link_slot() -> None:
    text = "Sponsored by Green Row seeds: https://example.com/green-row"
    videos = [video(i, f"{own(i)}\n{text}") for i in range(3)]
    (cand,) = tb.find_candidates(videos, [])
    assert cand.suggested_name == "Paid links" and cand.has_urls and not cand.suggested_link_slot


def test_two_candidates_with_the_same_cue_get_different_names() -> None:
    other = "Paid link: I may earn a commission on garden tools."
    videos = [video(i, f"{own(i)}\n{PAID}\n{own(i + 50)}\n{other}") for i in range(3)]
    names = sorted(c.suggested_name for c in tb.find_candidates(videos, []))
    assert names == ["Paid links", "Paid links 2"]


def test_an_empty_catalog() -> None:
    assert tb.find_candidates([], []) == []
    result = tb.scan([], [])
    assert (result.candidates, result.found, result.videos_read) == ([], 0, 0)
    assert tb.find_candidates([video(1, ""), video(2, "\n\n"), video(3, "")], []) == []
