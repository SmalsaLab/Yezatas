"""A saved sign-in Google no longer accepts: Pull again points to step 6, and step 6 offers Sign in again.
Fakes only: the token is a made-up one written by the test helpers, and no real Google call is made."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from webhelpers import fake_sign_in_ok, make_ui, make_video, seed_catalog, step_class, write_client_file, write_token
from yezatas.auth import WRITE
from yezatas.config import Config
from yezatas.quota import _utc_now, quota_day
from yezatas.store import Store
from yezatas.web.guidance import SIGN_IN_AGAIN, SIGN_IN_REJECTED, NeedsSignIn, map_api_error
from yezatas.web.steps import CheckState

REJECTED = map_api_error(NeedsSignIn())[1]  # what a check says when the saved token no longer loads


@pytest.fixture(autouse=True)
def no_real_client(monkeypatch: pytest.MonkeyPatch) -> None:
    import googleapiclient.discovery

    def refuse(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("no real YouTube client may be built in these tests")

    monkeypatch.setattr(googleapiclient.discovery, "build", refuse)


def expired_ui(tmp_path: Path, scopes: list[str] | None = None, **kwargs: Any) -> Any:
    """A client file and a saved token, but the token no longer loads (as when Google refuses to refresh it)."""
    loader = {"creds": None}
    ui = make_ui(tmp_path, credentials_loader=lambda config: loader["creds"], **kwargs)
    write_client_file(ui.config)
    write_token(ui.config, scopes)
    ui.loader = loader  # type: ignore[attr-defined]
    return ui


def flashes(html: str) -> list[str]:
    return [re.sub(r"\s+", " ", f).strip() for f in
            re.findall(r'<div class="flash [^"]*" role="[^"]*">(.*?)</div>', html.replace("&#39;", "'"), re.S)]


def flash_is(html: str, text: str, step: str) -> bool:
    """One flash on the page: `text`, with its link to the step `step` (on Today or on Configuration)."""
    found = flashes(html)
    return (len(found) == 1 and found[0].startswith(text + " <a href=")
            and re.search(rf'<a href="(/configuration)?#step-{step}">', found[0]) is not None)


def card(html: str, key: str) -> str:
    start = html.index(f'id="step-{key}"')
    return html[start:html.index("</section>", start)].replace("&#39;", "'")


def ledger(ui: Any) -> list[str]:
    with Store(ui.config.catalog_db) as store:
        return [r["operation"] for r in store.quota_entries(quota_day(_utc_now()))]


# --- Pull again with a failed check ---------------------------------------------------------------

def test_pull_again_from_today_with_an_expired_sign_in_points_to_step_6(tmp_path: Path) -> None:
    ui = expired_ui(tmp_path)
    seed_catalog(ui.config, [make_video(1)])  # an earlier pull, so Today shows (as for the user)
    resp = ui.post("/configuration/pull", data={"back": "/"}, follow_redirects=False)
    assert resp.status_code == 303 and resp.headers["location"] == "/"
    today = ui.client.get("/")
    assert today.status_code == 200 and str(today.url).endswith("/")
    assert flashes(today.text) == [f'{REJECTED} <a href="/configuration#step-signin">Open Configuration</a>']
    assert "(step 7)" not in today.text
    assert REJECTED.endswith(SIGN_IN_AGAIN)
    assert ui.web.pull_job.snapshot().status == "idle" and ui.reader.calls == []


def test_pull_from_configuration_with_a_failed_auth_check_goes_to_step_6(tmp_path: Path) -> None:
    ui = expired_ui(tmp_path)
    ui.web._check = CheckState(False, "auth", REJECTED)
    resp = ui.post("/configuration/pull", follow_redirects=False)
    assert resp.status_code == 303 and resp.headers["location"].endswith("#step-signin")
    html = ui.setup_html()
    assert flash_is(html, REJECTED, "signin")
    assert ledger(ui) == [] and ui.reader.calls == []  # a failed check is not run again


@pytest.mark.parametrize("kind, message", [
    ("api_disabled", "The YouTube Data API v3 is turned off for this project."),
    ("quota", "Today's YouTube quota is used up."),
    ("no_channel", "This Google account has no YouTube channel."),
])
def test_other_failed_checks_keep_step_7_with_their_own_message(tmp_path: Path, kind: str, message: str) -> None:
    ui = expired_ui(tmp_path)
    ui.web._check = CheckState(False, kind, message)
    resp = ui.post("/configuration/pull", follow_redirects=False)
    assert resp.headers["location"].endswith("#step-check")
    assert flash_is(ui.setup_html(), message, "check")


def test_no_check_result_keeps_the_step_7_sentence(tmp_path: Path) -> None:
    ui = make_ui(tmp_path)
    write_client_file(ui.config)  # not signed in, so nothing can be checked
    resp = ui.post("/configuration/pull", follow_redirects=False)
    assert resp.headers["location"].endswith("#step-check")
    assert flash_is(ui.setup_html(), "Check the connection first (step 7).", "check")


# --- step 6's card in each state -------------------------------------------------------------------

def test_step_6_not_signed_in(tmp_path: Path) -> None:
    ui = make_ui(tmp_path)
    write_client_file(ui.config)
    six = card(ui.setup_html(), "signin")
    assert ">Sign in with Google</button>" in six
    assert "You are signed in" not in six and "Sign out" not in six and "Sign in again" not in six


def test_step_6_signed_in_and_ok(tmp_path: Path) -> None:
    ui = make_ui(tmp_path)
    write_client_file(ui.config)
    write_token(ui.config, [WRITE])
    ui.post("/configuration/check")
    html = ui.setup_html()
    six = card(html, "signin")
    assert "done" in step_class(html, "signin")
    assert "You are signed in, and you allowed Yezatas to edit your videos" in six
    assert ">Sign out</button>" in six and "Sign in again" not in six and "signin-rejected" not in six


def test_step_6_signed_in_read_only(tmp_path: Path) -> None:
    ui = make_ui(tmp_path)
    write_client_file(ui.config)
    write_token(ui.config)
    six = card(ui.setup_html(), "signin")
    assert "You are signed in with read-only access." in six
    assert ">Sign out</button>" in six and "Sign in again</button>" not in six


@pytest.mark.parametrize("scopes", [None, [WRITE]])
def test_step_6_with_a_rejected_sign_in(tmp_path: Path, scopes: list[str] | None) -> None:
    ui = expired_ui(tmp_path, scopes)
    ui.post("/configuration/check")
    html = ui.setup_html()
    six = card(html, "signin")
    assert "problem" in step_class(html, "signin")
    assert "You are signed in" not in six
    # the What to fix line stays, and the card says why with the guidance sentence
    assert REJECTED in six
    assert f'<p id="signin-rejected">{SIGN_IN_REJECTED} Sign in again with the same account.</p>' in six
    buttons = re.findall(r'action="([^"]+)"[^>]*>\s*<input[^>]*>\s*<button type="submit" class="button ([^"]+)">([^<]+)<',
                         six)
    assert buttons == [("/configuration/signin", "primary", "Sign in again"),
                       ("/configuration/signout", "secondary", "Sign out")]
    edit_note = "Yezatas asks again for permission to edit your videos before your next push."
    assert (edit_note in re.sub(r"\s+", " ", six)) == (scopes == [WRITE])
    # the pull card no longer offers Check and pull
    pull = card(html, "pull")
    assert "Sign in again first (step 6)." in pull and "Check and pull" not in pull


def test_step_6_after_a_pull_that_failed_on_the_sign_in(tmp_path: Path) -> None:
    ui = expired_ui(tmp_path)
    ui.loader["creds"] = object()  # the check passes, then the token stops working during the pull
    ui.post("/configuration/check")
    ui.loader["creds"] = None
    ui.web._check = None  # as after a restart, with only the failed pull to go on
    ui.web.start_pull()
    ui.web.pull_job.wait()
    assert ui.web.pull_job.snapshot().error_kind == "auth"
    six = card(ui.setup_html(), "signin")
    assert "You are signed in" not in six and "Sign in again</button>" in six


# --- signing in again ------------------------------------------------------------------------------

def test_sign_in_again_replaces_the_rejected_token(tmp_path: Path) -> None:
    signed: list[str] = []

    def sign_in(config: Config) -> Any:
        signed.append("browser")
        return fake_sign_in_ok(config)  # writes a fresh read-only token over the old one

    ui = expired_ui(tmp_path, [WRITE], sign_in=sign_in)
    ui.post("/configuration/check")
    assert ui.web.check is not None and ui.web.check.kind == "auth"
    ui.loader["creds"] = object()  # the new token loads
    resp = ui.post("/configuration/signin", follow_redirects=False)
    assert resp.status_code == 303
    ui.web.signin_job.wait()
    assert signed == ["browser"] and ui.web.signin_job.snapshot().status == "done"
    assert ui.web.check is not None and ui.web.check.ok  # the check ran again by itself
    html = ui.setup_html()
    six = card(html, "signin")
    assert "done" in step_class(html, "signin")
    # the new token is read-only: edit access is asked again at the next push
    assert "You are signed in with read-only access." in six and "Sign in again" not in six


def test_a_working_sign_in_still_cannot_be_replaced(tmp_path: Path) -> None:
    ui = make_ui(tmp_path, sign_in=lambda c: (_ for _ in ()).throw(AssertionError("no sign-in")))
    write_client_file(ui.config)
    write_token(ui.config)
    assert "already signed in" in ui.post("/configuration/signin").text  # no check yet
    ui.post("/configuration/check")
    assert "already signed in" in ui.post("/configuration/signin").text  # a passing check
    ui.web._check = CheckState(False, "quota", "Today's YouTube quota is used up.")
    assert "already signed in" in ui.post("/configuration/signin").text  # a failure that is not the sign-in
    assert ui.web.signin_job.snapshot().status == "idle"
