import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from yezatas.store import (MIGRATIONS, AdCheck, Comment, CommentReply, CustomRule, DraftRow,
                           OutlierReport, PackagingReview, PushRecord, ReplyTemplate,
                           Store, SubtitleTranslationRecord, ThumbnailVariant,
                           TranscriptRecord, TranslationRecord, Video)

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
# undo the newest migrations, for tests that turn a database back into an older version
BACK_TO_V17 = ("DROP TABLE template_blocks; DROP TABLE description_templates; DROP TABLE template_choices; "
                "DROP TABLE template_settings; DROP TABLE playlist_items; DROP TABLE playlists; ")
BACK_TO_V15 = BACK_TO_V17 + ("ALTER TABLE comment_replies DROP COLUMN posted_comment_id; "
                              "ALTER TABLE comment_replies DROP COLUMN posted_at; ")
BACK_TO_V14 = BACK_TO_V15 + "ALTER TABLE comments DROP COLUMN by_owner; DROP TABLE category_checks; DROP TABLE pinned_comments; "
BACK_TO_V13 = BACK_TO_V14 + "ALTER TABLE comments DROP COLUMN owner_replied; "
BACK_TO_V12 = BACK_TO_V13 + "DROP TABLE shorts_finds; "
BACK_TO_V11 = BACK_TO_V12 + "DROP TABLE comments; DROP TABLE comment_replies; DROP TABLE reply_templates; DROP TABLE custom_rules; "
BACK_TO_V10 = BACK_TO_V11 + "DROP TABLE link_checks; DROP TABLE ad_checks; "
BACK_TO_V9 = BACK_TO_V10 + "DROP TABLE copilot_proposals; DROP TABLE copilot_turns; "
BACK_TO_V8 = BACK_TO_V9 + ("DROP INDEX idx_pushes_run; ALTER TABLE pushes DROP COLUMN run_id; "
                           "DROP TABLE push_runs; ")


def make_video(vid: str = "vid00000001", **kw: object) -> Video:
    base: dict[str, object] = dict(
        video_id=vid,
        channel_id="chan0000001",
        title="How to repot a cactus",
        description="Step by step.\nSecond line.",
        tags=["cactus", "plants"],
        privacy_status="public",
        default_language="en",
        published_at="2026-01-01T00:00:00Z",
        duration="PT5M3S",
        view_count=100,
        like_count=10,
        comment_count=2,
        localizations={"lt": {"title": "Kaip persodinti kaktusą", "description": "x"}},
        thumbnails={"default": {"url": "https://example.invalid/t.jpg"}},
        raw={"kind": "youtube#video"},
    )
    base.update(kw)
    return Video(**base)  # type: ignore[arg-type]


@pytest.fixture
def store(tmp_path: Path):
    with Store(tmp_path / "sub" / "c.sqlite") as s:
        yield s


def test_creates_parent_dirs_and_version(tmp_path: Path) -> None:
    with Store(tmp_path / "a" / "b" / "c.sqlite") as s:
        assert s.schema_version == len(MIGRATIONS) == 18
        assert s._conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_migrations_idempotent_on_reopen(tmp_path: Path) -> None:
    path = tmp_path / "c.sqlite"
    with Store(path) as s:
        pid = s.begin_pull("chan0000001", now=T0)
        s.upsert_video(make_video(), pid, now=T0)
    with Store(path) as s:
        assert s.schema_version == 18
        assert s.get_video("vid00000001") is not None
        assert len(s.snapshots_for("vid00000001")) == 1


def test_upsert_writes_snapshot_per_pull(store: Store) -> None:
    p1 = store.begin_pull("chan0000001", now=T0)
    store.upsert_video(make_video(view_count=100), p1, now=T0)
    store.finish_pull(p1, 1, now=T0 + timedelta(seconds=5))
    later = T0 + timedelta(days=1)
    p2 = store.begin_pull("chan0000001", now=later)
    store.upsert_video(make_video(view_count=250, title="Repotting a cactus"), p2, now=later)
    cur = store.get_video("vid00000001")
    assert cur is not None and cur.view_count == 250 and cur.title == "Repotting a cactus"
    assert len(store.list_videos()) == 1
    snaps = store.snapshots_for("vid00000001")
    assert [(s[0], s[2].view_count) for s in snaps] == [(p1, 100), (p2, 250)]
    assert snaps[0][2].title == "How to repot a cactus"
    pull = store.get_pull(p1)
    assert pull is not None and pull["video_count"] == 1 and pull["finished_at"] is not None
    assert store._conn.execute("SELECT last_pull_id FROM videos").fetchone()[0] == p2


def test_upsert_is_atomic(store: Store) -> None:
    # Unknown pull id violates the FK: neither the video nor a snapshot may be written.
    with pytest.raises(sqlite3.IntegrityError):
        store.upsert_video(make_video(), 999, now=T0)
    assert store.get_video("vid00000001") is None
    assert store.snapshots_for("vid00000001") == []


def test_json_round_trip_unicode_and_emoji(store: Store) -> None:
    v = make_video(
        title="Kaktusų persodinimas 🌵 – 仙人掌",
        description="Ačiū! 😀\nLine two",
        tags=["kaktusas", "🌵", "日本語"],
        localizations={"ja": {"title": "サボテン", "description": "🌵"}},
    )
    pid = store.begin_pull("chan0000001", now=T0)
    store.upsert_video(v, pid, now=T0)
    assert store.get_video(v.video_id) == v
    assert store.snapshots_for(v.video_id)[0][2] == v
    assert Video.from_dict(v.to_dict()) == v


def test_get_missing_video(store: Store) -> None:
    assert store.get_video("nope") is None


def test_list_videos_ordering(store: Store) -> None:
    pid = store.begin_pull("chan0000001", now=T0)
    for i, views in enumerate([5, 50, 20]):
        store.upsert_video(make_video(f"vid0000000{i}", view_count=views), pid, now=T0)
    assert [v.view_count for v in store.list_videos()] == [50, 20, 5]
    assert [v.view_count for v in store.list_videos("view_count asc")] == [5, 20, 50]
    assert [v.video_id for v in store.list_videos("video_id")] == [
        "vid00000000", "vid00000001", "vid00000002"]


@pytest.mark.parametrize("bad", [
    "view_count; DROP TABLE videos", "nope", "view_count sideways", "",
    "view_count desc, title", "1", "view_count desc; --",
])
def test_order_by_whitelist(store: Store, bad: str) -> None:
    with pytest.raises(ValueError):
        store.list_videos(bad)
    store._conn.execute("SELECT 1 FROM videos")  # table still exists


def test_quota_ledger(store: Store) -> None:
    store.add_quota_entry("videos.list", 1, 3, "pull", quota_day="2026-10-01", now=T0)
    store.add_quota_entries([("videos.update", 50, 1, ""), ("thumbnails.set", 50, 1, "")],
                            quota_day="2026-10-01", now=T0)
    store.add_quota_entry("videos.list", 1, quota_day="2026-10-02", now=T0)
    assert store.quota_used("2026-10-01") == 101
    assert store.quota_used("2026-10-02") == 1
    assert store.quota_used("2026-10-03") == 0
    rows = store.quota_entries("2026-10-01")
    assert [r["operation"] for r in rows] == ["videos.list", "videos.update", "thumbnails.set"]
    assert rows[0]["count"] == 3 and rows[0]["note"] == "pull"
    assert rows[0]["ts_utc"] == "2026-10-01T12:00:00+00:00"


def test_naive_timestamp_rejected(store: Store) -> None:
    with pytest.raises(ValueError):
        store.begin_pull("chan0000001", now=datetime(2026, 10, 1))


def test_v1_database_migrates_to_the_latest_and_keeps_its_data(tmp_path: Path) -> None:
    path = tmp_path / "old.sqlite"
    with Store(path) as s:
        pid = s.begin_pull("chan0000001", now=T0)
        s.upsert_video(make_video(), pid, now=T0)
        s.add_quota_entry("videos.list", 1, quota_day="2026-10-01", now=T0)
    conn = sqlite3.connect(path)  # turn it back into a version 1 database
    conn.executescript("DROP TABLE drafts; DROP TABLE pushes; DROP TABLE agent_runs; "
                       "DROP TABLE translations; DROP TABLE transcripts; DROP TABLE subtitle_translations; "
                       "DROP TABLE packaging_reviews; DROP TABLE thumbnail_variants; DROP TABLE outlier_reports; "
                       "DROP TABLE push_runs; " + BACK_TO_V9 + "ALTER TABLE videos DROP COLUMN gone_at; PRAGMA user_version = 1;")
    conn.close()
    with Store(path) as s:
        assert s.schema_version == 18
        video = s.get_video("vid00000001")
        assert video is not None and video.title == "How to repot a cactus"
        assert s.quota_used("2026-10-01") == 1
        assert s.list_drafts() == [] and s.list_pushes() == [] and s.list_agent_runs() == []
        s.put_draft(make_draft())
        assert s.get_draft("vid00000001") is not None


