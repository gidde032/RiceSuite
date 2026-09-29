"""Slow-network TikTok regressions (RiceSuite #28).

Driven through the recording fake browser with its virtual clock: a Post
button can stay disabled, or a success signal stay hidden, until a scripted
time, and a click on a disabled button waits and times out the way
Playwright's actionability check does.
"""
import json

import pytest

from backend import tiktok_browser
from tests.browser_trace import RAISE, Script, run_traced

CAPTION = "sunset over the harbour #latergram #goldenhour @studio"
BASE_POST = (
    """locator("button:has-text('Post')").filter(has_not=locator("button:has-text('Cancel'), """
    """button:has-text('Save')")).last"""
)
# The upload page shows this before the file has finished uploading
# (debug_tt_post_altaccajax.png, 2026-09-28).
PRE_POST_TEXT = "Checks can only start after the file is uploaded."


@pytest.fixture
def media(tmp_path):
    path = tmp_path / "2026-09-28_some_long_descriptive_name.mp4"
    path.write_bytes(b"not really a video")
    return path


def _script(**overrides):
    base = dict(
        url="https://www.tiktok.com/upload",
        wait_for_selector={"iframe[src*='upload']": RAISE},
        locator_wait={"Got it": RAISE},
        inner_text={"TUXModal": "", "": CAPTION},
        visible={"TUXModal": False},
    )
    for key in ("wait_for_selector", "visible"):
        base[key].update(overrides.pop(key, {}))
    base.update(overrides)
    return Script(**base)


def _run(monkeypatch, media, script):
    return run_traced(
        monkeypatch,
        tiktok_browser,
        lambda: tiktok_browser.post_media("A", media, CAPTION, "video", headless=True),
        script,
    )


def _post_clicks(rec):
    return [line for line in rec.lines if "has-text('Post')" in line and ".click(" in line]


def _base_clicks(rec):
    return [line for line in rec.lines if line.startswith(BASE_POST + ".click(")]


def _diagnostics(tmp_path):
    return [json.loads(p.read_text()) for p in sorted(tmp_path.glob("debug_tt_post_A_*.json"))]


NOT_CONFIRMED = {
    "Search for post description": False,
    "are being uploaded": False,
}
# The pre-#28 flow confirmed through wait_for_selector; script the same
# absence there so each regression fails for the behaviour, not the API.
NOT_CONFIRMED_WAITS = {"Search for post description": RAISE, "are being uploaded": RAISE}


def test_post_waits_for_a_slow_upload(monkeypatch, tmp_sessions, media, allow_browser_post_media):
    """Post stays disabled for 200s of upload. The old flow clicked it about
    16s after the file was sent and gave up 30s later."""
    rec = _run(monkeypatch, media, _script(enabled={"button:has-text('Post')": 200.0}))

    assert rec.lines[-1] == "RETURN 'tt_post_ok_A'"
    clicks = _base_clicks(rec)
    assert len(clicks) == 1 and "TIMEOUT" not in clicks[0]


def test_upload_that_never_finishes_fails_before_post(monkeypatch, tmp_sessions, media, tmp_path, allow_browser_post_media):
    rec = _run(monkeypatch, media, _script(enabled={"button:has-text('Post')": False}))

    assert rec.lines[-1].startswith("RAISED")
    assert "did not finish uploading within 450s" in rec.lines[-1]
    assert _post_clicks(rec) == []
    assert rec.now >= 450
    [meta] = _diagnostics(tmp_path)
    assert meta["outcome"] == "failed"
    assert meta["stage"] == "await_upload"
    assert meta["stage_timings_s"]["await_upload"] >= 450
    assert CAPTION not in json.dumps(meta)


def test_upload_cap_is_configurable(monkeypatch, tmp_sessions, media, allow_browser_post_media):
    monkeypatch.setattr(tiktok_browser, "TT_UPLOAD_TIMEOUT_S", 17)
    rec = _run(monkeypatch, media, _script(enabled={"button:has-text('Post')": False}))

    assert "did not finish uploading within 17s" in rec.lines[-1]
    assert _post_clicks(rec) == []


