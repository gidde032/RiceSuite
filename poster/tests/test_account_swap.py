"""Swapping accounts keeps the drafts already in their slots (RiceSuite #19).

A draft (media, caption, topic, frame) lives only in the browser, keyed by
account id. Switching accounts used to delete every leaving account's draft
behind a discard prompt, so a pulled batch loaded into a, b, c could not be
pointed at d, e, f without re-adding the media. Now an account leaving hands
its draft to the account joining in its place; only a draft left with no
joining account is at risk, and only that one is named in the prompt.

The behaviour tests execute the real page functions in node against a stubbed
DOM, like `test_platform_toggles.py`. The rendered test drives the real page in
Chrome with every /api call faked (nothing touches a server, queue, session or
account state), like `test_slot_header_layout.py`, and skips without Chrome.
"""

import json
import shutil
import subprocess

import pytest

from tests.test_frontend_robustness import _function_body, _html, _script

# Page functions the account-change path runs. A name missing from the page is
# left out rather than failing the extraction, so these tests fail on
# behaviour, not on a helper that did not exist before #19.
_FUNCTIONS = (
    "emptySlot", "currentActiveIds", "hasDraft", "planDraftTransfers",
    "draftWorkBusy", "applyActiveAccounts", "toggleAccount", "moveAccount",
    "replaceAccount", "selectRoster", "clearSlotPreview", "accountLabel",
    "isPlatformEnabled", "enabledPlatforms", "buildSlotsPayload", "slotOf",
)

_PRELUDE = """
const PLATFORMS = ['instagram', 'tiktok'];
const log = { confirms: [], alerts: [], status: [], persisted: 0 };
let confirmAnswer = true;
globalThis.confirm = msg => { log.confirms.push(msg); return confirmAnswer; };
globalThis.alert = msg => { log.alerts.push(msg); };
let draftWork = 0;
let pullInFlight = false;
let persistDelay = 0;
let accountChangeInFlight = false;
async function persistAccountState() {
  log.persisted += 1;
  if (persistDelay) await new Promise(r => setTimeout(r, persistDelay));
}
function renderSlots() {}
function renderSessionStrip() {}
function renderAccounts() {}
function updateButtons() {}
function setTransferStatus(message) { log.status.push(message); }
function resetSlotMenus() {}
const names = { A: 'Alpha', B: 'Beta', C: 'Gamma', D: 'Delta', E: 'Echo', F: 'Foxtrot' };
const state = {
  accounts: [], slots: {}, sessions: {}, postMode: 'mock',
  deviceProfileCapacity: 9, defaultCaptionStyle: 'generic', selectedRoster: '',
  availableAccounts: Object.keys(names).map(id => ({ slot: id, account_id: id, name: names[id] })),
  accountState: {
    active_account_ids: [], rosters: {}, device_profiles: {}, disabled_platforms: {},
    caption_defaults: { A: 'generic', B: 'generic', C: 'generic', D: 'funny', E: 'generic', F: 'generic' },
  },
};
function activate(ids) {
  state.accounts = ids.map(id => ({ slot: id, account_id: id, name: names[id] }));
  state.accountState.active_account_ids = [...ids];
  for (const id of ids) state.slots[id] = { ...emptySlot(), style: 'generic' };
}
function draft(id, extra = {}) {
  Object.assign(state.slots[id], {
    filename: `${id}_clip.mp4`, mediaType: 'video', caption: `caption for ${id}`,
    topic: `topic ${id}`, thumb: `thumb-${id}`, previewUrl: `api/media/${id}_clip.mp4`,
  }, extra);
  return state.slots[id];
}
function snapshot() {
  return {
    active: state.accounts.map(a => a.slot),
    slots: Object.fromEntries(Object.entries(state.slots).map(([id, s]) =>
      [id, { filename: s.filename, caption: s.caption, topic: s.topic, thumb: s.thumb, style: s.style }])),
    payload: buildSlotsPayload().slots.map(s => [s.slot, s.filename, s.caption]),
    log,
  };
}
"""