def test_v3_drafts_get_the_catalog_language_as_their_base(tmp_path: Path) -> None:
    path = tmp_path / "v3.sqlite"
    loc = {"de": {"title": "Kaktus umtopfen", "description": "Schritt für Schritt."}}
    with Store(path) as s:
        pid = s.begin_pull("chan0000001", now=T0)
        s.upsert_video(make_video(default_language="en", localizations=loc), pid, now=T0)
        s.upsert_video(make_video("vid00000002", default_language=None, localizations={}), pid,
                       now=T0)
        s.put_draft(make_draft())
        s.put_draft(make_draft("vid00000002"))
    conn = sqlite3.connect(path)  # turn it back into a version 3 database
    conn.executescript(
        "DROP TABLE packaging_reviews; DROP TABLE thumbnail_variants; DROP TABLE outlier_reports; "
        "DROP TABLE subtitle_translations; DROP TABLE transcripts; DROP TABLE translations; "
        "ALTER TABLE drafts DROP COLUMN default_language; "
        "ALTER TABLE drafts DROP COLUMN localizations_json; "
        "ALTER TABLE drafts DROP COLUMN base_default_language; "
        "ALTER TABLE drafts DROP COLUMN base_localizations_json; " + BACK_TO_V8 + "ALTER TABLE videos DROP COLUMN gone_at; PRAGMA user_version = 3;")
    conn.close()
    with Store(path) as s:
        row = s.get_draft("vid00000001")
        assert row is not None and row.base_default_language == "en"
        assert row.base_localizations == loc
        assert row.default_language is None and row.localizations is None
        other = s.get_draft("vid00000002")
        assert other is not None and other.base_localizations == {}
        assert other.base_default_language is None


def make_draft(vid: str = "vid00000001", **kw: object) -> DraftRow:
    base: dict[str, object] = dict(
        video_id=vid, title="New title", description="New text\nline two", tags=("a", "b"),
        base_title="How to repot a cactus", base_description="Step by step.\nSecond line.",
        base_tags=("cactus", "plants"), source="manual", note="", created_at="t1",
        updated_at="t2", approved_at=None,
    )
    base.update(kw)
    return DraftRow(**base)  # type: ignore[arg-type]


def test_draft_round_trip_and_upsert(store: Store) -> None:
    pid = store.begin_pull("chan0000001", now=T0)
    store.upsert_video(make_video(), pid, now=T0)
    row = make_draft(title="Kaktusų 🌵", tags=("🌵", "kaktusas"))
    store.put_draft(row)
    assert store.get_draft("vid00000001") == row
    assert row.draft.title == "Kaktusų 🌵" and row.draft.tags == ("🌵", "kaktusas")
    assert row.base.description == "Step by step.\nSecond line."
    changed = make_draft(title="Other", note="n", approved_at="t3")
    store.put_draft(changed)
    assert store.list_drafts() == [changed]
    store.set_draft_approval("vid00000001", None)
    assert store.get_draft("vid00000001").approved_at is None  # type: ignore[union-attr]
    store.set_draft_approval("vid00000001", "t9")
    assert store.get_draft("vid00000001").approved_at == "t9"  # type: ignore[union-attr]
    assert store.delete_draft("vid00000001") is True
    assert store.delete_draft("vid00000001") is False
    assert store.get_draft("vid00000001") is None


def test_draft_needs_a_catalog_video(store: Store) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        store.put_draft(make_draft("unknown00001"))


def test_update_video_text_changes_columns_and_raw_snippet_without_snapshot(store: Store) -> None:
    pid = store.begin_pull("chan0000001", now=T0)
    store.upsert_video(make_video(raw={"snippet": {"title": "old", "categoryId": "22"}}), pid, now=T0)
    later = T0 + timedelta(hours=1)
    store.update_video_text("vid00000001", "T", "D", ["x", "y"], now=later)
    video = store.get_video("vid00000001")
    assert video is not None
    assert (video.title, video.description, video.tags) == ("T", "D", ["x", "y"])
    assert video.raw["snippet"] == {"title": "T", "description": "D", "tags": ["x", "y"],
                                    "categoryId": "22"}
    assert video.view_count == 100
    assert len(store.snapshots_for("vid00000001")) == 1
    row = store._conn.execute("SELECT updated_at FROM videos").fetchone()
    assert row[0] == later.isoformat()
    with pytest.raises(KeyError):
        store.update_video_text("nope", "T", "D", [])


def test_push_records_round_trip_newest_first(store: Store) -> None:
    first = store.begin_push("vid00000001", "manual", {"title": "old", "tags": ["a"]}, now=T0)
    record = store.get_push(first)
    assert isinstance(record, PushRecord)
    assert (record.status, record.finished_at, record.after, record.message) == (
        "pending", None, {}, "")
    assert record.before == {"title": "old", "tags": ["a"]} and record.started_at == T0.isoformat()
    store.finish_push(first, "pushed", {"title": "new"}, "ok", now=T0 + timedelta(seconds=2))
    second = store.begin_push("vid00000002", "restore", {"title": "x"}, now=T0)
    third = store.begin_push("vid00000001", "manual", {"title": "y"}, now=T0)
    done = store.get_push(first)
    assert done is not None and done.status == "pushed" and done.after == {"title": "new"}
    assert done.finished_at == (T0 + timedelta(seconds=2)).isoformat() and done.message == "ok"
    assert [r.id for r in store.list_pushes()] == [third, second, first]
    assert [r.id for r in store.list_pushes("vid00000001")] == [third, first]
    assert [r.id for r in store.list_pushes(limit=2)] == [third, second]
    assert store.get_push(999) is None


def test_push_runs_group_pushes_and_older_pushes_have_no_run(store: Store) -> None:
    old = store.begin_push("vid00000001", "manual", {"title": "old"}, now=T0)
    assert store.get_push(old).run_id is None and store.get_push_run(1) is None
    run = store.begin_push_run(now=T0)
    a = store.begin_push("vid00000001", "manual", {"title": "a"}, now=T0, run_id=run)
    b = store.begin_push("vid00000002", "manual", {"title": "b"}, T0, run)
    other = store.begin_push_run(now=T0)
    c = store.begin_push("vid00000003", "manual", {"title": "c"}, now=T0, run_id=other)
    got = store.get_push_run(run)
    assert got is not None and (got.started_at, got.finished_at, got.video_count) == (
        T0.isoformat(), None, 0)
    store.finish_push_run(run, 2, now=T0 + timedelta(seconds=5))
    got = store.get_push_run(run)
    assert got.video_count == 2 and got.finished_at == (T0 + timedelta(seconds=5)).isoformat()
    assert [r.id for r in store.list_pushes_of_run(run)] == [a, b]  # push order
    assert [r.id for r in store.list_pushes_of_run(other)] == [c]
    assert store.get_push(a).run_id == run and store.list_pushes_of_run(999) == []
    assert set(store.get_push_runs([run, other, run, 999])) == {run, other}


def test_later_pushed_finds_the_next_pushed_row_of_the_video(store: Store) -> None:
    first = store.begin_push("vid00000001", "manual", {"title": "a"}, now=T0)
    failed = store.begin_push("vid00000001", "manual", {"title": "b"}, now=T0)
    store.finish_push(failed, "failed", {}, "no", now=T0)
    assert store.later_pushed("vid00000001", first) is None
    third = store.begin_push("vid00000001", "manual", {"title": "c"}, now=T0)
    store.finish_push(third, "pushed", {}, "ok", now=T0)
    other = store.begin_push("vid00000002", "manual", {"title": "d"}, now=T0)
    store.finish_push(other, "pushed", {}, "ok", now=T0)
    later = store.later_pushed("vid00000001", first)
    assert later is not None and later.id == third
    assert store.later_pushed("vid00000001", third) is None


