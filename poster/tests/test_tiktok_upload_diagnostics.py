"""TikTok failures name their cause in the debug JSON (RiceSuite #62).

On 2026-10-05 one account failed four times at send_media with a bare
Playwright `Error`, and each screenshot was a blank page. The JSON held only
the error type, so neither the error text nor the page the browser had
reached could be read back. These tests drive the real post_media through
the recording fake browser; no browser or profile is involved.
"""
import asyncio
import inspect
import json
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from playwright.async_api import Error as PlaywrightError

from backend import tiktok_browser
from tests.browser_trace import RAISE, Script, run_traced

CAPTION = "sunset over the harbour #latergram #goldenhour @studio"
UPLOAD_URL = "https://www.tiktok.com/upload"
# The query strings TikTok really adds; none of this may reach the JSON.
STUDIO_URL = "https://www.tiktok.com/tiktokstudio/upload?from=upload&lang=en#top"


@pytest.fixture
def media(tmp_path):
    path = tmp_path / "2026-10-05_some_long_descriptive_name.mp4"
    path.write_bytes(b"not really a video")
    return path


def _script(**overrides):
    base = dict(
        url=UPLOAD_URL,
        wait_for_selector={"iframe[src*='upload']": RAISE},
        locator_wait={"Got it": RAISE},
        inner_text={"TUXModal": "", "": CAPTION},
        visible={"TUXModal": False},
    )
    base.update(overrides)
    return Script(**base)


def _run(monkeypatch, media, script):
    return run_traced(
        monkeypatch,
        tiktok_browser,
        lambda: tiktok_browser.post_media("A", media, CAPTION, "video", headless=True),
        script,
    )


def _only_diagnostics(tmp_path):
    files = sorted(tmp_path.glob("debug_tt_post_A_*.json"))
    assert len(files) == 1, files
    return json.loads(files[0].read_text())


def _fail_send_media(monkeypatch, message):
    async def _navigated_away(target, upload_path):
        raise PlaywrightError(message)

    monkeypatch.setattr(tiktok_browser, "_upload_media_file", _navigated_away)


# --- the two fields --------------------------------------------------------


def test_send_media_failure_records_the_page_url_and_first_error_line(
    monkeypatch, tmp_sessions, media, tmp_path, allow_browser_post_media
):
    """The 2026-10-05 failure shape: Playwright raises mid-upload with a
    multi-line message (first line, then its call log)."""
    first = "ElementHandle.set_input_files: Execution context was destroyed, most likely because of a navigation"
    _fail_send_media(monkeypatch, first + "\nCall log:\n  - waiting for locator(\"input[type='file']\")")

    rec = _run(monkeypatch, media, _script(url=STUDIO_URL))

    assert rec.lines[-1].startswith("RAISED")
    meta = _only_diagnostics(tmp_path)
    assert meta["stage"] == "send_media"
    assert meta["error_type"] == "Error"
    assert meta["page_url"] == "https://www.tiktok.com/tiktokstudio/upload"
    assert meta["error_message"] == first
    dumped = json.dumps(meta)
    assert "from=upload" not in dumped and "lang=en" not in dumped and "#top" not in dumped
    assert "Call log" not in dumped


def test_error_message_is_cut_to_200_characters(
    monkeypatch, tmp_sessions, media, tmp_path, allow_browser_post_media
):
    _fail_send_media(monkeypatch, "x" * 500 + "\nsecond line")

    _run(monkeypatch, media, _script())

    assert _only_diagnostics(tmp_path)["error_message"] == "x" * 200


def test_session_expiry_records_the_login_page_without_its_redirect_query(
    monkeypatch, tmp_sessions, media, tmp_path, allow_browser_post_media
):
    login = "https://www.tiktok.com/login?redirect_url=https%3A%2F%2Fwww.tiktok.com%2Fupload"

    rec = _run(monkeypatch, media, _script(url=login))

    assert "Session expired for A" in rec.lines[-1]
    meta = _only_diagnostics(tmp_path)
    assert meta["stage"] == "open_upload_page"
    assert meta["page_url"] == "https://www.tiktok.com/login"
    assert meta["error_message"].startswith("Session expired for A. Run login again")
    assert "redirect_url" not in json.dumps(meta)


# --- the caption never reaches the JSON ------------------------------------


