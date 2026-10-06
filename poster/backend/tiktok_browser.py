"""
TikTok browser automation client.

Uses Playwright to automate posting through the TikTok web interface.
Requires saved browser sessions (login state) per account.
"""

import asyncio
import os
import re
import shutil
from contextlib import AsyncExitStack
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic
from urllib.parse import urlsplit, urlunsplit
from playwright.async_api import async_playwright, Page, BrowserContext, TimeoutError as PlaywrightTimeoutError

import json

# These are shared with instagram_browser and must behave identically on both
# platforms (tech-debt audit BE-3, 2026-07-29). _captions_match and
# EDITOR_MARKER joined them in Batch 6, when Instagram gained the same caption
# read-back check.
from backend.config import DEBUG_DIR, TT_SESSIONS_DIR, TT_UPLOAD_TIMEOUT_S
from backend.jitter import type_with_jitter
from backend.logging_setup import get_logger
from backend.browser_common import (
    EDITOR_MARKER,
    _captions_match,
    _post_id,
    _resolve_login_outcome,
    _selector_chain_error,
    url_matches_login_markers,
)

_log = get_logger("tiktok_browser")

# Directory to store persistent browser sessions (cookies/login state).
# Re-exported under the local name conftest's `tmp_sessions` fixture patches —
# see the layout section in config.py before changing this to a call-site
# reference.
SESSIONS_DIR = TT_SESSIONS_DIR
SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

# Failure screenshots go here (gitignored), same as instagram_browser
DEBUG_DIR.mkdir(exist_ok=True)

# URL fragments that mean "this is TikTok's login wall". TikTok redirects an
# expired session to /login. Owned here rather than in session_manager so
# platform knowledge lives with the platform (tech-debt audit BE-23).
LOGIN_REDIRECT_MARKERS = ("/login",)

# TikTok often surfaces the login wall as an in-page modal with no URL
# change, so the URL markers above are not sufficient on their own. Generous
# fallback chain in the codebase's idiom, since platform UIs shift.
LOGIN_MODAL_SELECTORS = (
    '[data-e2e="login-modal"]',
    '#loginContainer',
    'div[id*="login" i]',
)


async def login_modal_present(page) -> bool:
    """Probe the DOM for a TikTok login modal, complementing the /login URL
    check (DESIGN-scheduling.md §3b step 3). Never raises; any match → the
    session is expired.

    Moved here from session_manager by BE-23: the selectors are TikTok's, and
    session_manager should not need to know how TikTok renders a login wall.
    """
    for sel in LOGIN_MODAL_SELECTORS:
        try:
            if await page.locator(sel).count() > 0:
                return True
        except Exception:
            continue
    return False


def _is_usable_cookie_file(path: Path) -> bool:
    try:
        if (
            path.is_symlink()
            or path.parent.is_symlink()
            or SESSIONS_DIR.is_symlink()
            or not path.is_file()
            or path.stat().st_size <= 10
        ):
            return False
        cookies = json.loads(path.read_text())
        return (
            isinstance(cookies, list)
            and bool(cookies)
            and all(
                isinstance(cookie, dict)
                and isinstance(cookie.get("name"), str)
                and bool(cookie["name"])
                and isinstance(cookie.get("value"), str)
                for cookie in cookies
            )
        )
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False


def _cookies_file(account_key: str) -> Path:
    """Preferred cookie path, with the legacy flat file handled by readers."""
    preferred = SESSIONS_DIR / account_key / "cookies.json"
    legacy = SESSIONS_DIR / f"{account_key}_cookies.json"
    # A half-written or empty preferred file must not mask a usable legacy
    # export. This selection rule is shared by the availability probe and the
    # loader so they cannot disagree about which saved session is usable.
    for candidate in (preferred, legacy):
        if _is_usable_cookie_file(candidate):
            return candidate
    return preferred if preferred.exists() else legacy


def has_cookie_session(account_key: str) -> bool:
    """Check if exported cookies exist for this account."""
    path = _cookies_file(account_key)
    return _is_usable_cookie_file(path)


def has_profile_session(account_key: str) -> bool:
    """A persistent profile needs state beyond the optional cookie export.

    The preferred cookie file lives inside the same account directory. A bad
    or empty `cookies.json` must not make that directory look like an
    authenticated persistent profile merely because the file itself exists.
    """
    session_dir = SESSIONS_DIR / account_key
    if not session_dir.is_dir() or session_dir.is_symlink():
        return False
    try:
        return any(
            entry.name not in {"cookies.json", ".DS_Store"}
            for entry in session_dir.iterdir()
        )
    except OSError:
        return False


