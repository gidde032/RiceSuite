from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _html() -> str:
    return (ROOT / "web/index.html").read_text(encoding="utf-8")


def _js() -> str:
    return (ROOT / "web/app.js").read_text(encoding="utf-8")


def test_review_ui_exposes_all_visual_choices_and_sends_them():
    html = _html()
    javascript = _js()

    # All three header treatments and all eleven caption presets are offered as
    # per-clip radio-card tiles. The universal pre-upload batch-default selects
    # were removed in favor of per-slot saved defaults.
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
    assert 'value="din_condensed"' in html
    assert '<span class="choice-label">Powder</span>' in html
    assert "setRadioValue(clip.captionStyleEl" in javascript
    assert "setRadioValue(clip.headerStyleEl" in javascript


def test_content_row_is_offered_and_sent():
    html = _html()
    javascript = _js()

    assert 'class="field choice-field content" hidden' not in html
    assert 'name="content" value="speech" checked' in html
    assert 'name="content" value="music"' in html

    assert "content: radioValue(clip.contentEl)" in javascript
    assert "clip.contentEl = node.querySelector" in javascript
    assert "clip.contentEl.hidden" not in javascript


def test_music_content_offers_lyric_alignment_and_preserves_line_breaks():
    html = _html()
    javascript = _js()

    assert 'class="lyrics" hidden' in html
    assert 'class="lyrics-input"' in html
    assert 'placeholder="Paste lyrics, one line per caption line"' in html
    assert 'class="lyrics-align"' in html
    assert 'class="lyrics-badge"' in html

    assert "/api/jobs/${clip.jobId}/lyrics" in javascript
    assert "aligned · ${Math.round(data.anchor_rate * 100)}% anchors" in javascript
    assert '"even fill"' in javascript
    assert "line_start: w.line_start" in javascript


def test_geometry_row_is_offered_and_sent_and_toggled_by_orientation():
    html = _html()
    javascript = _js()

    # The landscape-only Geometry row: three radio-card tiles named "geometry".
    assert 'class="field choice-field geometry" hidden' in html
    assert 'name="geometry" value="auto"' in html
    assert 'name="geometry" value="blur_pad"' in html
    assert 'name="geometry" value="crop"' in html
    # The Auto card carries the plan summary and the near-zone warning badge.
    assert 'class="geometry-summary"' in html
    assert 'class="geometry-warning"' in html

    # The render payload carries the per-clip geometry choice.
    assert "geometry: radioValue(clip.geometryEl)" in javascript
    # The row is shown only for landscape jobs (width > height).
    assert "state.width > state.height" in javascript
    assert "will use subject crop or blur-pad" in javascript


def test_geometry_summary_strings_for_speech_and_music():
    javascript = _js()

    assert "blur-pad · analysis failed" in javascript
    assert "low safe rate" in javascript
    assert "low face rate" in javascript
    assert "no face" in javascript
    assert "crop · static centered" in javascript
    assert "holds" in javascript


def test_render_failure_remains_retryable_after_geometry_change():
    javascript = _js()
    render_clip = javascript.split("async function renderClip(clip)", 1)[1].split(
        "async function showResult", 1
    )[0]

    assert 'clip.status = "ready"; // keep failed renders retryable' in render_clip
    assert 'clip.status = "error"' not in render_clip


def test_upload_copy_describes_landscape_subject_crop():
    normalized = " ".join(_html().split())

    assert "Choose clips" in normalized
    assert "Landscape clips use subject crop or blur-pad." in normalized


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

    # The universal pre-upload batch-default selects are gone; per-clip cards now
    # seed from per-slot saved defaults instead.
    assert 'id="batch-caption-style"' not in html
    assert 'id="batch-header-style"' not in html
    assert "batch-caption-style" not in javascript
    assert "batch-header-style" not in javascript

    # A client-side clip list drives the existing per-job routes sequentially.
    assert "const clips = []" in javascript
    assert "processIngestQueue" in javascript


def test_per_slot_saved_styles_seed_and_persist():
    html = _html()
    javascript = _js()

    # No universal caption/header dropdown remains on the upload page.
    assert 'class="batch-defaults"' not in html
    assert "<select id=" not in html

    # Each clip seeds its caption/header choice from the slot ordinal's saved
    # default (v1 classic/plain fallback), keyed in browser localStorage.
    assert 'slotDefault(clip.ord, "caption", "classic")' in javascript
    assert 'slotDefault(clip.ord, "header", "plain")' in javascript
    assert "localStorage" in javascript
    assert "riceclipper.slotStyles" in javascript

    # Changing a clip writes that slot's default back so it carries forward.
    assert 'rememberSlotStyle(clip.ord, "caption"' in javascript
    assert 'rememberSlotStyle(clip.ord, "header"' in javascript


def test_music_upload_auto_switches_to_mix_when_untouched():
    javascript = _js()

    # Picking a music file defaults the mode to "mix" — but only while the mode
    # is still untouched, so a deliberate choice is respected.
    assert "musicModeTouched" in javascript
    assert 'clip.musicModeEl.value = "mix"' in javascript
    assert "!clip.musicModeTouched" in javascript


def test_cache_controls_live_in_the_upload_panel():
    html = _html()

    # The media-cache controls moved up into the upload panel (no standalone
    # bottom cache panel), grouped with the RiceSearcher intake.
    assert 'id="cache-panel"' not in html
    assert 'class="upload-tools"' in html
    assert 'class="cache-tools"' in html
    assert 'id="cache-info"' in html
    assert 'id="clear-cache-btn"' in html


def test_handoff_send_button_posts_the_batch():
    html = _html()
    javascript = _js()
    assert 'id="send-handoff-btn"' in html
    # The send button hands the batch to the local handoff endpoint (no posting).
    assert "/api/handoff" in javascript
    assert "position: i + 1" in javascript  # handoff order → RicePoster slot order
    assert "caption_style: radioValue(c.captionStyleEl)" in javascript
    assert "header_style: radioValue(c.headerStyleEl)" in javascript


def test_auto_header_control_is_wired_to_the_generation_endpoint():
    html = _html()
    javascript = _js()

    # The per-clip card exposes a generate button and an optional guidance field.
    assert 'class="header-generate"' in html
    assert 'class="header-feedback"' in html

    # It posts to the header endpoint, auto-fills after transcription, and
    # regenerates a different header on demand (avoid + feedback).
    assert "/header" in javascript
    assert "await autoGenerateHeader(clip)" in javascript
    assert "function regenerateHeader" in javascript


def test_render_runs_one_clip_at_a_time():
    # Render-all iterates clips sequentially (awaits each) rather than firing
    # concurrent renders — the SPEC §9 bounded-batch guarantee.
    javascript = _js()
    assert "handleRenderAll" in javascript
    assert "await renderClip(targets[i])" in javascript