def test_late_confirmation_is_observed(monkeypatch, tmp_sessions, media, allow_browser_post_media):
    """The dashboard appears 300s in, long after the old 45s + 15s windows."""
    script = _script(
        visible={**NOT_CONFIRMED, "Search for post description": 300.0},
        wait_for_selector=NOT_CONFIRMED_WAITS,
    )
    rec = _run(monkeypatch, media, script)

    assert rec.lines[-1] == "RETURN 'tt_post_ok_A'"
    assert len(_base_clicks(rec)) == 1


def test_unknown_outcome_is_observed_to_the_cap_then_unconfirmed(monkeypatch, tmp_sessions, media, tmp_path, allow_browser_post_media):
    script = _script(visible=NOT_CONFIRMED, wait_for_selector=NOT_CONFIRMED_WAITS)
    rec = _run(monkeypatch, media, script)

    assert rec.lines[-1] == "RETURN 'tt_post_unconfirmed_A'"
    assert len(_post_clicks(rec)) == 2  # base Post + the confirmation modal, once each
    [meta] = _diagnostics(tmp_path)
    assert meta["outcome"] == "unconfirmed"
    assert meta["stage"] == "confirmation"
    assert meta["stage_timings_s"]["confirmation"] >= 450


def test_pre_post_page_text_is_not_a_success_signal():
    """The old in-page banner check matched any div containing 'uploaded',
    which the upload page shows before Post is ever clicked."""
    assert tiktok_browser.TT_UPLOAD_BANNER_TEXTS
    for phrase in tiktok_browser.TT_UPLOAD_BANNER_TEXTS:
        assert phrase.lower() not in PRE_POST_TEXT.lower()
        assert f"has-text('{phrase}')" in tiktok_browser.TT_UPLOAD_BANNER


def test_no_modal_means_no_second_post_click(monkeypatch, tmp_sessions, media, allow_browser_post_media):
    """Without a confirmation modal, the old fallback re-clicked the last
    'Post' button on the page, which is the base Post button itself."""
    rec = _run(monkeypatch, media, _script(counts={"[class*='modal']": 0}))

    assert rec.lines[-1] == "RETURN 'tt_post_ok_A'"
    assert _post_clicks(rec) == _base_clicks(rec)
    assert len(_base_clicks(rec)) == 1


def test_confirmation_modal_is_still_clicked_once(monkeypatch, tmp_sessions, media, allow_browser_post_media):
    rec = _run(monkeypatch, media, _script())

    modal_clicks = [line for line in _post_clicks(rec) if "[class*='modal']" in line]
    assert len(modal_clicks) == 1
    assert len(_base_clicks(rec)) == 1


def test_caption_reset_during_upload_never_posts(monkeypatch, tmp_sessions, media, allow_browser_post_media):
    """If the editor falls back to the filename while the upload finishes,
    Post must not be clicked with that text."""
    def editor(now):
        return CAPTION if now < 100 else "tt_upload_A"

    script = _script(enabled={"button:has-text('Post')": 200.0},
                     inner_text={"TUXModal": "", "": editor})
    rec = _run(monkeypatch, media, script)

    assert rec.lines[-1].startswith("RAISED")
    assert "caption verification failed" in rec.lines[-1]
    assert _post_clicks(rec) == []


def test_interruption_after_post_is_unconfirmed_not_failed(monkeypatch, tmp_sessions, media, tmp_path, allow_browser_post_media):
    """A browser error after Post may follow a submitted post. Reporting it as
    failed would invite a retry and a duplicate."""
    script = _script(visible={"Search for post description": RAISE},
                     wait_for_selector={"Search for post description": RAISE})
    rec = _run(monkeypatch, media, script)

    assert rec.lines[-1] == "RETURN 'tt_post_unconfirmed_A'"
    assert len(_base_clicks(rec)) == 1
    [meta] = _diagnostics(tmp_path)
    assert meta["outcome"] == "unconfirmed"
    assert meta["error_type"] == "RuntimeError"
