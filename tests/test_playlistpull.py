"""The playlist pull (read only): pages of playlists and items, the quota charged before every call, the estimate,
all-or-nothing storing, refused playlists, the uploads playlist and ids not in the catalog. Only the fake reader is
used; no real YouTube client can be built here. Made-up gardening playlists only."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from fakes import UPLOADS_ID, FakeYouTubeReader, make_http_error
from yezatas.catalog import NoChannelError
from yezatas.playlistpull import pages_for, pull_playlists
from yezatas.quota import QuotaExceeded, QuotaGuard, quota_day
from yezatas.store import Store

NOW = datetime(2026, 11, 3, 18, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def no_real_client(monkeypatch: pytest.MonkeyPatch) -> None:
    import googleapiclient.discovery

    def refuse(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("no real YouTube client may be built in these tests")

    monkeypatch.setattr(googleapiclient.discovery, "build", refuse)


@pytest.fixture
def store(tmp_path: Path):
    with Store(tmp_path / "c.sqlite") as s:
        yield s


def guard(store: Store, budget: int = 10_000) -> QuotaGuard:
    return QuotaGuard(store, budget, lambda: NOW)


def playlist(n: int, count: int, title: str | None = None, **extra: Any) -> dict[str, Any]:
    return {"id": f"PLgarden{n:02d}", "title": title or f"Garden list {n}",
            "video_ids": [f"vid{n * 1000 + i:06d}" for i in range(count)], **extra}


def reader(*playlists: dict[str, Any], page_size: int = 50) -> FakeYouTubeReader:
    fake = FakeYouTubeReader(0, page_size=page_size)
    fake.playlists = list(playlists)
    return fake


def ledger(store: Store) -> list[tuple[str, int, str]]:
    return [(r["operation"], r["units"], r["note"]) for r in store.quota_entries(quota_day(NOW))]


def stored(store: Store) -> dict[str, tuple[str, tuple[str, ...]]]:
    return {p.playlist_id: (p.title, p.video_ids) for p in store.playlists()}


def test_pages_for() -> None:
    assert [pages_for(n) for n in (0, 1, 50, 51, 100, 101, None, "7", "x")] == [1, 1, 1, 2, 2, 3, 1, 1, 1]


def test_one_page_of_playlists(store: Store) -> None:
    fake = reader(playlist(1, 3, "Soil and compost"), playlist(2, 2, "Tomatoes"))
    result = pull_playlists(fake, store, guard(store))
    assert (result.playlists, result.items, result.units_used, result.skipped) == (2, 5, 4, ())
    assert stored(store) == {
        "PLgarden01": ("Soil and compost", ("vid001000", "vid001001", "vid001002")),
        "PLgarden02": ("Tomatoes", ("vid002000", "vid002001")),
    }
    # each call charged before it is made, with its own note
    assert ledger(store) == [("channels.list", 1, "playlist pull"), ("playlists.list", 1, "playlist pull"),
                             ("playlistItems.list", 1, "playlist pull"), ("playlistItems.list", 1, "playlist pull")]
    assert [c[0] for c in fake.calls] == ["channels_mine", "playlists_mine", "playlist_items", "playlist_items"]


def test_several_pages_of_playlists_and_of_items(store: Store) -> None:
    fake = reader(playlist(1, 5), playlist(2, 1), playlist(3, 2), page_size=2)
    result = pull_playlists(fake, store, guard(store))
    # 1 channel + 2 playlist pages + 3 + 1 + 1 item pages (two to a page)
    assert result.units_used == 1 + 2 + 3 + 1 + 1
    assert sum(units for _, units, _ in ledger(store)) == result.units_used
    assert len(stored(store)["PLgarden01"][1]) == 5
    assert ("playlists_mine", "2") in fake.calls and ("playlist_items", ("PLgarden01", "4")) in fake.calls


def test_the_charge_comes_before_each_call(store: Store) -> None:
    fake = reader(playlist(1, 2))
    seen: list[int] = []
    inner = fake.playlist_items

    def items(playlist_id: str, page_token: str | None) -> dict[str, Any]:
        seen.append(len(ledger(store)))
        return inner(playlist_id, page_token)

    fake.playlist_items = items  # type: ignore[method-assign]
    pull_playlists(fake, store, guard(store))
    assert seen == [3]  # channels.list, playlists.list and this playlistItems.list were already charged


def test_the_estimate_refuses_when_the_budget_is_short(store: Store) -> None:
    store.replace_playlists({"PLold": ("Old list", ["vid000001"])})
    fake = reader(playlist(1, 120), playlist(2, 10))  # 3 + 1 item pages
    with pytest.raises(QuotaExceeded, match=r"need 4 units for 4 playlistItems.list pages of 2 playlists but only 3"):
        pull_playlists(fake, store, guard(store, budget=5))  # 2 spent on channels and playlists
    assert stored(store) == {"PLold": ("Old list", ("vid000001",))}
    assert not any(c[0] == "playlist_items" for c in fake.calls)
    assert [op for op, _, _ in ledger(store)] == ["channels.list", "playlists.list"]


def test_the_estimate_uses_the_item_count_youtube_reports(store: Store) -> None:
    fake = reader(playlist(1, 2, item_count=500))  # says 10 pages, has 1
    with pytest.raises(QuotaExceeded):
        pull_playlists(fake, store, guard(store, budget=11))
    assert pull_playlists(fake, store, guard(store, budget=100)).playlists == 1


def test_a_failure_part_way_keeps_the_old_playlists(store: Store) -> None:
    store.replace_playlists({"PLold": ("Old list", ["vid000001"])})
    fake = reader(playlist(1, 2), playlist(2, 2))
    fake.playlist_errors["PLgarden02"] = make_http_error(500, "backendError")
    with pytest.raises(Exception) as caught:
        pull_playlists(fake, store, guard(store))
    assert "500" in str(caught.value)
    assert stored(store) == {"PLold": ("Old list", ("vid000001",))}


def test_quota_running_out_part_way_keeps_the_old_playlists(store: Store) -> None:
    store.replace_playlists({"PLold": ("Old list", ["vid000001"])})
    fake = reader(playlist(1, 2), playlist(2, 2))
    fake.playlist_errors["PLgarden02"] = make_http_error(403, "quotaExceeded")  # 403 but not about the playlist
    with pytest.raises(Exception):
        pull_playlists(fake, store, guard(store))
    assert stored(store) == {"PLold": ("Old list", ("vid000001",))}


@pytest.mark.parametrize("status,reason", [(404, "playlistNotFound"), (403, "playlistItemsNotAccessible"),
                                           (403, "forbidden")])
def test_a_refused_playlist_is_skipped_and_counted(store: Store, status: int, reason: str) -> None:
    fake = reader(playlist(1, 2, "Soil"), playlist(2, 2, "Private seeds"), playlist(3, 1, "Pots"))
    fake.playlist_errors["PLgarden02"] = make_http_error(status, reason)
    result = pull_playlists(fake, store, guard(store))
    assert result.skipped == ("Private seeds",)
    assert (result.playlists, result.items) == (2, 3)
    assert set(stored(store)) == {"PLgarden01", "PLgarden03"}


def test_an_empty_channel_stores_no_playlists(store: Store) -> None:
    store.replace_playlists({"PLold": ("Old list", ["vid000001"])})
    result = pull_playlists(reader(), store, guard(store))
    assert (result.playlists, result.items, result.units_used) == (0, 0, 2)
    assert stored(store) == {}


def test_an_empty_playlist_is_stored_with_no_videos(store: Store) -> None:
    result = pull_playlists(reader(playlist(1, 0, "Coming soon")), store, guard(store))
    assert stored(store) == {"PLgarden01": ("Coming soon", ())}
    assert result.units_used == 3


def test_ids_not_in_the_catalog_are_stored(store: Store) -> None:
    fake = reader({"id": "PLmix", "title": "Friends' gardens", "video_ids": ["otherchan01", "otherchan02"]})
    pull_playlists(fake, store, guard(store))
    assert store.playlist_memberships() == {"otherchan01": frozenset({"PLmix"}),
                                            "otherchan02": frozenset({"PLmix"})}


def test_the_uploads_playlist_is_not_read(store: Store) -> None:
    fake = reader({"id": UPLOADS_ID, "title": "Uploads", "video_ids": ["vid000001"]}, playlist(1, 1))
    pull_playlists(fake, store, guard(store))
    assert set(stored(store)) == {"PLgarden01"}
    assert not any(c == ("playlist_items", (UPLOADS_ID, None)) for c in fake.calls)


def test_replacing_drops_playlists_that_are_gone(store: Store) -> None:
    pull_playlists(reader(playlist(1, 1), playlist(2, 1)), store, guard(store))
    pull_playlists(reader(playlist(2, 3)), store, guard(store))
    assert set(stored(store)) == {"PLgarden02"} and len(stored(store)["PLgarden02"][1]) == 3


def test_a_video_twice_in_a_playlist_is_stored_once(store: Store) -> None:
    fake = reader({"id": "PLtwice", "title": "Twice", "video_ids": ["vidA", "vidB", "vidA"]})
    pull_playlists(fake, store, guard(store))
    assert stored(store)["PLtwice"][1] == ("vidA", "vidB")


def test_no_channel(store: Store) -> None:
    fake = reader(playlist(1, 1))
    fake.has_channel = False
    with pytest.raises(NoChannelError):
        pull_playlists(fake, store, guard(store))
    assert stored(store) == {}
