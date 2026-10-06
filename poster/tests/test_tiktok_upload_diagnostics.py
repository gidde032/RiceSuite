"""TikTok failures name their cause in the debug JSON (RiceSuite #62).

On 2026-10-05 one account failed four times at send_media with a bare
Playwright `Error`, and each screenshot was a blank page. The JSON held only
the error type, so neither the error text nor the page the browser had
reached could be read back. These tests drive the real post_media through
the recording fake browser; no browser or profile is involved.
"""
import inspect
import json
import re

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