def _node(driver: str) -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node needed to execute frontend helpers")
    script = _script()
    present = [n for n in _FUNCTIONS if f"function {n}(" in script]
    source = _PRELUDE + "\n".join(_function_head(n) + _function_body(n) for n in present)
    proc = subprocess.run(
        [node, "--input-type=module", "--eval",
         source + "\n(async () => {\n" + driver + "\nconsole.log(JSON.stringify(snapshot()));\n})();"],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def _function_head(name: str) -> str:
    """`[async ]function name(args) ` — the part `_function_body` leaves off."""
    script = _script()
    start = script.index(f"function {name}(")
    is_async = script[max(0, start - 6):start] == "async "
    head = script[start:script.index("{", script.index(")", start))]
    return ("async " if is_async else "") + head


# --- behaviour --------------------------------------------------------------


def test_roster_swap_moves_every_draft_to_the_account_that_replaces_it():
    """The reported case: a batch loaded into a, b, c is pointed at d, e, f."""
    out = _node("""
activate(['A', 'B', 'C']);
draft('A'); draft('B'); draft('C');
state.accountState.rosters.evening = ['D', 'E', 'F'];
await selectRoster('evening');
""")
    assert out["active"] == ["D", "E", "F"]
    assert set(out["slots"]) == {"D", "E", "F"}
    for old, new in (("A", "D"), ("B", "E"), ("C", "F")):
        moved = out["slots"][new]
        assert moved["filename"] == f"{old}_clip.mp4"
        assert moved["caption"] == f"caption for {old}"
        assert moved["topic"] == f"topic {old}"
        assert moved["thumb"] == f"thumb-{old}"
    # Nothing was lost, so nothing asked to discard.
    assert out["log"]["confirms"] == []
    # Post All would now target the new accounts with the moved media.
    assert out["payload"] == [
        ["D", "A_clip.mp4", "caption for A"],
        ["E", "B_clip.mp4", "caption for B"],
        ["F", "C_clip.mp4", "caption for C"],
    ]
    assert out["log"]["status"] and "Alpha → Delta" in out["log"]["status"][-1]


def test_accounts_in_both_rosters_keep_their_own_drafts():
    out = _node("""
activate(['A', 'B', 'C']);
draft('A'); draft('B'); draft('C');
state.accountState.rosters.mix = ['E', 'A', 'F'];
await selectRoster('mix');
""")
    assert out["active"] == ["E", "A", "F"]
    assert out["slots"]["A"]["filename"] == "A_clip.mp4"
    assert out["slots"]["E"]["filename"] == "B_clip.mp4"
    assert out["slots"]["F"]["filename"] == "C_clip.mp4"
    assert out["log"]["confirms"] == []


def test_replace_puts_the_new_account_in_the_same_place_with_the_draft():
    out = _node("""
activate(['A', 'B', 'C']);
draft('A'); draft('B'); draft('C');
await replaceAccount('B', 'E');
""")
    assert out["active"] == ["A", "E", "C"]
    assert out["slots"]["E"]["filename"] == "B_clip.mp4"
    assert out["slots"]["E"]["caption"] == "caption for B"
    assert "B" not in out["slots"]
    assert out["log"]["confirms"] == []


def test_moved_caption_text_is_kept_and_its_style_becomes_the_new_accounts_default():
    """Maintainer decision (#19, 2b): the text travels unchanged; the style
    switches so the next Regenerate writes for the receiving account."""
    out = _node("""
activate(['A', 'B', 'C']);
draft('A', { style: 'generic' });
await replaceAccount('A', 'D');
""")
    assert out["slots"]["D"]["caption"] == "caption for A"
    assert out["slots"]["D"]["style"] == "funny"


def test_shrinking_prompts_only_for_drafts_left_without_an_account():
    out = _node("""
activate(['A', 'B', 'C']);
draft('A'); draft('B'); draft('C');
state.accountState.rosters.pair = ['D', 'E'];
await selectRoster('pair');
""")
    assert len(out["log"]["confirms"]) == 1
    prompt = out["log"]["confirms"][0]
    assert "Gamma [C]" in prompt
    assert "Alpha" not in prompt and "Beta" not in prompt
    assert out["active"] == ["D", "E"]
    assert out["slots"]["D"]["filename"] == "A_clip.mp4"
    assert out["slots"]["E"]["filename"] == "B_clip.mp4"
    assert "C" not in out["slots"]


def test_declining_the_prompt_changes_nothing():
    out = _node("""
activate(['A', 'B', 'C']);
draft('A'); draft('B'); draft('C');
state.accountState.rosters.pair = ['D', 'E'];
confirmAnswer = false;
await selectRoster('pair');
""")
    assert out["active"] == ["A", "B", "C"]
    assert out["slots"]["C"]["filename"] == "C_clip.mp4"
    assert out["log"]["persisted"] == 0


def test_unticking_an_account_still_asks_before_discarding_its_draft():
    out = _node("""
activate(['A', 'B', 'C']);
draft('B');
toggleAccount('B', false);
await new Promise(r => setTimeout(r, 0));
""")
    assert len(out["log"]["confirms"]) == 1 and "Beta [B]" in out["log"]["confirms"][0]
    assert out["active"] == ["A", "C"]
    assert "B" not in out["slots"]


def test_reordering_keeps_each_draft_with_its_account():
    out = _node("""
activate(['A', 'B', 'C']);
draft('A'); draft('B');
moveAccount('A', 1);
await new Promise(r => setTimeout(r, 0));
""")
    assert out["active"] == ["B", "A", "C"]
    assert out["slots"]["A"]["filename"] == "A_clip.mp4"
    assert out["slots"]["B"]["filename"] == "B_clip.mp4"


@pytest.mark.parametrize("busy", ["draftWork = 1;", "pullInFlight = true;"])
def test_drafts_do_not_move_while_work_on_them_is_in_flight(busy):
    """An upload or caption request writes back to the account id it started
    under, and an unacknowledged pull must replay to its frozen targets, so a
    transfer waits for them rather than stranding their result."""
    out = _node(f"""
activate(['A', 'B', 'C']);
draft('A');
{busy}
await replaceAccount('A', 'D');
""")
    assert out["active"] == ["A", "B", "C"]
    assert out["slots"]["A"]["filename"] == "A_clip.mp4"
    assert out["log"]["persisted"] == 0
    assert out["log"]["alerts"] and "finish" in out["log"]["alerts"][0]


def test_in_flight_work_does_not_block_a_change_that_moves_no_draft():
    out = _node("""
activate(['A', 'B']);
draft('A');
draftWork = 1;
toggleAccount('C', true);
await new Promise(r => setTimeout(r, 0));
""")
    assert out["active"] == ["A", "B", "C"]
    assert out["log"]["alerts"] == []


def test_replace_ignores_an_account_that_is_already_active():
    """Maintainer decision (#19, 4): Replace lists inactive accounts only;
    reordering active accounts is what the order buttons are for."""
    out = _node("""
activate(['A', 'B', 'C']);
draft('A'); draft('B');
await replaceAccount('A', 'B');
""")
    assert out["active"] == ["A", "B", "C"]
    assert out["slots"]["A"]["filename"] == "A_clip.mp4"
    assert out["log"]["persisted"] == 0


def test_a_draft_goes_to_a_free_joining_account_before_anything_is_discarded():
    """Review repair R4: leaving accounts with no draft must not use up the
    joining accounts. Only C holds a draft; D is free, so nothing is lost."""
    out = _node("""
activate(['A', 'B', 'C']);
draft('C');
state.accountState.rosters.solo = ['D'];
await selectRoster('solo');
""")
    assert out["log"]["confirms"] == []
    assert out["active"] == ["D"]
    assert out["slots"]["D"]["filename"] == "C_clip.mp4"


def test_positional_pairs_win_before_leftovers_fill_free_accounts():
    out = _node("""
activate(['A', 'B', 'C']);
draft('A'); draft('C');
state.accountState.rosters.pair = ['D', 'E'];
await selectRoster('pair');
""")
    # A pairs with D by position; B (empty) leaves E free for C's draft.
    assert out["slots"]["D"]["filename"] == "A_clip.mp4"
    assert out["slots"]["E"]["filename"] == "C_clip.mp4"
    assert out["log"]["confirms"] == []


def test_a_second_account_change_waits_for_the_first_to_save():
    """Review repair R2: two swaps planned from the same state while the first
    is still saving used to drop a draft without a prompt."""
    out = _node("""
activate(['A', 'B', 'C']);
draft('A'); draft('B');
persistDelay = 30;
const first = replaceAccount('A', 'D');
const second = replaceAccount('B', 'D');
await Promise.all([first, second]);
""")
    assert out["active"] == ["D", "B", "C"]
    assert out["slots"]["D"]["filename"] == "A_clip.mp4"
    assert out["slots"]["B"]["filename"] == "B_clip.mp4"
    assert out["log"]["persisted"] == 1


def test_the_moved_line_is_cleared_by_the_next_account_change():
    """Review repair R7: the status must not describe a state that is gone."""
    out = _node("""
activate(['A', 'B']);
draft('A');
await replaceAccount('A', 'D');
toggleAccount('C', true);
await new Promise(r => setTimeout(r, 0));
""")
    assert out["log"]["status"][0].startswith("Moved 1 draft")
    assert out["log"]["status"][-1] == ""


def test_upload_never_overwrites_media_another_draft_may_hold(client, tmp_media):
    """Review repair R1: after A→D, D's draft still names `A_clip.mp4`. A new
    upload with the same name to A used to overwrite it, so Post All would have
    sent A's new clip to D."""
    first = client.post("/api/upload/A", files={"file": ("clip.mp4", b"original", "video/mp4")}).json()
    second = client.post("/api/upload/A", files={"file": ("clip.mp4", b"replacement", "video/mp4")}).json()
    assert first["filename"] == "A_clip.mp4"
    assert second["filename"] != first["filename"]
    assert second["filename"].startswith("A_") and second["filename"].endswith(".mp4")
    assert (tmp_media / first["filename"]).read_bytes() == b"original"
    assert (tmp_media / second["filename"]).read_bytes() == b"replacement"


# --- source contracts --------------------------------------------------------


def test_every_in_flight_draft_writer_counts_as_busy():
    """draftWorkBusy() is only as good as its coverage: each async path that
    writes back into a draft by account id must hold the counter."""
    for name in ("handleFile", "generateAll", "regenerateCaption", "postAll", "scheduleAll"):
        body = _function_body(name)
        assert "draftWork += 1" in body and "draftWork -= 1" in body, name
    assert "pullInFlight" in _function_body("draftWorkBusy")


def test_draft_writers_do_not_start_while_an_account_change_is_saving():
    for name in ("handleFile", "generateAll", "regenerateCaption"):
        assert "if (accountChangeInFlight)" in _function_body(name), name
    assert "accountChangeInFlight = true" in _function_body("applyActiveAccounts")


def test_upload_cannot_hang_the_busy_counter():
    body = _function_body("handleFile")
    assert "xhr.onabort" in body and "xhr.ontimeout" in body


def test_caption_frame_follows_the_draft_to_its_current_account():
    """Review repair R5: a frame captured after a move used to be dropped."""
    out = _node("""
activate(['A', 'B']);
const d = draft('A');
await replaceAccount('A', 'D');
log.owner = [slotOf(d), slotOf({})];
""")
    assert out["log"]["owner"] == ["D", None]
    body = _function_body("handleFile")
    assert "slotOf(s)" in body


def test_accounts_page_offers_replace_only_on_active_rows():
    body = _function_body("renderAccounts")
    assert "replaceAccount(" in body
    assert 'data-account-control="replace"' in body
    assert "index >= 0" in body


# --- rendered page ----------------------------------------------------------

_ACCOUNTS = [{"slot": s, "account_id": s, "name": n}
             for s, n in (("A", "Alpha"), ("B", "Beta"), ("C", "Gamma"), ("D", "Delta"))]
_STATE = {
    "schema_version": 1, "active_account_ids": ["A", "B", "C"], "rosters": {},
    "caption_defaults": {}, "device_profiles": {}, "disabled_platforms": {},
}


def _fake_api(route):
    url = route.request.url
    if "/api/accounts/state" in url:
        body = json.loads(route.request.post_data or "{}")
        state = {**_STATE, **body}
        return route.fulfill(json={"status": "saved", "account_state": state})
    if "/api/accounts" in url:
        return route.fulfill(json={
            "post_mode": "mock", "accounts": _ACCOUNTS[:3], "available_accounts": _ACCOUNTS,
            "account_state": _STATE, "sessions": {},
            "caption_styles": [{"name": "generic", "display_name": "Generic"}],
            "default_caption_style": "generic", "caption_limit": 2200,
            "device_profile_capacity": 5,
        })
    if "/api/media/" in url:
        return route.fulfill(status=404, body="")
    if "/api/" in url:
        return route.fulfill(json={"entries": [], "batches": [], "snapshots": []})
    return route.fulfill(body=_html(), content_type="text/html")


def _with_page(drive):
    """Run `drive(page)` against the real page in Chrome with /api faked."""
    playwright_api = pytest.importorskip("playwright.sync_api")
    with playwright_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel="chrome", headless=True)
        except Exception as exc:  # no local Chrome; only the launch may skip
            pytest.skip(f"Chrome unavailable: {exc}")
        page = browser.new_page()
        page.route("**/*", _fake_api)
        page.goto("http://ricepost.test/")
        page.wait_for_selector('.slot-card[data-account-id="B"]')
        page.evaluate("""() => {
          Object.assign(state.slots.B, {filename: 'B_clip.mp4', mediaType: 'video', caption: 'kept'});
          hydrateSlotCard('B');
        }""")
        try:
            return drive(page)
        finally:
            browser.close()


