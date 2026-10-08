"""Caption Motion (and later emoji) controls in the review UI (RiceSuite #66)."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from app.models import Word

ROOT = Path(__file__).resolve().parents[1]


def _html() -> str:
    return (ROOT / "web/index.html").read_text(encoding="utf-8")


def _js() -> str:
    return (ROOT / "web/app.js").read_text(encoding="utf-8")


def test_caption_ui_behaviors():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required for the caption UI behavior checks")
    result = subprocess.run(
        [node, "--test", str(Path(__file__).with_name("caption-ui.test.cjs"))],
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_motion_toggle_is_offered_on_by_default_and_sent():
    html = _html()
    js = _js()
    assert '<input class="motion-toggle" type="checkbox" checked />' in html
    assert "motion: clip.motionToggleEl.checked," in js
    # Saved per slot like the caption style, and carried when slots renumber.
    assert 'slotFlag(clip.ord, "motion", true)' in js
    assert 'rememberSlotStyle(clip.ord, "motion", clip.motionToggleEl.checked)' in js
    assert 'rememberSlotStyle(ord, "motion", clip.motionToggleEl.checked)' in js
    # A photo has no captions, so no Motion either.
    css = (ROOT / "web/style.css").read_text(encoding="utf-8")
    assert ".photo-card .motion-row" in css


def test_the_bundled_font_preset_has_a_card():
    html = _html()
    assert 'value="montserrat"' in html
    assert '<span class="choice-label">Montserrat</span>' in html


# --- caption emoji: the inline transcript editor (RiceSuite #66) -------------
# Finn chose option B, inline markers in the transcript, on 2026-10-06.


def _node_eval(expression: str):
    """Evaluate ``expression`` against the real app.js in node; JSON result."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required for the caption UI parity checks")
    script = (
        "const vm = require('node:vm'); const fs = require('node:fs');"
        "const el = () => ({ textContent: '', classList: { toggle() {}, add() {}, remove() {} },"
        " addEventListener() {}, setAttribute() {} });"
        "const store = { getItem: () => null, setItem() {}, removeItem() {} };"
        "const ctx = vm.createContext({ document: { getElementById: el }, localStorage: store,"
        " sessionStorage: store, window: { sessionStorage: store }, setInterval() {},"
        " setTimeout() { return 0; }, clearTimeout() {}, console,"
        " fetch: async () => ({ ok: true, json: async () => ({}) }) });"
        f"vm.runInContext(fs.readFileSync({str(ROOT / 'web/app.js')!r}, 'utf8'), ctx);"
        f"process.stdout.write(JSON.stringify(vm.runInContext({expression!r}, ctx)));"
    )
    out = subprocess.run(
        [node, "-e", script], capture_output=True, text=True, timeout=30
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_the_editor_groups_phrases_exactly_as_the_render_does():
    from transcribe.phrasing import group_words

    cases = [
        [("a", 0.0, 0.3), ("b", 0.3, 0.6), ("", 0.6, 0.9), ("c", 0.9, 1.2)]
        + [(f"w{i}", 1.2 + i * 0.3, 1.5 + i * 0.3) for i in range(7)],
        [("x", 0.0, 0.2), ("y", 0.9001, 1.0), ("z", 1.7, 1.9), ("  ", 1.9, 2.0)],
        [("one", 0.0, 0.1), ("two", 0.1, 0.2), ("three", 0.2, 0.3)],
    ]
    for case in cases:
        words = [
            {"text": t, "start": s, "end": e, "line_start": i == 2}
            for i, (t, s, e) in enumerate(case)
        ]
        objs = [Word(**w) for w in words]
        index_of = {id(w): i for i, w in enumerate(objs)}
        expected = [[index_of[id(w)] for w in p.words] for p in group_words(objs)]
        assert _node_eval(f"phraseGroups({json.dumps(words)})") == expected


def test_the_editor_accepts_exactly_the_emoji_the_render_accepts():
    from render.text_image import is_emoji_cluster

    samples = [
        "🍕",
        "👍🏽",
        "🇫🇷",
        "👨‍👩‍👧",
        "❤️",
        "☀️",
        "🏖️",
        "🏳️‍🌈",
        "a",
        "1️⃣",
        "🍕 ",
        "",
        "pizza",
        "🍕" * 17,
        "★",
        "✓",
        "#",
        "🔥🔥",
        "🇫",
        "\ufe0f",
        "\u200d",
        "🍕\u200d",
        "\u200d🍕",
        "🏽",
        "🇫🇷🇩🇪",
        "🏽🍕",
    ]
    got = _node_eval(f"{json.dumps(samples)}.map(isEmojiCluster)")
    assert got == [is_emoji_cluster(s) for s in samples]


def test_the_emoji_controls_are_offered_off_by_default_and_sent():
    html = _html()
    js = _js()
    assert '<input class="emoji-toggle" type="checkbox" />' in html
    assert 'class="emoji-suggest"' in html and "✨ Suggest emoji" in html
    assert 'class="emoji-strip"' in html
    assert "emoji_on: clip.emojiToggleEl.checked," in js
    assert "emoji: emojiPayload(clip.emojiPicks || {})," in js
    assert 'slotFlag(clip.ord, "emoji", false)' in js
    assert 'rememberSlotStyle(clip.ord, "emoji", clip.emojiToggleEl.checked)' in js
    assert 'rememberSlotStyle(ord, "emoji", clip.emojiToggleEl.checked)' in js
    css = (ROOT / "web/style.css").read_text(encoding="utf-8")
    assert ".photo-card .emoji-field" in css


def test_nothing_is_sent_to_the_emoji_picker_without_the_button():
    js = _js()
    # One fetch to the picker, inside requestEmoji ...
    assert len(re.findall(r"/emoji[`'\"]", js)) == 1
    start = js.index("async function requestEmoji(")
    assert js.index("/emoji`", start) < js.index("\n}\n", start)
    # ... which only the button's click handler calls.
    calls = [
        m.start()
        for m in re.finditer(r"requestEmoji\(", js)
        if not js[max(0, m.start() - 20) : m.start()].rstrip().endswith("function")
    ]
    assert len(calls) == 1
    assert 'emojiSuggestEl.addEventListener("click", () => requestEmoji(clip))' in js


def test_replacing_the_words_clears_the_emoji_picks():
    js = _js()
    assignments = [m.start() for m in re.finditer(r"clip\.words = ", js)]
    assert len(assignments) == 3  # transcription, lyric alignment, restore
    for at in assignments:
        line_end = js.index("\n", at)
        next_line = js[line_end + 1 : js.index("\n", line_end + 1)]
        assert next_line.strip() == "resetEmojiPicks(clip);", js[at:line_end]