def _normalize_cookies(raw_cookies: list) -> list:
    """Convert Cookie-Editor export records into the cookie dicts Playwright's
    context.add_cookies expects. Cookie-Editor uses "expirationDate" and
    lowercase sameSite ("strict"/"lax"/"no_restriction"/"unspecified");
    Playwright wants "expires" and capitalized "Strict"/"Lax"/"None". This is
    the inverse of _playwright_to_cookie_editor for the round-trippable fields."""
    same_site_map = {
        "strict": "Strict",
        "lax": "Lax",
        "none": "None",
        "no_restriction": "None",
    }
    cookies = []
    for c in raw_cookies:
        cookie = {
            "name": c["name"],
            "value": c["value"],
            "domain": c.get("domain", ".tiktok.com"),
            "path": c.get("path", "/"),
        }
        # Cookie-Editor uses "expirationDate", Playwright uses "expires"
        if "expirationDate" in c:
            cookie["expires"] = c["expirationDate"]
        elif "expires" in c:
            cookie["expires"] = c["expires"]
        ss = same_site_map.get(str(c.get("sameSite", "")).lower())
        if ss:
            cookie["sameSite"] = ss
        if c.get("secure"):
            cookie["secure"] = True
        if c.get("httpOnly"):
            cookie["httpOnly"] = True
        cookies.append(cookie)
    return cookies


def _playwright_to_cookie_editor(cookie: dict) -> dict:
    """Convert a Playwright cookie (from context.cookies()) back into a
    Cookie-Editor export record so refreshed cookies round-trip through
    _normalize_cookies (DESIGN-scheduling.md §3a). The field mappings are not
    1:1 renames — the lossy corners (session cookies, sameSite) are handled
    explicitly:
      - expires == -1 (session cookie) → omit expirationDate, session=True.
      - sameSite "Strict"/"Lax"/"None" → "strict"/"lax"/"no_restriction";
        missing/unknown → "unspecified".
      - hostOnly is derived: True iff the domain does not start with ".".
      - storeId is the constant "0".
    Pure function — extract, test, use."""
    domain = cookie.get("domain", "")
    same_site_map = {"Strict": "strict", "Lax": "lax", "None": "no_restriction"}
    out = {
        "name": cookie.get("name", ""),
        "value": cookie.get("value", ""),
        "domain": domain,
        "path": cookie.get("path", "/"),
        "hostOnly": not domain.startswith("."),
        "httpOnly": bool(cookie.get("httpOnly", False)),
        "secure": bool(cookie.get("secure", False)),
        "sameSite": same_site_map.get(cookie.get("sameSite"), "unspecified"),
        "storeId": "0",
    }
    if cookie.get("expires", -1) == -1:
        out["session"] = True
    else:
        out["expirationDate"] = cookie["expires"]
        out["session"] = False
    return out


def _is_tiktok_domain(domain: str) -> bool:
    """True only for tiktok.com and its subdomains, using an anchored suffix
    match so lookalikes (faketiktok.com, x.tiktok.com.evil.example) are
    rejected — a bare `"tiktok.com" in domain` substring test accepts them.
    Cookie domains may carry a single leading dot; strip one. Pure/testable."""
    if not domain:
        return False
    d = domain[1:] if domain.startswith(".") else domain
    return d == "tiktok.com" or d.endswith(".tiktok.com")


async def _write_back_cookies(context, account_key: str, from_cookie_session: bool = True) -> None:
    """Persist the context's fresh cookies back to the session file after a
    successful TikTok post so long-lived sessions self-sustain instead of
    silently expiring (DESIGN-scheduling.md §3a). Filters to tiktok.com
    cookies (Playwright returns cookies for every domain the context touched —
    CDN/analytics domains must not enter the session file), backs the current
    file up to .bak first, then writes Cookie-Editor format atomically. Any
    failure is logged and swallowed — a write-back must never fail the post —
    and no cookie values are ever logged.

    `from_cookie_session` guards the design's "session may be profile-based"
    case: a profile-fallback session also carries a sessionid cookie, so
    writing it out would create a cookie file and silently flip the slot to
    cookie-preferred auth. Only write back when the posting context actually
    came from the cookie file."""
    if not from_cookie_session:
        _log.info(f"[TikTok] Cookie write-back skipped for {account_key}: session is profile-based, not cookie-based.")
        return

    try:
        all_cookies = await context.cookies()
    except Exception as e:
        _log.warning(f"[TikTok] Warning: could not read cookies for write-back ({account_key}): {e}")
        return

    tiktok_cookies = [c for c in all_cookies if _is_tiktok_domain(c.get("domain") or "")]

    # No sessionid → don't overwrite a working session file with a
    # potentially profile-based or broken cookie set. Skip quietly.
    if not any(c.get("name") == "sessionid" for c in tiktok_cookies):
        _log.warning(f"[TikTok] Cookie write-back skipped for {account_key}: no tiktok.com sessionid cookie.")
        return

    cookie_file = _cookies_file(account_key)
    try:
        cookie_file.parent.mkdir(parents=True, exist_ok=True)
        # Single-depth backup; overwrite any existing .bak. Only touched when
        # the write itself is about to run, never on a failed write-back.
        if cookie_file.exists():
            shutil.copyfile(cookie_file, cookie_file.with_name(cookie_file.name + ".bak"))
        editor_cookies = [_playwright_to_cookie_editor(c) for c in tiktok_cookies]
        # Atomic write: serialize to a temp file in the same directory, then
        # os.replace() it onto the live file. A crash mid-write can only ever
        # damage the temp file — the live session file is either the old bytes
        # or the fully-written new bytes, never a truncated mix (review fix).
        tmp_file = cookie_file.with_name(cookie_file.name + ".tmp")
        try:
            with open(tmp_file, "w") as f:
                json.dump(editor_cookies, f, indent=2)
            os.replace(tmp_file, cookie_file)
        except Exception:
            # Clean up the partial temp file; leave the live file untouched.
            try:
                tmp_file.unlink(missing_ok=True)
            except Exception:
                pass
            raise
        _log.info(f"[TikTok] Wrote back {len(editor_cookies)} cookies for {account_key}.")
    except Exception as e:
        _log.warning(f"[TikTok] Warning: cookie write-back failed for {account_key}: {e}")