_CARDS = "() => [...document.querySelectorAll('.slot-card')].map(c => c.dataset.accountId)"


def test_review_card_menu_swaps_the_account_and_keeps_the_caption():
    def drive(page):
        card = page.locator('.slot-card[data-account-id="B"]')
        card.locator(".slot-menu summary").click()
        options = card.locator(".swap-menu button").all_text_contents()
        card.locator(".swap-menu button", has_text="Delta").click()
        page.wait_for_selector('.slot-card[data-account-id="D"]', timeout=2000)
        return options, page.evaluate("""() => ({
          cards: [...document.querySelectorAll('.slot-card')].map(c => c.dataset.accountId),
          caption: document.getElementById('caption_D').value,
          status: document.getElementById('statusPanel').textContent,
        })""")

    options, result = _with_page(drive)
    # Only the inactive account is offered.
    assert any("Delta" in o for o in options)
    assert not any(n in o for o in options for n in ("Alpha", "Beta", "Gamma"))
    assert result["cards"] == ["A", "D", "C"]
    assert result["caption"] == "kept"
    assert "Beta → Delta" in result["status"]


def test_a_stray_keystroke_on_the_slot_menu_moves_nothing():
    """Review repair R3: a native select commits on type-ahead, so focusing the
    slot menu and pressing "d" used to swap Beta for Delta with no prompt."""
    def drive(page):
        control = page.locator(
            '.slot-card[data-account-id="B"] .slot-menu select, '
            '.slot-card[data-account-id="B"] .slot-menu summary').first
        control.focus()
        page.keyboard.press("d")
        page.keyboard.press("ArrowDown")
        page.wait_for_timeout(200)
        return page.evaluate(_CARDS)

    assert _with_page(drive) == ["A", "B", "C"]