def test_caption_stage_error_text_is_not_recorded(
    monkeypatch, tmp_sessions, media, tmp_path, allow_browser_post_media
):
    """The caption-verification error quotes the editor text, which holds
    the caption. Its type is recorded, its text is not."""
    garbled = CAPTION[:-4]
    script = _script(inner_text={"TUXModal": "", "": garbled}, counts={"TUXModal": 0})

    rec = _run(monkeypatch, media, script)

    assert "caption verification failed" in rec.lines[-1]
    assert garbled in rec.lines[-1]  # the raised error does quote it
    meta = _only_diagnostics(tmp_path)
    assert meta["stage"] == "enter_caption"
    assert meta["error_type"] == "Exception"
    assert meta["error_message"] is None
    assert garbled not in json.dumps(meta)


def test_unconfirmed_post_records_the_url_and_no_error(
    monkeypatch, tmp_sessions, media, tmp_path, allow_browser_post_media
):
    script = _script(visible={"TUXModal": False, "Search for post description": False,
                              "are being uploaded": False})
    monkeypatch.setattr(tiktok_browser, "TT_UPLOAD_TIMEOUT_S", 3)

    rec = _run(monkeypatch, media, script)

    assert rec.lines[-1] == "RETURN 'tt_post_unconfirmed_A'"
    meta = _only_diagnostics(tmp_path)
    assert meta["settled_url"] == UPLOAD_URL
    assert meta["page_url"] == UPLOAD_URL
    assert meta["error_type"] is None
    assert meta["error_message"] is None


def _post_media_stages_in_order():
    """Stage names as post_media sets them, in source (= execution) order."""
    src = inspect.getsource(tiktok_browser.post_media)
    return re.findall(r'(?:stage = |step\()"(\w+)"', src)


def test_error_text_is_only_recorded_before_the_caption_is_entered(allow_browser_post_media):
    """Every stage from enter_caption on can raise with editor text in its
    message. The allowlist must stay strictly before it. (The opt-in fixture
    puts the real post_media back, so its source is the one read.)"""
    stages = list(dict.fromkeys(_post_media_stages_in_order()))
    assert "enter_caption" in stages and "send_media" in stages
    before_caption = set(stages[: stages.index("enter_caption")])

    assert tiktok_browser.DIAGNOSTIC_ERROR_TEXT_STAGES <= before_caption
    assert "send_media" in tiktok_browser.DIAGNOSTIC_ERROR_TEXT_STAGES
    assert "open_upload_page" in tiktok_browser.DIAGNOSTIC_ERROR_TEXT_STAGES


def test_no_stage_from_enter_caption_on_records_error_text(tmp_path, allow_browser_post_media):
    """The list alone proves nothing unless _save_post_diagnostics consults
    it. recheck_caption re-raises the editor text just as enter_caption
    does, and a stage added later must be withheld until it is listed."""
    stages = list(dict.fromkeys(_post_media_stages_in_order()))
    withheld = stages[stages.index("enter_caption"):] + ["a_stage_added_later"]
    assert "recheck_caption" in withheld
    error = Exception(f"TikTok caption verification failed: editor text: {CAPTION!r}")
    page = SimpleNamespace(url=UPLOAD_URL, screenshot=AsyncMock())

    for stage in withheld + ["send_media"]:
        asyncio.run(tiktok_browser._save_post_diagnostics(
            page, stage, "failed", stage, tiktok_browser.monotonic(), {}, error))

    metas = {json.loads(p.read_text())["stage"]: p.read_text()
             for p in tmp_path.glob("debug_tt_post_*.json")}
    assert set(metas) == set(withheld) | {"send_media"}
    for stage in withheld:
        meta = json.loads(metas[stage])
        assert meta["error_type"] == "Exception", stage
        assert meta["error_message"] is None, stage
        assert CAPTION not in metas[stage], stage
    # The control: a listed stage does keep the text, so the check above
    # is not passing because nothing is ever recorded.
    assert json.loads(metas["send_media"])["error_message"].startswith("TikTok caption")