def test_v8_database_upgrades_and_keeps_its_pushes(tmp_path: Path) -> None:
    path = tmp_path / "v8.sqlite"
    with Store(path) as s:
        push = s.begin_push("vid00000001", "manual", {"title": "old"}, now=T0)
        s.finish_push(push, "pushed", {"title": "new"}, "ok", now=T0)
    conn = sqlite3.connect(path)  # turn it back into a version 8 database
    conn.executescript(BACK_TO_V8 + "ALTER TABLE videos DROP COLUMN gone_at; PRAGMA user_version = 8;")
    conn.close()
    with Store(path) as s:
        assert s.schema_version == 18
        record = s.get_push(push)
        assert record is not None and record.run_id is None and record.status == "pushed"
        assert record.before == {"title": "old"} and record.after == {"title": "new"}
        run = s.begin_push_run(now=T0)
        assert s.begin_push("vid00000001", "manual", {}, now=T0, run_id=run) > push


def test_v9_database_upgrades_and_keeps_its_push_runs(tmp_path: Path) -> None:
    path = tmp_path / "v9.sqlite"
    with Store(path) as s:
        run = s.begin_push_run(now=T0)
        push = s.begin_push("vid00000001", "manual", {"title": "old"}, now=T0, run_id=run)
    conn = sqlite3.connect(path)  # turn it back into a version 9 database
    conn.executescript(BACK_TO_V9 + "ALTER TABLE videos DROP COLUMN gone_at; PRAGMA user_version = 9;")
    conn.close()
    with Store(path) as s:
        assert s.schema_version == 18
        record = s.get_push(push)
        assert record is not None and record.run_id == run
        assert s.latest_copilot_chat_id() is None and s.next_copilot_chat_id() == 1


def test_copilot_turns_and_proposals_round_trip(store: Store) -> None:
    first = store.begin_copilot_turn(1, "What should I fix?", "muse-spark-1.3", now=T0)
    turn = store.get_copilot_turn(first)
    assert turn is not None and turn.status == "running" and turn.finished_at is None
    assert turn.answer == "" and turn.refs == {} and turn.notes == [] and turn.log == []
    store.finish_copilot_turn(first, "answered", "Fix V2 first.", {"V2": "vid00000002"},
                              ["A sentence was removed."], [{"type": "tool", "name": "x"}],
                              tokens=(900, 100, 50), now=T0)
    second = store.begin_copilot_turn(1, "And then?", "muse-spark-1.3", now=T0)
    other = store.begin_copilot_turn(2, "Other chat", "muse-spark-1.3", now=T0)
    turn = store.get_copilot_turn(first)
    assert turn is not None and turn.status == "answered" and turn.finished_at is not None
    assert turn.answer == "Fix V2 first." and turn.refs == {"V2": "vid00000002"}
    assert turn.notes == ["A sentence was removed."] and turn.log == [{"type": "tool", "name": "x"}]
    assert (turn.input_tokens, turn.cached_tokens, turn.output_tokens) == (900, 100, 50)
    assert [t.id for t in store.list_copilot_turns(1)] == [first, second]
    assert [t.id for t in store.list_copilot_turns(2)] == [other]
    assert store.latest_copilot_chat_id() == 2 and store.next_copilot_chat_id() == 3
    assert store.get_copilot_turn(999) is None

    p1 = store.add_copilot_proposal(first, "vid00000002", "New title", "New text", ["a", "b"],
                                    "Shorter title", now=T0)
    p2 = store.add_copilot_proposal(other, "vid00000003", "T", "D", [], "Why", now=T0)
    proposal = store.get_copilot_proposal(p1)
    assert proposal is not None and proposal.status == "open" and proposal.tags == ("a", "b")
    assert (proposal.turn_id, proposal.video_id, proposal.reason) == (first, "vid00000002",
                                                                     "Shorter title")
    store.set_copilot_proposal_status(p1, "drafted")
    assert store.get_copilot_proposal(p1).status == "drafted"  # type: ignore[union-attr]
    assert [p.id for p in store.list_copilot_proposals([first, other])] == [p1, p2]
    assert [p.id for p in store.list_copilot_proposals([second])] == []
    assert store.list_copilot_proposals([]) == []
    assert store.get_copilot_proposal(999) is None


def test_v10_database_upgrades_to_v11_and_keeps_its_copilot_turns(tmp_path: Path) -> None:
    path = tmp_path / "v10.sqlite"
    with Store(path) as s:
        turn = s.begin_copilot_turn(1, "What should I fix?", "muse-spark-1.3", now=T0)
    conn = sqlite3.connect(path)  # turn it back into a version 10 database
    conn.executescript(BACK_TO_V10 + "ALTER TABLE videos DROP COLUMN gone_at; PRAGMA user_version = 10;")
    conn.close()
    with Store(path) as s:
        assert s.schema_version == 18
        assert s.get_copilot_turn(turn) is not None
        assert s.latest_link_checks() == {} and s.latest_ad_check("vid00000001") is None


def test_link_checks_keep_the_newest_per_url(store: Store) -> None:
    store.save_link_check("https://a.invalid/x", "broken", 404, "", "Not found (404).", now=T0)
    newer = store.save_link_check("https://a.invalid/x", "ok", 200, now=T0)
    other = store.save_link_check("https://b.invalid/", "redirected", 301,
                                  "https://b.invalid/home", "Moved.", now=T0)
    checks = store.latest_link_checks()
    assert set(checks) == {"https://a.invalid/x", "https://b.invalid/"}
    assert checks["https://a.invalid/x"].id == newer and checks["https://a.invalid/x"].status == "ok"
    moved = checks["https://b.invalid/"]
    assert (moved.id, moved.http_status, moved.final_url, moved.message) == (
        other, 301, "https://b.invalid/home", "Moved.")
    assert moved.checked_at != ""


def test_ad_checks_round_trip_newest_first(store: Store) -> None:
    first = AdCheck(0, "vid00000001", "", "muse-spark-1.3", "done", "no_concerns", "Fine.", [], [],
                    "h1", False, "", 10, 0, 5)
    store.save_ad_check(first, now=T0)
    second = AdCheck(0, "vid00000001", "", "muse-spark-1.3", "done", "worth_a_look", "One word.",
                     [{"category": "language", "source": "title", "quote": "darn"}],
                     ["A finding was dropped."], "h2", True, "", 900, 100, 50)
    second_id = store.save_ad_check(second, now=T0)
    got = store.latest_ad_check("vid00000001")
    assert got is not None and got.id == second_id and got.created_at != ""
    assert (got.verdict, got.summary, got.source_hash, got.had_transcript) == (
        "worth_a_look", "One word.", "h2", True)
    assert got.findings == [{"category": "language", "source": "title", "quote": "darn"}]
    assert got.notes == ["A finding was dropped."]
    assert (got.input_tokens, got.cached_tokens, got.output_tokens) == (900, 100, 50)
    assert store.latest_ad_check("vid00000002") is None


def test_agent_runs_round_trip_newest_first(store: Store) -> None:
    first = store.begin_agent_run("vid00000001", "muse-spark-1.3", now=T0)
    second = store.begin_agent_run("vid00000002", "muse-spark-1.3", now=T0)
    run = store.get_agent_run(first)
    assert run is not None and run.status == "running" and run.finished_at is None
    assert run.log == [] and run.result == {} and run.input_tokens == 0
    store.finish_agent_run(first, "drafted", "Longer title", [{"tool": "get_scorecard"}],
                           {"title": "New"}, tokens=(1200, 300, 450), now=T0)
    run = store.get_agent_run(first)
    assert run is not None and run.status == "drafted" and run.summary == "Longer title"
    assert run.log == [{"tool": "get_scorecard"}] and run.result == {"title": "New"}
    assert (run.input_tokens, run.cached_tokens, run.output_tokens) == (1200, 300, 450)
    assert [r.id for r in store.list_agent_runs()] == [second, first]
    assert [r.id for r in store.list_agent_runs("vid00000001")] == [first]
    assert store.get_agent_run(999) is None


