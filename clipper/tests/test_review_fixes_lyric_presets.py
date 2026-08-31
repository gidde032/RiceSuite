"""Regression contracts from PR #11's three-lens cold review."""

import re
from pathlib import Path

from render.ass import build_ass, style_for_presets
from tests._util import words

ROOT = Path(__file__).resolve().parents[1]


def _normalized_doc(name: str) -> str:
    return " ".join((ROOT / name).read_text(encoding="utf-8").split())


def test_lyric_block_renderer_preserves_ratified_italic_treatment():
    """Skeptical and preview reviewers (HIGH): Lyric Block renders italic.

    Fix: carry an explicit italic preset field through to the ASS style row.
    """
    lyric_block = style_for_presets("lyric_block", "plain")
    classic = style_for_presets("classic", "plain")

    assert lyric_block.italic is True
    assert classic.italic is False

    ass = build_ass(words(("little", 0.0, 0.4)), duration=1.0, style=lyric_block)
    caption_style = next(
        line for line in ass.splitlines() if line.startswith("Style: Caption,")
    )
    assert caption_style.split(",")[8] == "-1"


def test_powder_is_the_visible_name_in_source_of_truth_docs():
    """Skeptical and preview reviewers (MEDIUM): docs call the preset Powder.

    Fix: distinguish the visible Powder name from its stable internal identifier
    and DIN Condensed renderer font in every affected source-of-truth document.
    """
    expected_visible_contracts = {
        "SPEC.md": "**Velvet Serif**, **Powder**, and **Baskerville**",
        "docs/design/slate-ui-spec.md": (
            "**Lyric Block**, **Velvet Serif**, **Powder**, and **Baskerville**"
        ),
        "CHANGELOG.md": "Powder (DIN Condensed font with powder-blue highlight)",
    }
    for name, visible_contract in expected_visible_contracts.items():
        document = _normalized_doc(name)
        assert visible_contract in document
        assert "DIN Condensed font" in document


def test_each_lyric_card_binds_identifier_preview_and_visible_label():
    """Frontend reviewer (LOW): each new card's contract is tested as one unit.

    Fix: associate the submitted value, preview class, and visible label instead
    of merely proving that each identifier occurs somewhere in the page.
    """
    html = (ROOT / "web/index.html").read_text(encoding="utf-8")
    cards = re.findall(r'<label class="choice-card">(.*?)</label>', html)
    expected = {
        "lyric_block": ("sample-lyric-block", "Lyric Block"),
        "velvet_serif": ("sample-velvet-serif", "Velvet Serif"),
        "din_condensed": ("sample-din-condensed", "Powder"),
        "baskerville": ("sample-baskerville", "Baskerville"),
    }

    for identifier, (preview_class, visible_label) in expected.items():
        card = next(card for card in cards if f'value="{identifier}"' in card)
        assert f'class="caption-sample {preview_class}"' in card
        assert f'<span class="choice-label">{visible_label}</span>' in card