async def _get_context_from_cookies(playwright, account_key: str, headless: bool = True):
    """Create a browser context and load exported cookies. No persistent profile needed."""
    cookie_file = _cookies_file(account_key)
    if not _is_usable_cookie_file(cookie_file):
        raise ValueError(f"No safe usable TikTok cookie session exists for {account_key!r}.")
    browser = await playwright.chromium.launch(
        channel="chrome",
        headless=headless,
        args=[
            # LEGACY, NOT COVERAGE — see instagram_browser._get_context for the
            # full note. Kept as harmless; does not address current detection.
            "--disable-blink-features=AutomationControlled",
            "--disable-infobars",
            # --- CRITICAL VIDEO RENDERING FIXES ---
            "--ignore-gpu-blocklist",             # Overrides default restrictions on hardware acceleration
            "--enable-gpu-rasterization",         # Offloads interface drawing to your physical GPU
            "--enable-zero-copy",                 # Drastically reduces video memory consumption 
            "--disable-gpu-sandbox",              # Allows the browser threads to talk directly to your graphics card
            "--force-gpu-rasterization"           # Ensures UI layers do not fall back to crashing CPU engines
        ],
    )
    context = await browser.new_context(
        viewport={"width": 1280, "height": 900},
        # No user_agent override — see instagram_browser._get_context for why a
        # hardcoded UA desynchronises the client-hint surfaces.
    )

    # Load cookies from exported JSON. Cookie-Editor exports a slightly
    # different format than Playwright expects — normalize the objects.
    with open(cookie_file) as f:
        raw_cookies = json.load(f)

    cookies = _normalize_cookies(raw_cookies)

    await context.add_cookies(cookies)

    return context, browser


async def _get_context(playwright, account_key: str, headless: bool = True):
    """Get a browser context for the given account, returning
    (context, browser). Prefers cookie-based auth, falls back to persistent
    profile. The browser handle is the standalone Chromium process for the
    cookie path (callers must close it to avoid leaking a Chrome), or None for
    the persistent-profile path (closing the context closes that browser)."""

    # Prefer cookie-based approach (no automation fingerprint issues)
    if has_cookie_session(account_key):
        context, browser = await _get_context_from_cookies(playwright, account_key, headless)
        return context, browser

    # Fallback: persistent profile
    session_dir = SESSIONS_DIR / account_key
    session_dir.mkdir(exist_ok=True)
    context = await playwright.chromium.launch_persistent_context(
        user_data_dir=str(session_dir),
        headless=headless,
        channel="chrome",
        viewport={"width": 1280, "height": 900},
        # No user_agent override — see instagram_browser._get_context.
        args=[
            "--disable-blink-features=AutomationControlled",
        ],
    )
    # Persistent context owns its own browser; no separate handle to close.
    return context, None


def _find_system_chrome() -> str | None:
    """Find the system-installed Chrome/Chromium executable."""
    import platform
    import shutil

    system = platform.system()
    if system == "Darwin":  # macOS
        candidates = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
        ]
    elif system == "Linux":
        candidates = [
            shutil.which("google-chrome"),
            shutil.which("google-chrome-stable"),
            shutil.which("chromium"),
            shutil.which("chromium-browser"),
        ]
    elif system == "Windows":
        local = os.environ.get("LOCALAPPDATA", "")
        progfiles = os.environ.get("PROGRAMFILES", "")
        # pathlib to match the rest of the module (#42/BE-15). This branch is
        # unreachable on the maintainer's macOS machine and sits inside a
        # live-posting module, which is why it was deferred out of Batch 3 —
        # the equivalence claim could be argued but not executed. It can be
        # executed now: test_windows_chrome_lookup.py fakes the platform and
        # asserts these paths against os.path.join from any host OS.
        #
        # The tail stays a single raw literal rather than being split into
        # Path("Google") / "Chrome" / ... — splitting it emits forward slashes
        # on a POSIX host, so the test above could no longer compare against
        # os.path.join. Written this way the produced *string* is identical to
        # the old code on both POSIX and Windows. (On Windows the literal is
        # still parsed into separate path components — WindowsPath treats the
        # backslashes as separators — but it renders back to the same string,
        # which is what this function returns and what callers use.)
        chrome_exe = r"Google\Chrome\Application\chrome.exe"
        candidates = [
            str(Path(local) / chrome_exe),
            str(Path(progfiles) / chrome_exe),
        ]
    else:
        candidates = []

    for c in candidates:
        if c and Path(c).exists():
            return str(c)
    return None