def test_accounts_page_replace_is_offered_on_active_rows_and_needs_a_click():
    """Review repair R8: the Accounts-page offer itself, not just the handler."""
    def drive(page):
        page.evaluate("navTo('accounts')")
        rows = page.evaluate("""() => [...document.querySelectorAll('.account-row')].map(r => ({
          id: r.dataset.accountId,
          options: [...r.querySelectorAll('[data-account-control="replace"] option')].map(o => o.value),
        }))""")
        page.select_option('.account-row[data-account-id="B"] [data-account-control="replace"]', "D")
        before = page.evaluate(_CARDS)
        page.click('.account-row[data-account-id="B"] [data-account-control="replace-apply"]')
        # Review is hidden while Accounts is shown, so wait for the card to exist.
        page.wait_for_selector('.slot-card[data-account-id="D"]', state="attached", timeout=2000)
        return rows, before, page.evaluate(_CARDS), page.evaluate("document.getElementById('caption_D').value")

    rows, before, after, caption = _with_page(drive)
    offered = {r["id"]: [o for o in r["options"] if o] for r in rows}
    assert offered == {"A": ["D"], "B": ["D"], "C": ["D"], "D": []}
    assert before == ["A", "B", "C"]  # choosing alone changes nothing
    assert after == ["A", "D", "C"]
    assert caption == "kept"
