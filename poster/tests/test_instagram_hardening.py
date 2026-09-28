"""Slow-network regressions using a virtual clock and a stateful fake page."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from backend import instagram_browser as ig


class Clock:
    now = 0.0

    async def sleep(self, seconds, *args):
        self.now += seconds


class Control:
    def __init__(self, page, kind):
        self.page, self.kind = page, kind
        self.first = self
        self.last = self

    def filter(self, **kwargs):
        return self

    def locator(self, *args, **kwargs):
        return self

    async def count(self):
        return int(await self.is_visible())

    async def is_visible(self):
        p = self.page
        if self.kind == 'modal':
            return p.opened or (p.direct and p.created_at is not None and p.clock.now >= p.created_at + 4)
        if self.kind == 'create':
            return True
        if self.kind == 'post':
            return p.menu
        if self.kind == 'success':
            return p.clock.now >= p.success_at
        if self.kind == 'error':
            return p.rejected
        return False

    async def hover(self):
        if self.kind == 'post':
            self.page.menu = False

    async def click(self, **kwargs):
        p = self.page
        if self.kind == 'create':
            p.creates += 1
            p.created_at = p.clock.now
            p.menu = not p.direct
        elif self.kind == 'post':
            p.post_clicks += 1
            if not p.menu or (p.vanish_once and p.post_clicks == 1):
                p.menu = False
                raise PlaywrightTimeoutError('Post disappeared')
            p.opened = True
            p.menu = False

    async def wait_for(self, *, timeout, **kwargs):
        deadline = self.page.clock.now + timeout / 1000
        while not await self.is_visible():
            if self.page.clock.now >= deadline:
                raise PlaywrightTimeoutError('not visible')
            await self.page.clock.sleep(0.25)


class Page:
    url = 'https://www.instagram.com/'

    def __init__(self, clock, *, success_at=float('inf'), direct=False, vanish_once=False):
        self.clock = clock
        self.success_at = success_at
        self.direct = direct
        self.vanish_once = vanish_once
        self.menu = self.opened = self.rejected = False
        self.created_at = None
        self.creates = self.post_clicks = 0

    def locator(self, selector, **kwargs):
        kind = ('modal' if 'Select from computer' in selector else
                'create' if 'New post' in selector else
                'success' if 'Animated checkmark' in selector else
                'error' if 'could not be shared' in selector else 'post')
        return Control(self, kind)

    def get_by_text(self, text, **kwargs):
        return Control(self, 'post')

    async def wait_for_selector(self, selector, timeout):
        control = self.locator(selector)
        await control.wait_for(timeout=timeout)
        return control


@pytest.fixture
def clock(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(ig, 'monotonic', lambda: clock.now, raising=False)
    monkeypatch.setattr(ig, 'sleep_jittered', clock.sleep)
    monkeypatch.setattr(ig.asyncio, 'sleep', clock.sleep)
    return clock


def test_confirmation_arriving_after_old_three_minute_limit_is_observed(clock):
    page = Page(clock, success_at=240)
    assert asyncio.run(ig._await_post_confirmation(page, 'A')) is True
    assert clock.now == 240


def test_confirmation_wait_uses_full_cap_without_unchecked_grace_sleep(clock):
    page = Page(clock)
    assert asyncio.run(ig._await_post_confirmation(page, 'A')) is False
    assert clock.now == 450


def test_disappearing_post_menu_is_reopened_before_retry(clock):
    page = Page(clock, vanish_once=True)
    asyncio.run(ig._open_create_post(page))
    assert page.opened
    assert page.creates == 2
    assert page.post_clicks == 2


def test_delayed_direct_dialog_does_not_require_post_menu(clock):
    page = Page(clock, direct=True)
    asyncio.run(ig._open_create_post(page))
    assert asyncio.run(page.locator('Select from computer').is_visible())
    assert page.creates == 1
    assert page.post_clicks == 0


@pytest.mark.parametrize('interruption', [None, 'share', 'confirmation'])
def test_unconfirmed_saves_diagnostics_before_browser_close(monkeypatch, tmp_path, allow_browser_post_media, interruption):
    events = []
    page = SimpleNamespace(screenshot=AsyncMock(side_effect=lambda **kw: events.append('screenshot')))
    context = SimpleNamespace(pages=[page], close=AsyncMock(side_effect=lambda: events.append('close')))
    class PW:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
    monkeypatch.setattr(ig, 'async_playwright', PW)
    monkeypatch.setattr(ig, '_get_context', AsyncMock(return_value=context))
    monkeypatch.setattr(ig, 'DEBUG_DIR', tmp_path)
    for name in ('_open_instagram', '_dismiss_popups', '_browse_feed', '_open_create_post',
                 '_upload_media_file', '_dismiss_aspect_ratio_warning', '_select_original_crop',
                 '_advance_past_edit_screens', '_find_caption_field', '_enter_caption', '_share_post'):
        monkeypatch.setattr(ig, name, AsyncMock())
    monkeypatch.setattr(ig, '_await_post_confirmation', AsyncMock(return_value=False))
    if interruption:
        helper = '_share_post' if interruption == 'share' else '_await_post_confirmation'
        getattr(ig, helper).side_effect = RuntimeError('browser closed after dispatch')
    assert asyncio.run(ig.post_media('A', tmp_path / 'video.mp4', 'caption', 'reel')) == 'ig_post_unconfirmed_A'
    assert events == ['screenshot', 'close']
    ig._share_post.assert_awaited_once()
    assert list(tmp_path.glob('*.json'))


def test_explicit_rejection_stops_waiting(clock):
    page = Page(clock)
    page.rejected = True
    with pytest.raises(ig.InstagramShareRejected):
        asyncio.run(ig._await_post_confirmation(page, 'A'))
    assert clock.now == 0


def test_configured_confirmation_cap_is_respected(monkeypatch, clock):
    monkeypatch.setattr(ig, 'IG_UPLOAD_TIMEOUT_S', 17)
    assert asyncio.run(ig._await_post_confirmation(Page(clock), 'A')) is False
    assert clock.now == 17


def test_success_at_cap_is_observed(clock):
    assert asyncio.run(ig._await_post_confirmation(Page(clock, success_at=450), 'A')) is True
    assert clock.now == 450


def test_menu_recovery_is_bounded(monkeypatch, clock):
    page = Page(clock)
    original = Control.click
    async def disappear(control, **kwargs):
        if control.kind == 'post':
            page.menu = False
            page.post_clicks += 1
            raise PlaywrightTimeoutError('menu disappeared')
        await original(control, **kwargs)
    monkeypatch.setattr(Control, 'click', disappear)
    with pytest.raises(Exception, match='after 3 attempts'):
        asyncio.run(ig._open_create_post(page))
    assert page.creates == page.post_clicks == 3


def test_click_that_opens_dialog_then_times_out_does_not_reopen(monkeypatch, clock):
    page = Page(clock)
    original = Control.click
    async def click_then_timeout(control, **kwargs):
        await original(control, **kwargs)
        if control.kind == 'post':
            raise PlaywrightTimeoutError('acknowledgement lost')
    monkeypatch.setattr(Control, 'click', click_then_timeout)
    asyncio.run(ig._open_create_post(page))
    assert page.opened
    assert page.creates == page.post_clicks == 1


@pytest.mark.parametrize('outcome', ['unconfirmed', 'failed'])
def test_diagnostics_are_unique_and_record_timing_without_page_content(monkeypatch, tmp_path, clock, outcome):
    import json
    monkeypatch.setattr(ig, 'DEBUG_DIR', tmp_path)
    page = SimpleNamespace(screenshot=AsyncMock())
    for _ in range(2):
        asyncio.run(ig._save_post_diagnostics(page, 'A', outcome, 'confirmation', 0,
                                            {'confirmation': 450}, RuntimeError('private caption')))
    paths = list(tmp_path.glob('*.json'))
    assert len(paths) == 2
    for path in paths:
        data = json.loads(path.read_text())
        assert data['stage_timings_s'] == {'confirmation': 450}
        assert data['stage'] == 'confirmation'
        assert data['confirmation_cap_s'] == 450
        assert data['outcome'] == outcome
        assert data['error_type'] == 'RuntimeError'
        assert 'private caption' not in path.read_text()


def test_screenshot_failure_still_saves_metadata(monkeypatch, tmp_path, clock):
    import json
    monkeypatch.setattr(ig, 'DEBUG_DIR', tmp_path)
    page = SimpleNamespace(screenshot=AsyncMock(side_effect=RuntimeError('closed')))
    asyncio.run(ig._save_post_diagnostics(page, 'A', 'unconfirmed', 'confirmation', 0, {}))
    data = json.loads(next(tmp_path.glob('*.json')).read_text())
    assert data['screenshot_error_type'] == 'RuntimeError'