def test_draft_language_and_translations_round_trip(store: Store) -> None:
    store.upsert_video(make_video(), store.begin_pull("chan0000001", now=T0), now=T0)
    store.put_draft(make_draft())
    row = store.get_draft("vid00000001")
    assert row is not None and row.default_language is None and row.localizations is None
    assert row.base_default_language is None and row.base_localizations == {}
    loc = {"es": {"title": "Cactus", "description": "Hola"}}
    store.put_draft(replace(make_draft(), default_language="en", localizations=loc,
                            base_default_language="lt", base_localizations={"de": {"title": "x", "description": "y"}}))
    row = store.get_draft("vid00000001")
    assert row is not None and row.default_language == "en" and row.localizations == loc
    assert row.base_default_language == "lt" and row.base_localizations["de"]["title"] == "x"
    store.put_draft(replace(row, localizations={}))  # an empty map is not "leave alone"
    assert store.get_draft("vid00000001").localizations == {}


def test_update_video_text_sets_language_and_translations_only_when_given(store: Store) -> None:
    pid = store.begin_pull("chan0000001", now=T0)
    store.upsert_video(make_video(default_language="lt", localizations={"de": {"title": "a"}}), pid, now=T0)
    store.update_video_text("vid00000001", "T", "D", ["x"], T0)
    video = store.get_video("vid00000001")
    assert video.default_language == "lt" and video.localizations == {"de": {"title": "a"}}
    loc = {"es": {"title": "T es", "description": "D es"}}
    store.update_video_text("vid00000001", "T", "D", ["x"], T0, default_language="en", localizations=loc)
    video = store.get_video("vid00000001")
    assert video.default_language == "en" and video.localizations == loc
    assert video.raw["localizations"] == loc


def test_translations_round_trip_and_latest_per_language(store: Store) -> None:
    def record(language: str, title: str, status: str = "verified") -> TranslationRecord:
        return TranslationRecord(0, "vid00000001", language, "en", "abc123", title, "desc",
                                 "muse-spark-open", "muse-glimmer:30b", status, 5, [], [],
                                 {"title": "back", "description": "back d"}, 100, 50, "")
    first = store.add_translation(record("es", "Uno"), now=T0)
    store.add_translation(record("de", "Eins"), now=T0)
    newest = store.add_translation(record("es", "Uno bis", "flagged"), now=T0)
    got = store.get_translation(first)
    assert got is not None and got.title == "Uno" and got.back_translation["title"] == "back"
    assert got.created_at and got.id == first
    assert [t.id for t in store.list_translations("vid00000001")][0] == newest
    latest = store.latest_translations("vid00000001")
    assert set(latest) == {"es", "de"} and latest["es"].title == "Uno bis"
    assert latest["es"].status == "flagged" and store.list_translations("other") == []



def test_transcripts_round_trip(store: Store) -> None:
    assert store.latest_transcript("vid00000001") is None
    segments = [{"start_ms": 0, "end_ms": 4200, "text": "Hallo und willkommen 🌵"},
                {"start_ms": 4200, "end_ms": 9800, "text": "Heute topfen wir um."}]
    first = store.add_transcript(TranscriptRecord(0, "vid00000001", "de", "asr-test", "clip.mp4",
                                                  9800, segments, [], ""), now=T0)
    second = store.add_transcript(TranscriptRecord(0, "vid00000001", "", "asr-test", "clip.mp4",
                                                   9800, segments[:1], ["Piece 2 failed."], ""),
                                  now=T0 + timedelta(minutes=1))
    got = store.get_transcript(first)
    assert got is not None and got.id == first and got.segments == segments
    assert (got.language, got.model, got.source_name, got.duration_ms) == ("de", "asr-test",
                                                                          "clip.mp4", 9800)
    assert got.created_at == T0.isoformat()
    latest = store.latest_transcript("vid00000001")
    assert latest is not None and latest.id == second and latest.problems == ["Piece 2 failed."]
    assert store.get_transcript(999) is None


def make_subtitles(language: str, transcript_id: int = 1, status: str = "verified",
                   text: str = "Hola") -> SubtitleTranslationRecord:
    return SubtitleTranslationRecord(
        0, "vid00000001", transcript_id, language, "en",
        [{"start_ms": 0, "end_ms": 4200, "text": text}, {"start_ms": 4200, "end_ms": 5000, "text": ""}],
        "spark-open-test", "glimmer-test", status, 5, ["An issue."], ["A problem."],
        {"segments": [{"n": 1, "text": "Hello"}]}, 100, 50, "")


def test_subtitle_translations_round_trip(store: Store) -> None:
    assert store.list_subtitle_translations("vid00000001") == []
    new_id = store.add_subtitle_translation(make_subtitles("es", text="Hola 🌵"), now=T0)
    got = store.get_subtitle_translation(new_id)
    assert got is not None and got.id == new_id and got.created_at == T0.isoformat()
    assert got == replace(make_subtitles("es", text="Hola 🌵"), id=new_id, created_at=T0.isoformat())
    assert store.get_subtitle_translation(999) is None


def test_latest_subtitle_translations_per_language_and_transcript(store: Store) -> None:
    store.add_subtitle_translation(make_subtitles("es", 1, text="uno"), now=T0)
    store.add_subtitle_translation(make_subtitles("de", 1, text="eins"), now=T0)
    newest = store.add_subtitle_translation(make_subtitles("es", 1, "flagged", "uno bis"), now=T0)
    other = store.add_subtitle_translation(make_subtitles("fr", 2, text="un"), now=T0)
    assert [r.id for r in store.list_subtitle_translations("vid00000001")][:2] == [other, newest]
    latest = store.latest_subtitle_translations("vid00000001", 1)
    assert set(latest) == {"es", "de"} and latest["es"].id == newest
    assert latest["es"].segments[0]["text"] == "uno bis" and latest["es"].status == "flagged"
    assert set(store.latest_subtitle_translations("vid00000001", 2)) == {"fr"}
    assert store.latest_subtitle_translations("other", 1) == {}


def test_v5_database_upgrades_to_v6_and_keeps_its_data(tmp_path: Path) -> None:
    path = tmp_path / "v5.sqlite"
    with Store(path) as s:
        pid = s.begin_pull("chan0000001", now=T0)
        s.upsert_video(make_video(), pid, now=T0)
        s.put_draft(make_draft())
        s.add_transcript(TranscriptRecord(0, "vid00000001", "en", "asr-test", "clip.mp4", 5000,
                                          [{"start_ms": 0, "end_ms": 5000, "text": "Hello"}], [], ""),
                         now=T0)
    conn = sqlite3.connect(path)  # turn it back into a version 5 database
    conn.executescript("DROP TABLE packaging_reviews; DROP TABLE thumbnail_variants; DROP TABLE outlier_reports; "
                       "DROP TABLE subtitle_translations; " + BACK_TO_V8 + "ALTER TABLE videos DROP COLUMN gone_at; PRAGMA user_version = 5;")
    conn.close()
    with Store(path) as s:
        assert s.schema_version == 18
        assert s.get_video("vid00000001") is not None and s.get_draft("vid00000001") is not None
        transcript = s.latest_transcript("vid00000001")
        assert transcript is not None and transcript.segments[0]["text"] == "Hello"
        assert s.list_subtitle_translations("vid00000001") == []
        assert s.add_subtitle_translation(make_subtitles("es"), now=T0) > 0


def make_review(video_id: str = "vid00000001", status: str = "done", **kw: object) -> PackagingReview:
    base: dict[str, object] = dict(
        id=0, video_id=video_id, title="How to repot a cactus", thumbnail_url="https://i.ytimg.com/x.jpg",
        image_file="thumb_1.jpg", measurements={"width": 1280, "brightness": 90},
        reading={"text": "REPOT IT 🌵", "subject": "cactus"},
        critique={"points": ["Bigger text."], "ideas": [{"instruction": "Zoom in", "overlay_text": "REPOT"}]},
        problems=["A problem."], status=status, error="", models={"vision": "m", "segmenter": "s"},
        input_tokens=100, cached_tokens=10, output_tokens=50, cost_usd=0.01, created_at="")
    base.update(kw)
    return PackagingReview(**base)  # type: ignore[arg-type]


def make_variant(review_id: int, text_ok: bool | None = True, **kw: object) -> ThumbnailVariant:
    base: dict[str, object] = dict(
        id=0, review_id=review_id, video_id="vid00000001", instruction="Zoom in", overlay_text="REPOT",
        image_file="variant_1.jpg", text_read="REPOT", text_ok=text_ok, measurements={"share": 40},
        status="ok", error="", model="muse-image-1.0", cost_usd=0.0135, created_at="")
    base.update(kw)
    return ThumbnailVariant(**base)  # type: ignore[arg-type]


