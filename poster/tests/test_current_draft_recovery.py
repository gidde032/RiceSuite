"""Current Review recovery in a browser, with every request intercepted.

No server, platform, session, credentials, or live state is accessed. Chromium
uses its disposable profile, as the existing slot-layout fixture does.
"""

from urllib.parse import urlparse

import pytest

from tests.test_slot_header_layout import HTML, SLATE_CSS

playwright_api = pytest.importorskip("playwright.sync_api")
IDENTITY = {"size": 12, "mtime_ns": 1000}


@pytest.fixture
def browser_page():
    writes = []
    with playwright_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel="chrome", headless=True)
        except Exception as exc:
            pytest.skip(f"Chrome unavailable for recovery fixture: {exc}")
        page = browser.new_page()

        def route(request):
            path = urlparse(request.request.url).path
            if request.request.method != "GET":
                writes.append(path)
                return request.fulfill(status=500, json={"detail": "unexpected write"})
            if path.endswith("slate.css"):
                return request.fulfill(body=SLATE_CSS, content_type="text/css")
            if path.endswith("/api/accounts"):
                return request.fulfill(
                    json={
                        "post_mode": "mock",
                        "accounts": [{"slot": "A", "name": "Fixture"}],
                        "available_accounts": [],
                        "sessions": {},
                        "account_state": {
                            "active_account_ids": ["A"],
                            "caption_defaults": {},
                        },
                        "caption_styles": [
                            {"name": "generic", "display_name": "Generic"}
                        ],
                        "default_caption_style": "generic",
                    }
                )
            if path.endswith("/api/media-stat"):
                return request.fulfill(json={"files": {"fixture.mp4": IDENTITY}})
            if "/api/" in path:
                return request.fulfill(
                    json={"entries": [], "batches": [], "snapshots": []}
                )
            if path == "/shell":
                return request.fulfill(
                    body='<iframe src="/post/" style="width:100%;height:100vh"></iframe>',
                    content_type="text/html",
                )
            return request.fulfill(body=HTML, content_type="text/html")

        page.route("**/*", route)
        yield page, writes
        browser.close()


def test_reload_offers_resume_and_preserves_latest_text_without_posts(browser_page):
    page, writes = browser_page
    page.goto("http://recovery.test/post/")
    page.wait_for_selector("#caption_A")
    page.evaluate("""() => {
      Object.assign(state.slots.A, {filename:'fixture.mp4', mediaType:'video', identityFilename:'fixture.mp4', mediaIdentity:{size:12,mtime_ns:1000}});
    }""")
    page.locator("#topic_A").fill("manual topic")
    page.locator("#caption_A").fill("latest caption")
    dialogs = []
    page.on("dialog", lambda dialog: (dialogs.append(dialog.type), dialog.accept()))
    page.reload()
    page.get_by_role("button", name="Resume saved drafts").wait_for()
    assert page.locator("#caption_A").input_value() == ""
    page.get_by_role("button", name="Resume saved drafts").click()
    playwright_api.expect(page.locator("#caption_A")).to_have_value("latest caption")
    assert page.locator("#topic_A").input_value() == "manual topic"
    assert dialogs == []
    assert writes == []


def test_iframe_unsaved_upload_warns_on_top_level_reload(browser_page):
    page, writes = browser_page
    page.goto("http://recovery.test/shell")
    frame = page.frame_locator("iframe")
    frame.locator("#caption_A").wait_for()
    # A real input gesture activates the child document's unload prompt.
    frame.locator("#caption_A").fill("caption")
    child = page.frames[1]
    child.evaluate(
        """() => { state.slots.A.file = {name:'pending.mp4'}; saveCurrentDrafts(); }"""
    )
    dialogs = []
    page.on("dialog", lambda dialog: (dialogs.append(dialog.type), dialog.accept()))
    page.reload()
    frame.get_by_role("button", name="Resume saved drafts").wait_for()
    assert "beforeunload" in dialogs
    assert "pending.mp4" in frame.locator("#currentRecovery").inner_text()
    assert writes == []