async def login(account_key: str):
    """
    Open a visible browser window for manual login.
    Uses system Chrome instead of Playwright's Chromium to avoid TikTok bot detection.
    Falls back to Playwright Chromium if system Chrome isn't found.
    """
    async with async_playwright() as pw:
        session_dir = SESSIONS_DIR / account_key
        had_profile_before = session_dir.exists() and any(session_dir.iterdir())
        session_dir.mkdir(exist_ok=True)

        chrome_path = _find_system_chrome()
        browser = None

        if chrome_path:
            _log.info(f"[TikTok] Using system Chrome: {chrome_path}")
            context = await pw.chromium.launch_persistent_context(
                user_data_dir=str(session_dir),
                headless=False,
                executable_path=chrome_path,
                viewport={"width": 1280, "height": 900},
                args=[
                    "--disable-blink-features=AutomationControlled",
                ],
            )
        else:
            _log.warning("[TikTok] System Chrome not found, using Playwright Chromium (may trigger bot detection)")
            context, browser = await _get_context(pw, account_key, headless=False)

        page = context.pages[0] if context.pages else await context.new_page()

        await page.goto("https://www.tiktok.com/login")
        print(f"\n[TikTok] Log in to account '{account_key}' in the browser window.")
        print("Once logged in and you see your feed, press Enter here to save the session")
        print("(or type 'abort' + Enter to discard if the login didn't work)...")
        answer = await asyncio.get_event_loop().run_in_executor(None, input)
        await context.close()
        # The cookie-path _get_context returns a standalone browser to close too.
        if browser:
            await browser.close()
        if _resolve_login_outcome(session_dir, answer, had_profile_before):
            print(f"[TikTok] Session saved for '{account_key}'.")
        else:
            print(f"[TikTok] Login aborted — discarded partial session for '{account_key}'.")


# ---------------------------------------------------------------------------
# The posting flow, in named steps
#
# Originally decomposed in BE-4 (#28 in the RicePoster repo); the upload
# wait, confirmation observation and diagnostics were added in RiceSuite #28.
# tests/golden/tt_*.trace records the intended current flow.
#
# CLAUDE.md § Intentional design protects the long sleeps, the generous
# timeouts and the fallback selector chains. Note also that TikTok's one-time
# per-account dialogs are dismissed manually by the maintainer, deliberately:
# _describe_blocking_modal names the blocker, it does not clear it.
# ---------------------------------------------------------------------------


# The upload page can still be redirecting after domcontentloaded (to
# TikTok Studio, or to a login page), so the file is sent only once the URL
# has held still (RiceSuite #62). The fixed 5s sleep this replaced stays the
# floor (CLAUDE.md § Intentional design). The cap only bounds a page that
# keeps navigating: reaching it is logged, not a failure, so a URL TikTok
# keeps rewriting cannot block posting.
UPLOAD_PAGE_SETTLE_FLOOR_S = 5.0
UPLOAD_PAGE_STABLE_S = 2.0
UPLOAD_PAGE_POLL_S = 0.5
UPLOAD_PAGE_SETTLE_CAP_S = 15.0


async def _await_url_settled(page: Page, account_key: str) -> str:
    """Return page.url once it has been unchanged for UPLOAD_PAGE_STABLE_S
    and the floor has passed, or whatever it is at the cap.

    State observation, not a user action, so it polls with plain sleeps
    like _await_upload_ready rather than jittered ones.
    """
    started = monotonic()
    url = page.url
    changed_at = started
    while True:
        now = monotonic()
        if now - started >= UPLOAD_PAGE_SETTLE_FLOOR_S and now - changed_at >= UPLOAD_PAGE_STABLE_S:
            _log.info(f"[TikTok] {account_key}: upload page settled on {_diagnostic_url(url)}.")
            return url
        if now - started >= UPLOAD_PAGE_SETTLE_CAP_S:
            _log.warning(
                f"[TikTok] {account_key}: upload page still navigating after "
                f"{UPLOAD_PAGE_SETTLE_CAP_S:g}s (now {_diagnostic_url(url)}); continuing."
            )
            return url
        await asyncio.sleep(UPLOAD_PAGE_POLL_S)
        current = page.url
        if current != url:
            url, changed_at = current, monotonic()


async def _open_upload_page(page: Page, account_key: str) -> str:
    """Load the upload page and return the URL it settled on."""
    # domcontentloaded completely ignores endless background tracking loops
    await page.goto("https://www.tiktok.com/upload", wait_until="domcontentloaded", timeout=60000)
    await page.wait_for_load_state("domcontentloaded")
    return await _await_url_settled(page, account_key)


def _check_upload_page_session(settled_url: str, account_key: str):
    """Fail fast if the settled upload page is TikTok's login wall."""
    if url_matches_login_markers(settled_url, LOGIN_REDIRECT_MARKERS):
        raise Exception(
            f"Session expired for {account_key}. Run login again: "
            f"python -m backend.session_manager login tiktok {account_key}"
        )


async def _resolve_upload_target(page: Page):
    """TikTok's upload page sometimes nests the panel in an iframe. Return
    whichever object subsequent element lookups must be addressed to."""
    iframe = None
    try:
        iframe_el = await page.wait_for_selector(
            "iframe[src*='upload']", timeout=10000
        )
        iframe = await iframe_el.content_frame()
        _log.info("[TikTok] Intercepted upload panel inside iframe view context.")
    except Exception:
        _log.info("[TikTok] No iframe detected, interacting directly with main page layout.")
        iframe = page

    return iframe or page