def test_packaging_reviews_round_trip_and_latest(store: Store) -> None:
    assert store.latest_packaging_review("vid00000001") is None
    first = store.add_packaging_review(make_review(), now=T0)
    second = store.add_packaging_review(make_review(status="failed", error="Nope.", cost_usd=None),
                                        now=T0 + timedelta(hours=1))
    store.add_packaging_review(make_review("vid00000002"), now=T0)
    got = store.get_packaging_review(first)
    assert got == make_review(id=first, created_at=T0.isoformat())
    assert got is not None and got.reading["text"] == "REPOT IT 🌵"
    latest = store.latest_packaging_review("vid00000001")
    assert latest is not None and latest.id == second and latest.status == "failed"
    assert latest.cost_usd is None and latest.error == "Nope."
    assert store.get_packaging_review(999) is None


def test_thumbnail_variants_round_trip(store: Store) -> None:
    review = store.add_packaging_review(make_review(), now=T0)
    a = store.add_thumbnail_variant(make_variant(review), now=T0)
    b = store.add_thumbnail_variant(make_variant(review, text_ok=False, status="flagged"), now=T0)
    c = store.add_thumbnail_variant(make_variant(review, text_ok=None, image_file="", status="failed",
                                                 error="Bad.", cost_usd=None), now=T0)
    store.add_thumbnail_variant(make_variant(review + 1), now=T0)
    assert [v.id for v in store.list_thumbnail_variants(review)] == [a, b, c]
    assert store.get_thumbnail_variant(a) == make_variant(review, created_at=T0.isoformat(), id=a)
    got_b, got_c = store.get_thumbnail_variant(b), store.get_thumbnail_variant(c)
    assert got_b is not None and got_b.text_ok is False
    assert got_c is not None and got_c.text_ok is None and got_c.cost_usd is None
    assert store.get_thumbnail_variant(999) is None


def test_v6_database_upgrades_to_v7_and_keeps_its_data(tmp_path: Path) -> None:
    path = tmp_path / "v6.sqlite"
    with Store(path) as s:
        pid = s.begin_pull("chan0000001", now=T0)
        s.upsert_video(make_video(), pid, now=T0)
        s.add_subtitle_translation(make_subtitles("es"), now=T0)
    conn = sqlite3.connect(path)  # turn it back into a version 6 database
    conn.executescript("DROP TABLE packaging_reviews; DROP TABLE thumbnail_variants; DROP TABLE outlier_reports; "
                       "" + BACK_TO_V8 + "ALTER TABLE videos DROP COLUMN gone_at; PRAGMA user_version = 6;")
    conn.close()
    with Store(path) as s:
        assert s.schema_version == 18
        assert s.get_video("vid00000001") is not None
        assert len(s.list_subtitle_translations("vid00000001")) == 1
        assert s.latest_packaging_review("vid00000001") is None
        review = s.add_packaging_review(make_review(), now=T0)
        assert s.add_thumbnail_variant(make_variant(review), now=T0) > 0


def make_outlier_report(**kw: object) -> OutlierReport:
    base: dict[str, object] = dict(
        id=0, pull_id=3, as_of="2026-10-20T10:00:00+00:00", params={"peers": 20},
        labels={"O1": {"video_id": "vid00000001", "ratio": 12.5, "views": 1250, "peer_median": 100.0}},
        insight={"patterns": [{"text": "Short titles.", "evidence": ["O1", "O2"], "kind": "title"}],
                 "per_video": [], "ideas": ["Try a question."]},
        problems=["A problem."], status="done", error="", model="muse-spark-1.3",
        input_tokens=100, cached_tokens=10, output_tokens=50, cost_usd=0.01, created_at="")
    base.update(kw)
    return OutlierReport(**base)  # type: ignore[arg-type]


def test_outlier_reports_round_trip_and_latest(store: Store) -> None:
    assert store.latest_outlier_report() is None
    first = store.save_outlier_report(make_outlier_report(), now=T0)
    second = store.save_outlier_report(
        make_outlier_report(pull_id=None, status="too_few", cost_usd=None, labels={}, insight={}),
        now=T0 + timedelta(minutes=1))
    assert second > first
    got = store.latest_outlier_report()
    assert got == make_outlier_report(pull_id=None, status="too_few", cost_usd=None, labels={},
                                      insight={}, id=second, created_at=(T0 + timedelta(minutes=1)).isoformat())


def test_outlier_report_keeps_every_field(store: Store) -> None:
    report_id = store.save_outlier_report(make_outlier_report(), now=T0)
    assert store.latest_outlier_report() == make_outlier_report(id=report_id, created_at=T0.isoformat())


def test_v7_database_upgrades_to_v8_and_keeps_its_data(tmp_path: Path) -> None:
    path = tmp_path / "v7.sqlite"
    with Store(path) as s:
        pid = s.begin_pull("chan0000001", now=T0)
        s.upsert_video(make_video(), pid, now=T0)
        s.add_packaging_review(make_review(), now=T0)
    conn = sqlite3.connect(path)  # turn it back into a version 7 database
    conn.executescript("DROP TABLE outlier_reports; " + BACK_TO_V8 + "ALTER TABLE videos DROP COLUMN gone_at; PRAGMA user_version = 7;")
    conn.close()
    with Store(path) as s:
        assert s.schema_version == 18
        assert s.get_video("vid00000001") is not None
        assert s.latest_packaging_review("vid00000001") is not None
        assert s.latest_outlier_report() is None
        assert s.save_outlier_report(make_outlier_report(), now=T0) > 0


def make_comment(comment_id: str = "cmt00000001", **kw: object) -> Comment:
    base: dict[str, object] = dict(
        comment_id=comment_id, video_id="vid00000001", author="Ada",
        text="How often should I water it?", published_at="2026-09-01T10:00:00Z",
        like_count=3, reply_count=1, fetched_at=T0.isoformat(),
    )
    base.update(kw)
    return Comment(**base)  # type: ignore[arg-type]


def test_comments_round_trip_newest_first(store: Store) -> None:
    assert store.list_comments("vid00000001") == []
    assert store.upsert_comments([
        make_comment("cmt00000001", published_at="2026-09-01T10:00:00Z"),
        make_comment("cmt00000002", text="Great video!", published_at="2026-09-03T10:00:00Z"),
        make_comment("cmt00000003", video_id="vid00000002", published_at="2026-09-04T10:00:00Z"),
    ], now=T0) == 3
    comments = store.list_comments("vid00000001")
    assert [c.comment_id for c in comments] == ["cmt00000002", "cmt00000001"]
    assert comments[1] == make_comment("cmt00000001")
    assert comments[1].category == "" and comments[1].classified_at == ""


def test_upsert_comments_updates_text_and_counts_but_keeps_the_category(store: Store) -> None:
    store.upsert_comments([make_comment()], now=T0)
    store.set_comment_categories({"cmt00000001": "question"}, now=T0)
    later = T0 + timedelta(days=1)
    assert store.upsert_comments(
        [make_comment(text="Edited words", like_count=9, reply_count=4, fetched_at="")],
        now=later) == 1
    got = store.list_comments("vid00000001")[0]
    assert (got.text, got.like_count, got.reply_count) == ("Edited words", 9, 4)
    assert got.category == "question" and got.classified_at == T0.isoformat()
    assert got.fetched_at == later.isoformat()
    assert len(store.list_comments("vid00000001")) == 1


def test_set_comment_categories_stamps_classified_at(store: Store) -> None:
    store.upsert_comments([make_comment("cmt00000001"), make_comment("cmt00000002")], now=T0)
    store.set_comment_categories({"cmt00000001": "question", "cmt00000002": "praise"}, now=T0)
    by_id = {c.comment_id: c for c in store.list_comments("vid00000001")}
    assert by_id["cmt00000001"].category == "question"
    assert by_id["cmt00000002"].category == "praise"
    assert by_id["cmt00000001"].classified_at == T0.isoformat()