def test_error_message_drops_the_query_from_a_quoted_url(
    monkeypatch, tmp_sessions, media, tmp_path, allow_browser_post_media
):
    """Playwright quotes the URL that interrupted a navigation, query and
    all; the query must not reach the JSON through the error text either."""
    first = ('Page.goto: Navigation to "https://www.tiktok.com/upload" is interrupted by '
             'another navigation to "https://www.tiktok.com/login?redirect_url='
             'https%3A%2F%2Fwww.tiktok.com%2Fupload&lang=en#top"')

    async def _interrupted(page, account_key):
        raise PlaywrightError(first + '\nCall log:\n  - navigating to "https://www.tiktok.com/upload"')

    monkeypatch.setattr(tiktok_browser, "_open_upload_page", _interrupted)
    _run(monkeypatch, media, _script())

    meta = _only_diagnostics(tmp_path)
    assert meta["stage"] == "open_upload_page"
    assert meta["error_message"] == (
        'Page.goto: Navigation to "https://www.tiktok.com/upload" is interrupted by '
        'another navigation to "https://www.tiktok.com/login"'
    )
    dumped = json.dumps(meta)
    assert "redirect_url" not in dumped and "lang=en" not in dumped and "#top" not in dumped


@pytest.mark.parametrize("message, expected", [
    ("net::ERR_ABORTED at https://www.tiktok.com/upload?x=1 while loading",
     "net::ERR_ABORTED at https://www.tiktok.com/upload while loading"),
    ("frame was detached from chrome-error://chromewebdata/#x",
     "frame was detached from chrome-error://chromewebdata/"),
    # Text that is not a URL keeps its punctuation.
    ("Why? Upload failed #2", "Why? Upload failed #2"),
])
def test_error_first_line_strips_queries_only_from_urls(message, expected):
    assert tiktok_browser._error_first_line(Exception(message)) == expected


# --- the URL format -------------------------------------------------------


@pytest.mark.parametrize("url, expected", [
    (STUDIO_URL, "https://www.tiktok.com/tiktokstudio/upload"),
    ("https://www.tiktok.com/upload", "https://www.tiktok.com/upload"),
    # A failed navigation must not read as a TikTok page.
    ("chrome-error://chromewebdata/", "chrome-error://chromewebdata/"),
    ("about:blank", "about:blank"),
    ("https://user:secret@www.tiktok.com:8443/x?y=1", "https://www.tiktok.com:8443/x"),
    ("", None),
    (None, None),
])
def test_diagnostic_url_drops_the_query_fragment_and_credentials(url, expected):
    assert tiktok_browser._diagnostic_url(url) == expected


def test_diagnostic_url_is_bounded():
    long_url = "data:text/html," + "a" * 1000
    assert len(tiktok_browser._diagnostic_url(long_url)) == 200


# --- the upload page settles before the file is sent ------------------------
#
# The fixed 5s sleep in _open_upload_page became a wait for the URL to hold
# still. Each test spies on the moment the file is handed to the picker,
# read from the fake's virtual clock (run_traced patches the module's
# monotonic).

STUDIO_SETTLED = "https://www.tiktok.com/tiktokstudio/upload"


def _spy_send_time(monkeypatch):
    sent_at = []
    real_upload = tiktok_browser._upload_media_file

    async def _spy(target, upload_path):
        sent_at.append(tiktok_browser.monotonic())
        return await real_upload(target, upload_path)

    monkeypatch.setattr(tiktok_browser, "_upload_media_file", _spy)
    return sent_at


def test_file_is_sent_only_after_a_redirect_chain_settles(
    monkeypatch, tmp_sessions, media, allow_browser_post_media
):
    """A redirect chain whose last URL change lands at 5.5s. In the iframe
    layout the upload frame is found at once, so the old fixed 5s sent the
    file before the chain had ended."""
    def url(now):
        if now < 4.0:
            return UPLOAD_URL
        if now < 5.5:
            return "https://www.tiktok.com/tiktokstudio/upload?from=upload"
        return "https://www.tiktok.com/tiktokstudio/upload?from=upload&lang=en"

    sent_at = _spy_send_time(monkeypatch)
    rec = _run(monkeypatch, media, _script(url=url, wait_for_selector={}))

    assert rec.lines[-1] == "RETURN 'tt_post_ok_A'"
    assert "content_frame()" in rec.text()  # the iframe layout, as described
    # Last change at 5.5s, then 2s unchanged (decision record on #62).
    assert len(sent_at) == 1 and 7.5 <= sent_at[0] <= 8.0


