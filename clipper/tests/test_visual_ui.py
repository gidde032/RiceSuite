from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _html() -> str:
    return (ROOT / "web/index.html").read_text(encoding="utf-8")


def _js() -> str:
    return (ROOT / "web/app.js").read_text(encoding="utf-8")


def test_review_ui_exposes_all_visual_choices_and_sends_them():
    html = _html()
    javascript = _js()

    # All three header treatments and all eleven caption presets are offered
    # (they appear in both the batch-default selects and the per-clip template).
    assert 'value="plain"' in html
    assert 'value="black_plate"' in html
    assert 'value="white_plate"' in html
    for style in (
        "classic",
        "clean",
        "punch",
        "friendly",
        "sunset",
        "mono",
        "editorial",
        "lyric_block",
        "velvet_serif",
        "din_condensed",
        "baskerville",
    ):
        assert f'value="{style}"' in html

    # The render payload still carries the per-clip visual choices.
    assert "caption_style: radioValue(clip.captionStyleEl)" in javascript
    assert "header_style: radioValue(clip.headerStyleEl)" in javascript
    assert 'class="choice-grid header-choice-grid"' in html
    assert 'class="choice-grid caption-choice-grid"' in html
    assert 'value="din_condensed">Powder / powder blue</option>' in html
    assert '<span class="choice-label">Powder</span>' in html
    assert "setRadioValue(clip.captionStyleEl" in javascript
    assert "setRadioValue(clip.headerStyleEl" in javascript


def test_slate_identity_and_theme_contract_are_present():
    html = _html()
    stylesheet = (ROOT / "web/style.css").read_text(encoding="utf-8")
    approved_logo = (ROOT / "docs/design/assets/slate-logo-selected.png").read_bytes()
    runtime_logo = (ROOT / "web/slate-logo.png").read_bytes()

    assert 'href="/slate-logo.png"' in html
    assert '<span class="sr-only">RiceClipper</span>' in html
    for token in (
        "#04060a",
        "#0c1116",
        "#14191e",
        "#2b3136",
        "#626a70",
        "#aeb3b6",
        "#80878c",
        "#3b4044",
        "#e5e8ea",
    ):
        assert token in stylesheet
    assert runtime_logo == approved_logo


def test_each_cloned_card_uses_non_reused_identity_for_dom_relationships():
    javascript = _js()
    for prefix in (
        "header-style",
        "caption-style",
        "header-help",
        "header-input",
        "clip-title",
    ):
        assert f"`{prefix}-${{clip.localId}}`" in javascript
        assert f"`{prefix}-${{clip.ord}}`" not in javascript

    # Reproduce the remove/add shape: the visible ordinal may be reused, but
    # monotonic local IDs—and therefore radio names—remain distinct.
    cards = [{"local_id": 1, "ord": 1}, {"local_id": 2, "ord": 2}]
    cards.pop(0)
    cards.append({"local_id": 3, "ord": 2})
    assert len({f"header-style-{card['local_id']}" for card in cards}) == len(cards)
    assert "setRadioDisabled(clip.captionStyleEl" in javascript


def test_slate_interactions_keep_keyboard_and_status_semantics():
    html = _html()
    javascript = _js()
    stylesheet = (ROOT / "web/style.css").read_text(encoding="utf-8")

    assert 'id="file-input" class="file-input-accessible"' in html
    assert "multiple hidden" not in html
    assert 'class="header-label"' in html
    assert 'maxlength="120"' not in html
    assert 'role="status"' in html
    assert (
        'node.querySelector(".header-label").htmlFor = clip.headerEl.id' in javascript
    )
    assert "repeat(auto-fit, minmax(76px, 1fr))" in stylesheet
    assert "opacity: 0.42" not in stylesheet
    assert "opacity: 0.38" not in stylesheet
    assert ">Render all</button>" in html


def test_batch_review_ui_supports_multiple_clips():
    html = _html()
    javascript = _js()

    # Upload accepts several clips at once, and each gets a cloned review card.
    assert "multiple" in html
    assert 'id="clip-card-template"' in html
    assert 'id="render-all-btn"' in html

    # Batch-default preset controls exist and per-clip cards can override them.
    assert 'id="batch-caption-style"' in html
    assert 'id="batch-header-style"' in html
    assert "captionStyleTouched" in javascript
    assert "headerStyleTouched" in javascript

    # A client-side clip list drives the existing per-job routes sequentially.
    assert "const clips = []" in javascript
    assert "processIngestQueue" in javascript


def test_handoff_send_button_posts_the_batch():
    html = _html()
    javascript = _js()
    assert 'id="send-handoff-btn"' in html
    # The send button hands the batch to the local handoff endpoint (no posting).
    assert "/api/handoff" in javascript
    assert "position: i + 1" in javascript  # handoff order → RicePoster slot order
    assert "caption_style: radioValue(c.captionStyleEl)" in javascript
    assert "header_style: radioValue(c.headerStyleEl)" in javascript


def test_render_runs_one_clip_at_a_time():
    # Render-all iterates clips sequentially (awaits each) rather than firing
    # concurrent renders — the SPEC §9 bounded-batch guarantee.
    javascript = _js()
    assert "handleRenderAll" in javascript
    assert "await renderClip(targets[i])" in javascript