def test_comment_replies_round_trip_and_status(store: Store) -> None:
    assert store.list_comment_replies("vid00000001") == []
    pinned = store.add_comment_reply("vid00000001", "", "Thanks for watching!", "muse-spark-1.3",
                                     ["A draft was dropped."], now=T0)
    reply = store.add_comment_reply("vid00000001", "cmt00000001", "Water it weekly.",
                                    "muse-spark-1.3", [], now=T0)
    store.add_comment_reply("vid00000002", "", "Other video.", "muse-spark-1.3", [], now=T0)
    drafts = store.list_comment_replies("vid00000001")
    assert [d.id for d in drafts] == [pinned, reply]
    assert drafts[0] == CommentReply(pinned, "vid00000001", "", "Thanks for watching!",
                                     "muse-spark-1.3", T0.isoformat(), "open",
                                     ("A draft was dropped.",))
    assert drafts[1].notes == ()
    store.set_comment_reply_status(reply, "used")
    assert [d.id for d in store.list_comment_replies("vid00000001", "used")] == [reply]
    assert [d.id for d in store.list_comment_replies("vid00000001", "open")] == [pinned]
    assert store.list_comment_replies("vid00000001", "dismissed") == []
    with pytest.raises(ValueError):
        store.set_comment_reply_status(reply, "held")
    assert store.list_comment_replies("vid00000001", "used")[0].status == "used"


def test_mark_comment_reply_posted_once_and_only_when_open(store: Store) -> None:
    store.upsert_comments([Comment("cmt00000001", "vid00000001", author="Alice", text="How often?",
                                   category="question")], now=T0)
    rid = store.add_comment_reply("vid00000001", "cmt00000001", "Weekly.", "muse-spark-1.3", [], now=T0)
    other = store.add_comment_reply("vid00000001", "", "Pinned.", "muse-spark-1.3", [], now=T0)
    assert store.get_comment_reply(999) is None
    assert [c.comment_id for c in store.comments_to_answer()] == ["cmt00000001"]
    assert store.mark_comment_reply_posted(rid, "Every week.", "UgzReply.1", now=T0) is True
    row = store.get_comment_reply(rid)
    assert (row.status, row.text, row.posted_comment_id) == ("posted", "Every week.", "UgzReply.1")
    assert row.posted_at == T0.isoformat()
    assert store.mark_comment_reply_posted(rid, "Again.", "UgzReply.2", now=T0) is False
    assert store.get_comment_reply(rid).posted_comment_id == "UgzReply.1"
    assert store.comments_to_answer() == [] and store.list_open_comment_replies() == [
        store.get_comment_reply(other)]
    assert [r.id for r in store.list_posted_comment_replies()] == [rid]
    assert store.get_comment("cmt00000001").author == "Alice" and store.get_comment("nope") is None
    dismissed = store.add_comment_reply("vid00000001", "", "Gone.", "muse-spark-1.3", [], now=T0)
    store.set_comment_reply_status(dismissed, "dismissed")
    assert store.mark_comment_reply_posted(dismissed, "Gone.", "x", now=T0) is False


def test_v15_database_upgrades_to_v16_and_keeps_its_replies(tmp_path: Path) -> None:
    path = tmp_path / "v15.sqlite"
    with Store(path) as s:
        rid = s.add_comment_reply("vid00000001", "cmt00000001", "Weekly.", "muse-spark-1.3", [], now=T0)
    conn = sqlite3.connect(path)
    conn.executescript(BACK_TO_V15 + "ALTER TABLE videos DROP COLUMN gone_at; PRAGMA user_version = 15;")
    conn.close()
    with Store(path) as s:
        assert s.schema_version == 18
        row = s.get_comment_reply(rid)
        assert (row.text, row.status, row.posted_comment_id, row.posted_at) == ("Weekly.", "open", "", "")


def test_dismiss_open_comment_replies_only_open_of_one_video(store: Store) -> None:
    first = store.add_comment_reply("vid00000001", "cmt00000001", "First.", "muse-spark-1.3",
                                    [], now=T0)
    second = store.add_comment_reply("vid00000001", "cmt00000002", "Second.", "muse-spark-1.3",
                                     [], now=T0)
    used = store.add_comment_reply("vid00000001", "cmt00000003", "Used.", "muse-spark-1.3",
                                   [], now=T0)
    store.set_comment_reply_status(used, "used")
    already = store.add_comment_reply("vid00000001", "", "Pinned.", "muse-spark-1.3", [], now=T0)
    store.set_comment_reply_status(already, "dismissed")
    store.add_comment_reply("vid00000002", "", "Other video.", "muse-spark-1.3", [], now=T0)
    assert store.dismiss_open_comment_replies("vid00000001") == 2
    by_id = {d.id: d.status for d in store.list_comment_replies("vid00000001")}
    assert by_id == {first: "dismissed", second: "dismissed", used: "used",
                     already: "dismissed"}
    assert store.list_comment_replies("vid00000002")[0].status == "open"
    assert store.dismiss_open_comment_replies("vid00000001") == 0


def test_reply_templates_round_trip_by_name(store: Store) -> None:
    assert store.list_reply_templates() == []
    thanks = store.save_reply_template("Thanks", "Thanks, {name}!", now=T0)
    question = store.save_reply_template("Answer", "Hi {name}, the answer is weekly.", now=T0)
    assert [t.name for t in store.list_reply_templates()] == ["Answer", "Thanks"]
    got = store.list_reply_templates()[1]
    assert got == ReplyTemplate(thanks, "Thanks", "Thanks, {name}!", T0.isoformat(), T0.isoformat())
    later = T0 + timedelta(hours=2)
    assert store.save_reply_template("Thanks!", "Thank you, {name}!", now=later,
                                     template_id=thanks) == thanks
    got = store.list_reply_templates()[1]
    assert (got.name, got.body) == ("Thanks!", "Thank you, {name}!")
    assert got.created_at == T0.isoformat() and got.updated_at == later.isoformat()
    assert store.delete_reply_template(question) is True
    assert store.delete_reply_template(question) is False
    assert [t.name for t in store.list_reply_templates()] == ["Thanks!"]


def test_custom_rules_round_trip(store: Store) -> None:
    assert store.list_custom_rules() == []
    rule = {"kind": "must_link", "domain": "example.com", "visibility": "public"}
    first = store.add_custom_rule("Newsletter link", "every public video must link my newsletter",
                                  rule, now=T0)
    second = store.add_custom_rule("Title length", "titles stay short",
                                   {"kind": "title_max", "chars": 60, "visibility": "all"}, now=T0)
    rules = store.list_custom_rules()
    assert [r.id for r in rules] == [first, second]
    assert rules[0] == CustomRule(first, "Newsletter link",
                                  "every public video must link my newsletter", rule, True,
                                  T0.isoformat())
    assert rules[0].enabled is True
    store.set_custom_rule_enabled(first, False)
    assert store.list_custom_rules()[0].enabled is False
    assert [r.id for r in store.list_custom_rules(enabled_only=True)] == [second]
    assert store.delete_custom_rule(first) is True
    assert store.delete_custom_rule(first) is False
    assert [r.id for r in store.list_custom_rules()] == [second]


def test_v12_database_upgrades_to_v13_and_keeps_its_data(tmp_path: Path) -> None:
    path = tmp_path / "v12.sqlite"
    with Store(path) as s:
        rule = {"kind": "must_link", "domain": "example.com", "visibility": "public"}
        s.add_custom_rule("Newsletter link", "every public video must link it", rule, now=T0)
    conn = sqlite3.connect(path)
    conn.executescript(BACK_TO_V12 + "ALTER TABLE videos DROP COLUMN gone_at; PRAGMA user_version = 12;")
    conn.close()
    with Store(path) as s:
        assert s.schema_version == 18
        assert s.list_custom_rules()[0].name == "Newsletter link"
        assert s.latest_shorts_find("vid00000001") is None
        from yezatas.store import ShortsFind

        rec = ShortsFind(0, "vid00000001", 1, "", "m", "found", "msg", [], [], "", 0, 0)
        assert s.save_shorts_find(rec, now=T0) > 0


def test_v11_database_upgrades_to_v12_and_keeps_its_data(tmp_path: Path) -> None:
    path = tmp_path / "v11.sqlite"
    with Store(path) as s:
        turn = s.begin_copilot_turn(1, "What should I fix?", "muse-spark-1.3", now=T0)
        s.save_link_check("https://example.com/page", "ok", 200, now=T0)
    conn = sqlite3.connect(path)  # turn it back into a version 11 database
    conn.executescript(BACK_TO_V11 + "ALTER TABLE videos DROP COLUMN gone_at; PRAGMA user_version = 11;")
    conn.close()
    with Store(path) as s:
        assert s.schema_version == 18
        assert s.get_copilot_turn(turn) is not None
        assert set(s.latest_link_checks()) == {"https://example.com/page"}
        assert s.list_comments("vid00000001") == [] and s.list_reply_templates() == []
        assert s.list_custom_rules() == []
        assert s.upsert_comments([make_comment()], now=T0) == 1
        assert s.save_reply_template("Thanks", "Thanks, {name}!", now=T0) > 0