def test_login_redirect_after_the_old_5s_is_caught_before_upload(
    monkeypatch, tmp_sessions, media, tmp_path, allow_browser_post_media
):
    """Studio at 4s bounces to login at 5.5s. The old flow read the URL
    once, at 5s, saw Studio and passed the login check, so the run failed
    later and less clearly (on a real page, at send_media) instead of as an
    expired session."""
    login = "https://www.tiktok.com/login?redirect_url=https%3A%2F%2Fwww.tiktok.com%2Fupload"

    def url(now):
        if now < 4.0:
            return UPLOAD_URL
        return STUDIO_URL if now < 5.5 else login

    sent_at = _spy_send_time(monkeypatch)
    rec = _run(monkeypatch, media, _script(url=url))

    assert rec.lines[-1].startswith("RAISED Exception: TikTok post failed for A: Session expired for A")
    assert sent_at == []
    meta = _only_diagnostics(tmp_path)
    assert meta["stage"] == "open_upload_page"
    assert meta["settled_url"] == "https://www.tiktok.com/login"


def test_a_stable_page_still_waits_the_old_5s_floor(
    monkeypatch, tmp_sessions, media, allow_browser_post_media
):
    """CLAUDE.md § Intentional design: a wait may grow, never shrink."""
    sent_at = _spy_send_time(monkeypatch)
    rec = _run(monkeypatch, media, _script())

    assert rec.lines[-1] == "RETURN 'tt_post_ok_A'"
    assert sent_at[0] >= 5.0
    assert tiktok_browser.UPLOAD_PAGE_SETTLE_FLOOR_S >= 5.0


def test_a_page_that_never_settles_is_used_at_the_cap_with_a_warning(
    monkeypatch, tmp_sessions, media, capsys, allow_browser_post_media
):
    """Reaching the cap is not a failure: a URL TikTok keeps rewriting must
    not block posting. It is logged, and the flow goes on as before."""
    def url(now):
        return f"{STUDIO_SETTLED}?tick={int(now)}"

    sent_at = _spy_send_time(monkeypatch)
    rec = _run(monkeypatch, media, _script(url=url))

    assert rec.lines[-1] == "RETURN 'tt_post_ok_A'"
    # The 15s cap from the decision record on #62, give or take one poll.
    assert len(sent_at) == 1 and 15.0 <= sent_at[0] <= 15.5
    out = capsys.readouterr().out
    assert "still navigating" in out and "tick=" not in out


def test_settled_url_shows_the_page_moved_after_settling(
    monkeypatch, tmp_sessions, media, tmp_path, allow_browser_post_media
):
    """The #62 failure shape: the page settles on TikTok Studio, then
    navigates away while the file uploads and the screenshot is blank."""
    def url(now):
        return STUDIO_URL if now < 6.0 else "chrome-error://chromewebdata/"

    async def _navigates_away(target, upload_path):
        await tiktok_browser.asyncio.sleep(3)
        raise PlaywrightError("ElementHandle.set_input_files: Target page, context or browser has been closed")

    monkeypatch.setattr(tiktok_browser, "_upload_media_file", _navigates_away)
    _run(monkeypatch, media, _script(url=url))

    meta = _only_diagnostics(tmp_path)
    assert meta["stage"] == "send_media"
    assert meta["settled_url"] == STUDIO_SETTLED
    assert meta["page_url"] == "chrome-error://chromewebdata/"
    assert meta["error_message"].startswith("ElementHandle.set_input_files: Target page")


def test_settled_url_is_null_before_the_upload_page_opens(
    monkeypatch, tmp_sessions, media, tmp_path, allow_browser_post_media
):
    def disk_full(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(tiktok_browser.shutil, "copyfile", disk_full)
    _run(monkeypatch, media, _script())

    meta = _only_diagnostics(tmp_path)
    assert meta["stage"] == "prepare_media"
    assert meta["settled_url"] is None
    assert meta["page_url"] is None
    assert meta["error_message"] == "disk full"


def test_error_first_line_stays_linear_on_a_long_unbroken_line():
    """An unbounded scheme pattern backtracks quadratically through a long
    run of letters (about 2 s at this length). The line is scanned before it
    is cut to 200 characters, so the scan itself must stay cheap."""
    import time

    started = time.perf_counter()
    result = tiktok_browser._error_first_line(Exception("a" * 60_000))
    assert time.perf_counter() - started < 0.5
    assert result == "a" * 200
