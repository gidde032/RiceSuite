"""Slow-network TikTok regressions (RiceSuite #28).

Driven through the recording fake browser with its virtual clock: a Post
button can stay disabled, or a success signal stay hidden, until a scripted
time, and a click on a disabled button waits and times out the way
Playwright's actionability check does.
"""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend import tiktok_browser
from tests.browser_trace import RAISE, Script, run_traced

CAPTION = "sunset over the harbour #latergram #goldenhour @studio"
BASE_POST = (
    """locator("button:has-text('Post')").filter(has_not=locator("button:has-text('Cancel'), """
    """button:has-text('Save')")).last"""
)
# The upload page shows this before the file has finished uploading
# (maintainer's failure screenshot, 2026-09-28).
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
    assert "Post stayed disabled for 450s" in rec.lines[-1]
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

    assert "Post stayed disabled for 17s" in rec.lines[-1]
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


def test_singular_upload_toast_counts_as_success():
    """'Your video is being uploaded' was matched only by the dropped bare
    'uploaded' phrase (review, #29)."""
    toast = "Your video is being uploaded"
    assert any(p.lower() in toast.lower() for p in tiktok_browser.TT_UPLOAD_BANNER_TEXTS)


def test_role_dialog_confirmation_modal_is_recognised():
    """A confirmation dialog marked only by role must still be clicked
    now that the page-wide fallback is gone (review, #29)."""
    assert "[role='dialog'] button:has-text('Post')" in tiktok_browser.TT_CONFIRM_MODAL_POST


def test_missing_post_button_fails_fast(monkeypatch, tmp_sessions, media, allow_browser_post_media):
    """A Post selector that matches nothing is a layout problem, not a slow
    upload: fail within a minute, not after the full 450s cap."""
    rec = _run(monkeypatch, media, _script(enabled={"button:has-text('Post')": RAISE}))

    assert rec.lines[-1].startswith("RAISED")
    assert "Post button not found" in rec.lines[-1]
    assert rec.now < 90
    assert _post_clicks(rec) == []


def test_blocked_post_click_fails_with_the_dialog_text(monkeypatch, tmp_sessions, media, allow_browser_post_media):
    """Playwright never dispatches a click that fails its pre-click checks,
    so an overlay blocking Post means nothing was posted: failed, quoting the
    dialog, never unconfirmed (review, #29)."""
    script = _script(
        click_raises={BASE_POST: True},
        visible={"TUXModal": True},
        inner_text={"TUXModal": "Turn on automatic content checks?", "": CAPTION},
    )
    rec = _run(monkeypatch, media, script)

    assert rec.lines[-1].startswith("RAISED")
    assert "blocked by a TikTok dialog" in rec.lines[-1]
    assert "Turn on automatic content checks?" in rec.lines[-1]


def test_login_redirect_after_post_is_not_success(monkeypatch, tmp_sessions, media, allow_browser_post_media):
    """Leaving /upload counts as success only when it is not a login bounce
    (whose encoded redirect_url hides '/upload')."""
    def url(now):
        if now < 250:
            return "https://www.tiktok.com/upload"
        return "https://www.tiktok.com/login?redirect_url=https%3A%2F%2Fwww.tiktok.com%2Fupload"

    script = _script(url=url, enabled={"button:has-text('Post')": 200.0},
                     visible=NOT_CONFIRMED, wait_for_selector=NOT_CONFIRMED_WAITS)
    rec = _run(monkeypatch, media, script)

    assert rec.lines[-1] == "RETURN 'tt_post_unconfirmed_A'"


@pytest.mark.parametrize("failed_stage", ["browser_start", "browser_context", "browser_page"])
def test_browser_startup_failure_saves_metadata(monkeypatch, tmp_path, tmp_sessions, media, allow_browser_post_media, failed_stage):
    context = SimpleNamespace(pages=[], new_page=AsyncMock(side_effect=RuntimeError("page failed")),
                              close=AsyncMock())

    class PW:
        async def __aenter__(self):
            if failed_stage == "browser_start":
                raise RuntimeError("driver failed")
            return self

        async def __aexit__(self, *args):
            pass

    monkeypatch.setattr(tiktok_browser, "async_playwright", PW)
    monkeypatch.setattr(tiktok_browser, "_get_context", AsyncMock(
        return_value=(context, None),
        side_effect=RuntimeError("launch failed") if failed_stage == "browser_context" else None,
    ))
    with pytest.raises(Exception, match="TikTok post failed for A"):
        asyncio.run(tiktok_browser.post_media("A", media, CAPTION, "video"))
    [meta] = _diagnostics(tmp_path)
    assert meta["stage"] == failed_stage
    assert meta["outcome"] == "failed"
    assert meta["error_type"] == "RuntimeError"
    assert "screenshot_error_type" in meta
    assert context.close.await_count == (1 if failed_stage == "browser_page" else 0)
    assert not (media.parent / "tt_upload_A.mp4").exists()


def test_detached_upload_frame_after_redirect_is_still_success(monkeypatch, tmp_sessions, media, allow_browser_post_media):
    """Iframe layout: Post redirects to the Studio dashboard and the upload
    frame detaches, so probing it raises. The redirect must still confirm
    (review, #29: the probe used to escape and report unconfirmed)."""
    def url(now):
        return "https://www.tiktok.com/upload" if now < 250 else "https://www.tiktok.com/tiktokstudio/content"

    script = _script(
        url=url,
        enabled={"button:has-text('Post')": 200.0},
        wait_for_selector={"iframe[src*='upload']": None},
        # Frame key first: the fake uses the first matching substring.
        visible={"frame.locator(\"div:has-text('are being uploaded')": RAISE, **NOT_CONFIRMED},
    )
    rec = _run(monkeypatch, media, script)

    assert rec.lines[-1] == "RETURN 'tt_post_ok_A'"


def test_detached_frame_without_redirect_keeps_observing(monkeypatch, tmp_sessions, media, allow_browser_post_media):
    script = _script(
        wait_for_selector={"iframe[src*='upload']": None},
        visible={"frame.locator(\"div:has-text('are being uploaded')": RAISE, **NOT_CONFIRMED},
    )
    monkeypatch.setattr(tiktok_browser, "TT_UPLOAD_TIMEOUT_S", 5)
    rec = _run(monkeypatch, media, script)

    assert rec.lines[-1] == "RETURN 'tt_post_unconfirmed_A'"
    # Observed every round until the cap, not abandoned at the first probe.
    assert rec.text().count("Search for post description") >= 5


def test_failed_upload_copy_leaves_no_partial_file(monkeypatch, tmp_sessions, media, tmp_path, allow_browser_post_media):
    def partial_copy(src, dst):
        Path(dst).write_bytes(b"half")
        raise OSError("disk full")

    monkeypatch.setattr(tiktok_browser.shutil, "copyfile", partial_copy)
    rec = _run(monkeypatch, media, _script())

    assert rec.lines[-1].startswith("RAISED Exception: TikTok post failed for A: disk full")
    assert not (media.parent / "tt_upload_A.mp4").exists()
    [meta] = _diagnostics(tmp_path)
    assert meta["stage"] == "prepare_media"


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