def test_previous_view_counts_reads_the_snapshot_before_the_newest(store: Store) -> None:
    p1 = store.begin_pull("chan0000001", now=T0)
    store.upsert_video(make_video(view_count=100), p1, now=T0)
    store.upsert_video(make_video("vid00000002", view_count=7), p1, now=T0)
    store.finish_pull(p1, 2, now=T0)
    assert store.previous_view_counts() == {}  # one pull: nothing to compare with
    day2 = T0 + timedelta(days=1)
    p2 = store.begin_pull("chan0000001", now=day2)
    store.upsert_video(make_video(view_count=250), p2, now=day2)
    store.finish_pull(p2, 1, now=day2)
    day3 = T0 + timedelta(days=2)
    p3 = store.begin_pull("chan0000001", now=day3)
    store.upsert_video(make_video(view_count=400), p3, now=day3)
    store.finish_pull(p3, 1, now=day3)
    assert store.previous_view_counts() == {"vid00000001": (day2.isoformat(), 250)}


def test_v13_database_upgrades_to_v14_and_keeps_its_comments(tmp_path: Path) -> None:
    path = tmp_path / "v13.sqlite"
    with Store(path) as s:
        s.upsert_comments([make_comment()], now=T0)
    conn = sqlite3.connect(path)
    conn.executescript(BACK_TO_V13 + "ALTER TABLE videos DROP COLUMN gone_at; PRAGMA user_version = 13;")
    conn.close()
    with Store(path) as s:
        assert s.schema_version == 18
        kept = s.list_comments("vid00000001")
        assert [c.comment_id for c in kept] == ["cmt00000001"] and kept[0].owner_replied is False


def test_comments_to_answer_skips_answered_used_and_dismissed(store: Store) -> None:
    store.upsert_comments([
        make_comment("cmt00000001", published_at="2026-09-05T10:00:00Z"),
        make_comment("cmt00000002", published_at="2026-09-04T10:00:00Z", owner_replied=True),
        make_comment("cmt00000003", published_at="2026-09-03T10:00:00Z"),
        make_comment("cmt00000004", published_at="2026-09-02T10:00:00Z"),
        make_comment("cmt00000005", published_at="2026-09-01T10:00:00Z"),
        make_comment("cmt00000006", published_at="2026-09-06T10:00:00Z"),
    ], now=T0)
    store.set_comment_categories({f"cmt0000000{n}": "question" for n in range(1, 6)}
                                 | {"cmt00000006": "praise"}, now=T0)
    used = store.add_comment_reply("vid00000001", "cmt00000003", "Thanks!", "m", [], now=T0)
    store.set_comment_reply_status(used, "used")
    gone = store.add_comment_reply("vid00000001", "cmt00000004", "Hi", "m", [], now=T0)
    store.set_comment_reply_status(gone, "dismissed")
    store.add_comment_reply("vid00000001", "cmt00000005", "Open draft", "m", [], now=T0)
    assert [c.comment_id for c in store.comments_to_answer()] == ["cmt00000001", "cmt00000005"]
    assert [c.comment_id for c in store.comments_to_answer(limit=1)] == ["cmt00000001"]
    store.upsert_comments([make_comment("cmt00000001", published_at="2026-09-05T10:00:00Z",
                                        owner_replied=True)], now=T0)
    assert [c.comment_id for c in store.comments_to_answer()] == ["cmt00000005"]


# --- description templates (v18) ---

SUPPORT_TEXT = "Support the garden channel:\nhttps://shop.test/support"


def test_v17_database_upgrades_to_v18_and_keeps_its_data(tmp_path: Path) -> None:
    path = tmp_path / "v17.sqlite"
    with Store(path) as s:
        pid = s.begin_pull("chan0000001", now=T0)
        s.upsert_video(make_video(), pid, now=T0)
        rid = s.add_comment_reply("vid00000001", "cmt00000001", "Weekly.", "muse-spark-1.3", [], now=T0)
    conn = sqlite3.connect(path)
    conn.executescript(BACK_TO_V17 + "PRAGMA user_version = 17;")
    conn.close()
    with Store(path) as s:
        assert s.schema_version == 18
        assert s.get_video("vid00000001").title == "How to repot a cactus"
        assert s.get_comment_reply(rid).text == "Weekly."
        assert s.list_template_blocks() == [] and s.list_description_templates() == []
        assert s.template_choices() == {} and s.templates_include_shorts() is False
        assert s.playlists() == [] and s.playlist_memberships() == {}


def test_template_block_round_trip(store: Store) -> None:
    bid = store.add_template_block("  Support   and follow ", SUPPORT_TEXT + "\r\n", now=T0)
    block = store.get_template_block(bid)
    assert block is not None
    assert (block.name, block.text, block.old_versions, block.link_slot) == (
        "Support and follow", SUPPORT_TEXT, (), False)
    assert block.created_at == block.updated_at == T0.isoformat()
    paid = store.add_template_block("Paid links", "Some links below are paid links.", link_slot=True)
    assert [b.id for b in store.list_template_blocks()] == [bid, paid]
    assert store.get_template_block(paid).link_slot is True
    assert store.get_template_block(999) is None


