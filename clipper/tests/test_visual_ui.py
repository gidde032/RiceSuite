from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_review_ui_exposes_all_visual_choices_and_sends_them():
    html = (ROOT / "web/index.html").read_text(encoding="utf-8")
    javascript = (ROOT / "web/app.js").read_text(encoding="utf-8")

    assert 'id="header-style"' in html
    assert 'value="plain"' in html
    assert 'value="black_plate"' in html
    assert 'value="white_plate"' in html
    for style in ("classic", "clean", "punch", "friendly", "sunset", "mono", "editorial"):
        assert f'value="{style}"' in html

    assert "caption_style: $(\"caption-style\").value" in javascript
    assert "header_style: $(\"header-style\").value" in javascript
