from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _html() -> str:
    return (ROOT / "web/index.html").read_text(encoding="utf-8")


def _js() -> str:
    return (ROOT / "web/app.js").read_text(encoding="utf-8")


def test_review_ui_exposes_all_visual_choices_and_sends_them():
    html = _html()
    javascript = _js()

    # All three header treatments and all seven caption presets are offered
    # (they appear in both the batch-default selects and the per-clip template).
    assert 'value="plain"' in html
    assert 'value="black_plate"' in html
    assert 'value="white_plate"' in html
    for style in ("classic", "clean", "punch", "friendly", "sunset", "mono", "editorial"):
        assert f'value="{style}"' in html

    # The render payload still carries the per-clip visual choices.
    assert "caption_style: clip.captionStyleEl.value" in javascript
    assert "header_style: clip.headerStyleEl.value" in javascript


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


def test_render_runs_one_clip_at_a_time():
    # Render-all iterates clips sequentially (awaits each) rather than firing
    # concurrent renders — the SPEC §9 bounded-batch guarantee.
    javascript = _js()
    assert "handleRenderAll" in javascript
    assert "await renderClip(targets[i])" in javascript