async def _upload_media_file(target, upload_path: Path):
    """Send the short-named copy to the picker and wait out processing."""
    file_input = await target.wait_for_selector(
        "input[type='file']",
        state="attached",
        timeout=15000
    )
    await file_input.set_input_files(str(upload_path))
    _log.info("[TikTok] Media file sent to picker layer. Upload continues in the background.")
    await asyncio.sleep(5)

    # Not proof the upload finished: _await_upload_ready waits for that
    # before Post is clicked.
    _log.info("[TikTok] Editor fields ready; entering the caption while the upload runs.")


async def _dismiss_one_time_overlay(page: Page):
    """Clear the 'Got it' feature-promo overlay when it is present.

    This is NOT the content-check dialog: that one is dismissed manually by
    the maintainer by deliberate decision after the 2026-07-18 incident, and
    _describe_blocking_modal only reports it.
    """
    try:
        _log.info("[TikTok] Dismissing layout overlays...")
        # Wait up to 5 seconds for the popup to finish its slide-in animation
        got_it_btn = page.locator("button:has-text('Got it'), div[role='button']:has-text('Got it')").first
        await got_it_btn.wait_for(state="visible", timeout=5000)
        await got_it_btn.click()
        _log.info("[TikTok] Successfully clicked 'Got it' overlay.")
        await asyncio.sleep(1.0)
    except Exception as e:
        # INFO, not WARNING: the "Got it" promo is a one-time dialog, so it is
        # absent on virtually every post and this timeout is the ordinary
        # path, not a degraded one. Warning here put a Playwright timeout dump
        # on every successful TikTok post at LOG_LEVEL=WARNING — the setting
        # meant for unattended batches — which buries the real warnings.
        # Matches _resolve_upload_target's structurally identical
        # "No iframe detected" expected-miss (cold review, 2026-07-31).
        _log.info(f"[TikTok] Overlay clearance check complete (No buttons clicked: {e})")


async def _find_caption_field(page: Page):
    """Resolve the caption editor, naming the whole chain if nothing matches."""
    caption_attempts: list[tuple[str, int | None]] = []
    for selector in [
        "div[contenteditable='true']",
        "div[data-placeholder*='caption']",
        "div[data-placeholder*='title']",
        "div.public-DraftEditor-content",
        "[class*='editor-container'] div[role='textbox']"
    ]:
        try:
            loc = page.locator(selector)
            count = await loc.count()
            caption_attempts.append((selector, count))
            if count > 0:
                return loc.first
        except Exception:
            caption_attempts.append((selector, None))
            continue

    raise Exception(_selector_chain_error("caption field", caption_attempts))


async def _enter_and_verify_caption(page: Page, caption_field, caption: str, account_key: str):
    """Clear, insert atomically, read back, and refuse to post garbled text.

    TikTok pre-fills this field with the video filename, and the editor keeps
    internal state that survives DOM wipes. Clear with real keyboard input,
    insert the caption atomically (no per-key events for '#'/'@' means no
    autocomplete interception), then read the field back and verify before
    ever clicking Post. The retry types sequentially instead.
    """
    await caption_field.scroll_into_view_if_needed()

    last_seen = ""
    for attempt in range(2):
        await caption_field.focus()
        await caption_field.click()
        await asyncio.sleep(0.5)

        # Editor-level clear — select-all + Delete goes through the editor
        # model, unlike setting textContent
        await page.keyboard.press("ControlOrMeta+A")
        await page.keyboard.press("Delete")
        await asyncio.sleep(0.5)

        if attempt == 0:
            await page.keyboard.insert_text(caption)
        else:
            _log.warning("[TikTok] Caption mismatch — retrying with sequential typing...")
            await type_with_jitter(caption_field, caption)
        await asyncio.sleep(0.5)

        # Dispatch lifecycle events inside the targeted window tree to bind state data securely
        await page.evaluate("""() => {
            const selectors = [
                'div[contenteditable="true"]',
                'div[data-placeholder*="caption"]',
                'div.public-DraftEditor-content'
            ];
            let field = null;
            for (const sel of selectors) {
                field = document.querySelector(sel);
                if (field) break;
            }
            if (field) {
                field.dispatchEvent(new Event('input', { bubbles: true, cancelable: true }));
                field.dispatchEvent(new Event('change', { bubbles: true, cancelable: true }));
                field.dispatchEvent(new Event('blur', { bubbles: true }));
                field.focus();
            }
        }""")
        await asyncio.sleep(1.5)

        last_seen = await caption_field.inner_text()
        if _captions_match(caption, last_seen):
            _log.info("[TikTok] Caption verified in editor.")
            return
    raise Exception(
        f"TikTok caption verification failed for {account_key}: editor text does not "
        f"match the intended caption after 2 attempts — aborting before posting "
        f"garbled text. {EDITOR_MARKER}: {last_seen[:200]!r}"
    )


POST_BUTTON_WAIT_S = 30.0


def _base_post_button(page: Page):
    return page.locator("button:has-text('Post')").filter(
        has_not=page.locator("button:has-text('Cancel'), button:has-text('Save')")
    ).last