@pytest.mark.parametrize("name, text, message", [
    ("", "Text", "Give the block a name."),
    ("x" * 101, "Text", "at most 100 characters"),
    ("Support", "   \n ", "The block needs some text."),
    ("Support", "Shop <here>", "cannot contain < or >"),
    ("Support", "a > b", "cannot contain < or >"),
    ("Support", "x" * 5001, "at most 5,000 characters"),
])
def test_template_block_validation(store: Store, name: str, text: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        store.add_template_block(name, text)
    assert store.list_template_blocks() == []


def test_updating_a_block_keeps_old_versions_newest_first_without_duplicates(store: Store) -> None:
    bid = store.add_template_block("Support", "First text.")
    store.update_template_block(bid, text="Second text.")
    store.update_template_block(bid, text="Second text.")  # unchanged: adds nothing
    store.update_template_block(bid, text="Third text.")
    assert store.get_template_block(bid).old_versions == ("Second text.", "First text.")
    store.update_template_block(bid, text="First text.")  # back to an old text: it leaves the old list
    block = store.get_template_block(bid)
    assert block.text == "First text." and block.old_versions == ("Third text.", "Second text.")
    store.update_template_block(bid, name="Support and follow", link_slot=True, now=T0)
    block = store.get_template_block(bid)
    assert (block.name, block.text, block.link_slot, block.updated_at) == (
        "Support and follow", "First text.", True, T0.isoformat())
    assert block.old_versions == ("Third text.", "Second text.")


def test_updating_a_block_validates(store: Store) -> None:
    bid = store.add_template_block("Support", "First text.")
    with pytest.raises(ValueError, match="does not exist"):
        store.update_template_block(999, text="x")
    with pytest.raises(ValueError, match="< or >"):
        store.update_template_block(bid, text="<b>")
    with pytest.raises(ValueError, match="name"):
        store.update_template_block(bid, name=" ")
    assert store.get_template_block(bid).text == "First text."


def test_a_used_block_cannot_be_deleted(store: Store) -> None:
    bid = store.add_template_block("Support", SUPPORT_TEXT)
    tid = store.add_description_template("Main", [{"kind": "block", "block_id": bid}])
    with pytest.raises(ValueError, match='"Main" uses this block'):
        store.delete_template_block(bid)
    store.update_description_template(tid, sections=[{"kind": "opening"}])
    assert store.delete_template_block(bid) is True
    assert store.delete_template_block(bid) is False and store.list_template_blocks() == []


def test_description_template_round_trip(store: Store) -> None:
    bid = store.add_template_block("Support", SUPPORT_TEXT)
    sections = [{"kind": "opening"}, {"kind": "chapters"}, {"kind": "block", "block_id": bid},
                {"kind": "hashtags"}]
    rules = [{"kind": "playlist", "value": "PLbeds"}, {"kind": "title_word", "value": " raised   bed "},
             {"kind": "tag", "value": "compost"}]
    tid = store.add_description_template("  Main ", sections, rules, now=T0)
    tpl = store.get_description_template(tid)
    assert tpl is not None
    assert (tpl.name, tpl.position, tpl.is_default) == ("Main", 0, False)
    assert tpl.sections == tuple(sections)
    assert tpl.rules == ({"kind": "playlist", "value": "PLbeds"}, {"kind": "title_word", "value": "raised bed"},
                         {"kind": "tag", "value": "compost"})
    assert tpl.created_at == T0.isoformat()
    second = store.add_description_template("Shorts", [{"kind": "opening"}])
    assert store.get_description_template(second).position == 1 and store.get_description_template(999) is None


def test_updating_a_template_keeps_what_is_left_out(store: Store) -> None:
    tid = store.add_description_template("Main", [{"kind": "opening"}], [{"kind": "tag", "value": "soil"}])
    store.update_description_template(tid, name="Main template")
    tpl = store.get_description_template(tid)
    assert tpl.name == "Main template" and tpl.sections == ({"kind": "opening"},)
    assert tpl.rules == ({"kind": "tag", "value": "soil"},)
    store.update_description_template(tid, sections=[{"kind": "about"}], rules=[])
    tpl = store.get_description_template(tid)
    assert tpl.sections == ({"kind": "about"},) and tpl.rules == ()
    with pytest.raises(ValueError, match="does not exist"):
        store.update_description_template(999, name="x")
    with pytest.raises(ValueError, match="not a kind of section"):
        store.update_description_template(tid, sections=[{"kind": "footer"}])
    assert store.get_description_template(tid).sections == ({"kind": "about"},)


@pytest.mark.parametrize("sections, message", [
    ([{"kind": "footer"}], '"footer" is not a kind of section'),
    (["opening"], "is not a kind of section"),
    ([{"kind": "block", "block_id": 999}], "a block that does not exist"),
    ([{"kind": "block"}], "a block that does not exist"),
    ([{"kind": "block", "block_id": True}], "a block that does not exist"),
    ([{"kind": "block", "block_id": "BLOCK"}, {"kind": "block", "block_id": "BLOCK"}],
     "A block can be in a template only once"),
    ([{"kind": "opening"}, {"kind": "about"}, {"kind": "opening"}], '"opening" can be in a template only once'),
])
def test_template_section_validation(store: Store, sections, message) -> None:
    bid = store.add_template_block("Support", SUPPORT_TEXT)
    sections = [{**s, "block_id": bid} if isinstance(s, dict) and s.get("block_id") == "BLOCK" else s
                for s in sections]
    with pytest.raises(ValueError, match=message):
        store.add_description_template("Main", sections)
    assert store.list_description_templates() == []


@pytest.mark.parametrize("rules, message", [
    ([{"kind": "channel", "value": "x"}], '"channel" is not a kind of rule'),
    ([{"value": "x"}], "is not a kind of rule"),
    (["tag"], "is not a kind of rule"),
    ([{"kind": "tag", "value": "   "}], "A rule needs a value"),
    ([{"kind": "tag"}], "A rule needs a value"),
    ([{"kind": "title_word", "value": "x" * 101}], "at most 100 characters"),
])
def test_template_rule_validation(store: Store, rules, message) -> None:
    with pytest.raises(ValueError, match=message):
        store.add_description_template("Main", [{"kind": "opening"}], rules)
    assert store.list_description_templates() == []


def test_a_rule_value_of_100_characters_is_fine(store: Store) -> None:
    tid = store.add_description_template("Main", [], [{"kind": "tag", "value": "x" * 100}])
    assert store.get_description_template(tid).rules[0]["value"] == "x" * 100


@pytest.mark.parametrize("name, message", [("", "Give the template a name."),
                                           ("   ", "Give the template a name."),
                                           ("x" * 101, "at most 100 characters")])
def test_template_name_validation(store: Store, name: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        store.add_description_template(name, [])


def test_only_one_default_template(store: Store) -> None:
    first = store.add_description_template("Main", [], is_default=True)
    second = store.add_description_template("Shorts", [], is_default=True)
    assert [t.is_default for t in store.list_description_templates()] == [False, True]
    store.set_default_template(first)
    assert {t.id: t.is_default for t in store.list_description_templates()} == {first: True, second: False}
    store.set_default_template(None)
    assert not any(t.is_default for t in store.list_description_templates())
    with pytest.raises(ValueError, match="does not exist"):
        store.set_default_template(999)


def test_reorder_templates(store: Store) -> None:
    a = store.add_description_template("A", [])
    b = store.add_description_template("B", [])
    c = store.add_description_template("C", [])
    store.reorder_description_templates([c, a, b])
    assert [(t.id, t.position) for t in store.list_description_templates()] == [(c, 0), (a, 1), (b, 2)]
    for bad in ([c, a], [c, a, b, a], [c, a, 999]):
        with pytest.raises(ValueError, match="every template exactly once"):
            store.reorder_description_templates(bad)
    assert [t.id for t in store.list_description_templates()] == [c, a, b]


def test_deleting_a_template_drops_its_choices(store: Store) -> None:
    main = store.add_description_template("Main", [])
    other = store.add_description_template("Other", [])
    store.set_template_choice("vid00000001", main)
    store.set_template_choice("vid00000002", other)
    store.exclude_from_templates("vid00000003")
    assert store.delete_description_template(main) is True
    assert store.template_choices() == {"vid00000002": other, "vid00000003": None}
    assert store.delete_description_template(main) is False
    assert [t.id for t in store.list_description_templates()] == [other]


def test_template_choices(store: Store) -> None:
    main = store.add_description_template("Main", [])
    assert store.template_choices() == {}
    store.set_template_choice("vid00000001", main, now=T0)
    store.exclude_from_templates("vid00000002", now=T0)
    assert store.template_choices() == {"vid00000001": main, "vid00000002": None}
    store.exclude_from_templates("vid00000001")  # a choice replaces the one before
    store.set_template_choice("vid00000002", main)
    assert store.template_choices() == {"vid00000001": None, "vid00000002": main}
    assert store.clear_template_choice("vid00000001") is True
    assert store.clear_template_choice("vid00000001") is False
    assert store.template_choices() == {"vid00000002": main}
    with pytest.raises(ValueError, match="does not exist"):
        store.set_template_choice("vid00000003", 999)
    assert "vid00000003" not in store.template_choices()


def test_templates_include_shorts_setting(store: Store) -> None:
    assert store.templates_include_shorts() is False
    store.set_templates_include_shorts(True)
    assert store.templates_include_shorts() is True
    store.set_templates_include_shorts(False)
    assert store.templates_include_shorts() is False


def test_playlists_round_trip_and_memberships(store: Store) -> None:
    store.replace_playlists({
        "PLsoil": ("soil basics", ["vid00000002", "vid00000001"]),
        "PLbeds": ("Raised bed builds", ["vid00000001", "vid00000001"]),  # a repeat is stored once
        "PLempty": ("An empty list", []),
    })
    assert [(p.playlist_id, p.title, p.video_ids) for p in store.playlists()] == [
        ("PLempty", "An empty list", ()),
        ("PLbeds", "Raised bed builds", ("vid00000001",)),
        ("PLsoil", "soil basics", ("vid00000002", "vid00000001")),
    ]
    assert store.playlist_memberships() == {"vid00000001": frozenset({"PLsoil", "PLbeds"}),
                                            "vid00000002": frozenset({"PLsoil"})}
    store.replace_playlists({"PLnew": ("Compost", ["vid00000003"])})
    assert [p.playlist_id for p in store.playlists()] == ["PLnew"]
    assert store.playlist_memberships() == {"vid00000003": frozenset({"PLnew"})}
    store.replace_playlists({})
    assert store.playlists() == [] and store.playlist_memberships() == {}


def test_replacing_playlists_is_all_or_nothing(store: Store) -> None:
    store.replace_playlists({"PLbeds": ("Raised bed builds", ["vid00000001"])})

    def failing_ids():
        yield "vid00000002"
        raise RuntimeError("the pull broke")

    with pytest.raises(RuntimeError):
        store.replace_playlists({"PLsoil": ("Soil", ["vid00000003"]), "PLbad": ("Bad", failing_ids())})
    assert [(p.playlist_id, p.video_ids) for p in store.playlists()] == [("PLbeds", ("vid00000001",))]
    with pytest.raises(sqlite3.IntegrityError):
        store.replace_playlists({"PLsoil": ("Soil", ["vid00000003"]), "PLbad": (None, [])})  # type: ignore[dict-item]
    assert store.playlist_memberships() == {"vid00000001": frozenset({"PLbeds"})}