async def _await_upload_ready(page: Page, account_key: str):
    """Wait until Post is enabled: TikTok keeps it disabled until the upload
    finishes. Before #28 the flow slept 5s and clicked, so any upload that
    outlasted Playwright's 30s click timeout failed.

    A timeout here fails the post before Post is ever clicked, so a retry
    cannot duplicate it.
    """
    post_btn = _base_post_button(page)
    started = monotonic()
    deadline = started + TT_UPLOAD_TIMEOUT_S
    next_log = started + 30
    seen = False
    while True:
        try:
            if await post_btn.is_enabled(timeout=1000):
                _log.info(f"[TikTok] {account_key}: upload finished; Post is enabled.")
                return
            seen = True
        except PlaywrightTimeoutError:
            # Post not rendered. Briefly that is normal; for long it is a
            # layout change, not a slow upload, so do not wait out the cap.
            if not seen and monotonic() - started >= POST_BUTTON_WAIT_S:
                raise Exception(
                    f"TikTok Post button not found within {int(POST_BUTTON_WAIT_S)}s "
                    f"(page layout may have changed); nothing was posted"
                )
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise Exception(
                f"TikTok Post stayed disabled for {TT_UPLOAD_TIMEOUT_S}s: the upload did not "
                f"finish or TikTok is holding Post; nothing was posted"
            )
        if monotonic() >= next_log:
            _log.info(f"[TikTok] {account_key}: upload still in progress ({int(remaining)}s remaining).")
            next_log = monotonic() + 30
        await asyncio.sleep(min(1.0, remaining))


async def _recheck_caption(page: Page, caption_field, caption: str, account_key: str):
    """The caption went in while the upload ran; make sure the editor still
    holds it now that the upload is done. Re-enter once, or refuse to post."""
    if _captions_match(caption, await caption_field.inner_text()):
        return
    _log.warning("[TikTok] Caption changed while the upload finished; entering it again...")
    await _enter_and_verify_caption(page, caption_field, caption, account_key)


async def _click_post_button(page: Page):
    """Click the base "Post" button at the bottom of the editing panel.

    The chain deliberately has no failure branch — it clicks whatever it
    finds. That positional heuristic is tracked separately in issue #22; it
    is not a diagnostics gap (see test_tiktok_post_button_chain_is_out_of_scope).
    """
    _log.info("[TikTok] Caption finalized. Locating the base Publish/Post button...")

    post_btn = _base_post_button(page)

    if await post_btn.count() == 0:
        post_btn = page.locator("[class*='button']").aria_role("button", name="Post").first

    await post_btn.scroll_into_view_if_needed()
    await post_btn.click()
    _log.info("[TikTok] Base Post action button clicked. Monitoring for confirmation modal...")
    await asyncio.sleep(1.5)


TT_CONFIRM_MODAL_POST = (
    "div[class*='modal'] button:has-text('Post'), "
    "div[class*='TUXModal'] button:has-text('Post'), "
    "[class*='dialog'] button:has-text('Post'), "
    "[role='dialog'] button:has-text('Post')"
)


async def _confirm_post_modal(page: Page):
    """Handle the secondary confirmation modal if it slides up.

    Only a Post button inside a modal or dialog counts. The page-wide
    fallback this replaced (#28) re-clicked the last Post button on the
    page, which with no modal open is the base Post button itself.
    TUXModal is TikTok's modal class; `[class*='modal']` is case-sensitive
    and misses it. This still relies on the modal's markup: a confirmation
    dialog matching none of these is not clicked, and the result is then
    unconfirmed rather than a duplicate.
    """
    try:
        confirm_modal_btn = page.locator(TT_CONFIRM_MODAL_POST).first

        if await confirm_modal_btn.count() > 0:
            _log.info("[TikTok] Final confirmation modal detected. Clicking final 'Post' switch...")
            await confirm_modal_btn.click()
            _log.info("[TikTok] Final confirmation action deployed successfully.")
    except Exception as e:
        _log.warning(f"[TikTok] Confirmation modal bypass step notice: {e}")


# Studio dashboard after the redirect; checked on the page, because a
# redirect leaves any upload iframe behind.
TT_DASHBOARD = (
    "input[placeholder*='Search for post description'], "
    "span:has-text('Posts (Created on)'), "
    "div:has-text('Post successfully uploaded')"
)
# In-page success banners, checked on the upload target. Specific phrases
# only: before Post, the upload page itself says "Checks can only start
# after the file is uploaded", which the old bare 'uploaded' match counted
# as success (#28).
TT_UPLOAD_BANNER_TEXTS = ("is being uploaded", "are being uploaded", "Your video has been uploaded")
TT_UPLOAD_BANNER = ", ".join(f"div:has-text('{text}')" for text in TT_UPLOAD_BANNER_TEXTS)


async def _await_upload_confirmation(page: Page, target, account_key: str) -> bool:
    """True only when a success signal was actually observed.

    Observes the dashboard, the in-page banner and a redirect away from
    /upload throughout TT_UPLOAD_TIMEOUT_S, instead of the old fixed 45s,
    15s and 10s windows. Unknown at the cap means unconfirmed, never
    success and never a retry.
    """
    _log.info("[TikTok] Holding execution. Waiting for upload confirmation or dashboard redirect...")
    deadline = monotonic() + TT_UPLOAD_TIMEOUT_S
    next_log = monotonic() + 30
    while True:
        if await page.locator(TT_DASHBOARD).first.is_visible():
            _log.info("[TikTok] Server confirmation packet and dashboard redirect validated successfully!")
            return True
        # Redirected away from upload after Post: it likely succeeded. A
        # login bounce is not success (its redirect_url hides '/upload').
        # Checked before the frame, which a redirect detaches.
        url = page.url
        if "/upload" not in url and not url_matches_login_markers(url, LOGIN_REDIRECT_MARKERS):
            return True
        if target is not page:
            try:
                if await target.locator(TT_UPLOAD_BANNER).first.is_visible():
                    return True
            except Exception:
                pass  # Detached upload frame; the page-level signals decide.
        elif await page.locator(TT_UPLOAD_BANNER).first.is_visible():
            return True
        remaining = deadline - monotonic()
        if remaining <= 0:
            return False
        if monotonic() >= next_log:
            _log.info(f"[TikTok] {account_key}: still awaiting post confirmation ({int(remaining)}s remaining).")
            next_log = monotonic() + 30
        await asyncio.sleep(min(1.0, remaining))


# Stages whose error text the debug JSON may hold (RiceSuite #62). From
# enter_caption on, an error can quote the editor, which holds the caption,
# so this names the earlier stages one by one: a stage added later records
# its error type only until it is listed here.
DIAGNOSTIC_ERROR_TEXT_STAGES = frozenset({
    "prepare_media", "browser_start", "browser_context", "browser_page",
    "open_upload_page", "resolve_target", "send_media", "dismiss_overlay",
    "find_caption",
})
DIAGNOSTIC_TEXT_MAX_CHARS = 200


def _diagnostic_url(url) -> str | None:
    """The URL without its query string, fragment or credentials.

    Keeps the scheme and host, so a failed navigation
    (chrome-error://chromewebdata/) or a blank page (about:blank) cannot be
    mistaken for a TikTok path.
    """
    if not url:
        return None
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    host = parts.netloc.rpartition("@")[2]
    return urlunsplit((parts.scheme, host, parts.path, "", ""))[:DIAGNOSTIC_TEXT_MAX_CHARS]


# A URL quoted in an error line, split before its query or fragment.
# Playwright quotes the URL that interrupted a navigation in full. The
# scheme is bounded so a long run of letters cannot backtrack quadratically.
_QUOTED_URL_QUERY = re.compile(r"([A-Za-z][A-Za-z0-9+.-]{0,31}://[^\s\"'?#]*)[?#][^\s\"']*")


def _error_first_line(error) -> str | None:
    """First line of the error, with the query and fragment dropped from any
    URL it quotes (as for page_url), cut to DIAGNOSTIC_TEXT_MAX_CHARS."""
    lines = str(error).strip().splitlines()
    if not lines:
        return None
    return _QUOTED_URL_QUERY.sub(r"\1", lines[0])[:DIAGNOSTIC_TEXT_MAX_CHARS]


async def _save_post_diagnostics(page, account_key, outcome, stage, started, timings, error=None,
                                 settled_url=None):
    """Local screenshot and bounded metadata; never persist captions or DOM.

    The URL is read before the screenshot, as close to the failure as
    possible; settled_url is where the settle wait ended, so the two show
    whether the URL changed afterwards. Error text is kept only for the
    stages listed in DIAGNOSTIC_ERROR_TEXT_STAGES.
    """
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    stem = DEBUG_DIR / f"debug_tt_post_{account_key}_{stamp}_{outcome}"
    try:
        page_url = _diagnostic_url(page.url) if page is not None else None
    except Exception:
        page_url = None
    metadata = {
        "timestamp_utc": stamp, "slot": account_key, "outcome": outcome,
        "stage": stage, "elapsed_s": round(monotonic() - started, 3),
        "stage_timings_s": timings, "upload_cap_s": TT_UPLOAD_TIMEOUT_S,
        "settled_url": _diagnostic_url(settled_url),
        "page_url": page_url,
        "error_type": type(error).__name__ if error else None,
        "error_message": (
            _error_first_line(error)
            if error and stage in DIAGNOSTIC_ERROR_TEXT_STAGES else None
        ),
    }
    try:
        if page is None:
            raise RuntimeError("Browser page unavailable")
        await page.screenshot(path=str(stem.with_suffix(".png")), timeout=5000)
    except Exception as exc:
        metadata["screenshot_error_type"] = type(exc).__name__
    try:
        stem.with_suffix(".json").write_text(json.dumps(metadata, indent=2) + "\n")
    except Exception as exc:
        _log.warning(f"[TikTok] Could not save diagnostics for {account_key}: {type(exc).__name__}")


async def _describe_blocking_modal(page: Page) -> str:
    """Return the text of a blocking TikTok dialog, or "" if none is open.

    One-time per-account dialogs (content checks, feature promos) sit in a
    TUXModal overlay and block all clicks. Naming the blocker in the error
    makes the fix obvious — observed live 2026-07-18 with "Turn on automatic
    content checks?". Deliberately reports rather than dismisses.
    """
    try:
        overlay = page.locator("div.TUXModal-overlay, div[class*='TUXModal']").first
        if await overlay.count() > 0 and await overlay.is_visible():
            return " ".join((await overlay.inner_text()).split())[:200]
    except Exception:
        pass
    return ""


async def post_media(
    account_key: str,
    media_path: Path,
    caption: str,
    media_type: str,
    headless: bool = True,
) -> str:
    """Post a video or photo to TikTok via the web upload page.

    A thin shell over the named steps above: session acquisition, the
    short-named upload copy, the step sequence, cookie write-back, failure
    diagnostics and cleanup.
    """
    # TikTok pre-fills the caption field with the uploaded file's name,
    # so upload a short-named copy — long descriptive filenames must
    # never be able to bleed into the caption
    upload_path = media_path.with_name(f"tt_upload_{account_key}{media_path.suffix or '.mp4'}")

    async with AsyncExitStack() as stack:
        context = None
        browser = None
        page = None
        started = monotonic()
        stage = "prepare_media"
        timings = {}
        post_attempted = False
        settled_url = None

        async def step(name, fn, *args):
            nonlocal stage
            stage = name
            began = monotonic()
            _log.info(f"[TikTok] {account_key}: starting {name}.")
            try:
                return await fn(*args)
            finally:
                timings[name] = round(monotonic() - began, 3)
                _log.info(f"[TikTok] {account_key}: {name} elapsed {timings[name]}s.")

        try:
            shutil.copyfile(media_path, upload_path)
            pw = await step("browser_start", stack.enter_async_context, async_playwright())
            used_cookie_session = has_cookie_session(account_key)
            if used_cookie_session:
                context, browser = await step("browser_context", _get_context_from_cookies, pw, account_key, headless)
            else:
                context, browser = await step("browser_context", _get_context, pw, account_key, headless)
            stage = "browser_page"
            page = context.pages[0] if context.pages else await step("browser_page", context.new_page)

            settled_url = await step("open_upload_page", _open_upload_page, page, account_key)
            _check_upload_page_session(settled_url, account_key)
            target = await step("resolve_target", _resolve_upload_target, page)
            await step("send_media", _upload_media_file, target, upload_path)
            await step("dismiss_overlay", _dismiss_one_time_overlay, page)

            caption_field = await step("find_caption", _find_caption_field, page)
            await step("enter_caption", _enter_and_verify_caption, page, caption_field, caption, account_key)
            await step("await_upload", _await_upload_ready, page, account_key)
            await step("recheck_caption", _recheck_caption, page, caption_field, caption, account_key)

            # Set before dispatch: a click can reach TikTok and then raise.
            # After this boundary an error must never invite a retry.
            post_attempted = True
            await step("post", _click_post_button, page)
            await step("confirm_modal", _confirm_post_modal, page)

            confirmed = await step("confirmation", _await_upload_confirmation, page, target, account_key)

            if not confirmed:
                # Do NOT report success we didn't observe — the post may or
                # may not be live; the caller/UI shows this as unconfirmed
                _log.warning(f"[TikTok] Warning: no post confirmation seen for {account_key} within {TT_UPLOAD_TIMEOUT_S}s — result unconfirmed.")
                await _save_post_diagnostics(page, account_key, "unconfirmed", stage, started, timings,
                                             settled_url=settled_url)

            # Write refreshed cookies back so the session self-sustains
            # (DESIGN-scheduling.md §3a). Only reached when the post did not
            # raise; _write_back_cookies never raises, so a failed write-back
            # can never fail the post. Gated on cookie-session origin so a
            # profile-fallback post never creates a cookie file and flips the
            # slot's auth mode.
            await _write_back_cookies(context, account_key, from_cookie_session=used_cookie_session)

            return _post_id("tt_post", account_key, confirmed)

        except Exception as e:
            # Playwright raises its TimeoutError from a click only when the
            # pre-click checks never passed (an overlay in the way), so the
            # click was never dispatched and nothing was posted.
            uncertain = post_attempted and not (
                stage == "post" and isinstance(e, PlaywrightTimeoutError)
            )
            await _save_post_diagnostics(
                page, account_key, "unconfirmed" if uncertain else "failed",
                stage, started, timings, e, settled_url=settled_url,
            )
            modal_text = await _describe_blocking_modal(page) if page is not None else ""

            if uncertain:
                _log.warning(
                    f"[TikTok] {account_key}: interrupted during {stage}; result unconfirmed "
                    f"({type(e).__name__}){f'; dialog open: {modal_text!r}' if modal_text else ''}."
                )
                return _post_id("tt_post", account_key, False)

            if modal_text:
                raise Exception(
                    f"TikTok post failed for {account_key}: blocked by a TikTok dialog: "
                    f"\"{modal_text}\" — log into this account in a normal browser, dismiss "
                    f"the dialog once, then retry. Original error: {e}"
                ) from e
            raise Exception(f"TikTok post failed for {account_key}: {e}") from e
        finally:
            try:
                upload_path.unlink(missing_ok=True)
            except Exception:
                pass
            # A failed close must not mask the real posting error
            if context is not None:
                try:
                    await context.close()
                except Exception as close_err:
                    _log.warning(f"[TikTok] Warning: context cleanup failed: {close_err}")
            if browser:
                try:
                    await browser.close()
                except Exception as close_err:
                    _log.warning(f"[TikTok] Warning: browser cleanup failed: {close_err}")
