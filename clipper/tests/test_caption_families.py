"""Caption style families, retired styles, and real-style thumbnails (RiceSuite #79)."""

import re
import typing
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app import main
from app.models import CaptionStyle, HandoffClip, RenderRequest
from render import text_image
from render.ass import (
    _CAPTION_PRESETS,
    CAPTION_STYLE_NAMES,
    DEFAULT_CAPTION_STYLE,
    RETIRED_CAPTION_STYLES,
    build_ass,
    style_for_presets,
)
from tests._util import words

ROOT = Path(__file__).resolve().parents[1]
COLOUR_FIELDS = {"primary_color", "highlight_color", "outline_color"}


def test_the_request_model_offers_exactly_the_renderer_presets():
    literal = typing.get_args(CaptionStyle)[0]
    assert typing.get_args(literal) == CAPTION_STYLE_NAMES
    assert set(_CAPTION_PRESETS) == set(CAPTION_STYLE_NAMES)


def test_montserrat_is_the_default_everywhere():
    assert DEFAULT_CAPTION_STYLE == "montserrat"
    assert RenderRequest().caption_style == "montserrat"
    assert HandoffClip(job_id="j", position=1).caption_style == "montserrat"
    assert style_for_presets() == style_for_presets("montserrat", "plain")


@pytest.mark.parametrize("name", RETIRED_CAPTION_STYLES)
def test_a_retired_style_takes_the_default(name):
    assert name not in CAPTION_STYLE_NAMES
    assert RenderRequest(caption_style=name).caption_style == "montserrat"
    assert HandoffClip(job_id="j", position=1, caption_style=name).caption_style == (
        "montserrat"
    )
    assert style_for_presets(name, "plain") == style_for_presets("montserrat", "plain")


def test_an_unknown_style_is_still_rejected():
    with pytest.raises(ValidationError):
        RenderRequest(caption_style="sparkle")


@pytest.mark.parametrize(
    ("family", "variants"),
    [
        ("montserrat", ("montserrat_violet", "montserrat_sky", "montserrat_green")),
        ("punch", ("punch_volt", "punch_red", "punch_blue")),
        ("pop", ("pop_bubblegum", "pop_lime", "pop_fire")),
    ],
)
def test_a_family_variant_changes_only_colours(family, variants):
    base = _CAPTION_PRESETS[family]
    for name in variants:
        variant = _CAPTION_PRESETS[name]
        changed = {
            k for k in variant.keys() | base.keys() if variant.get(k) != base.get(k)
        }
        assert changed and changed <= COLOUR_FIELDS


def test_neon_inverse_also_changes_its_edge_width():
    base, inverse = _CAPTION_PRESETS["neon"], _CAPTION_PRESETS["neon_inverse"]
    changed = {k for k in base if inverse[k] != base[k]}
    assert changed == {"primary_color", "outline_color", "outline"}


def test_pop_and_neon_write_their_coloured_edge_into_the_ass_style():
    pop = build_ass(
        words(("hi", 0.0, 0.3)), duration=1.0, style=style_for_presets("pop")
    )
    neon = build_ass(
        words(("hi", 0.0, 0.3)), duration=1.0, style=style_for_presets("neon")
    )
    # ASS colours are &HAABBGGRR.
    assert "Style: Caption,Luckiest Guy,100,&H004DE1FF,&H00FFFFFF,&H006B0A3B," in pop
    assert (
        "Style: Caption,Futura Condensed ExtraBold,100,&H00FFC34F,&H00FFFFFF,"
        "&H00B84FFF," in neon
    )


def test_every_preset_font_resolves_for_measurement_on_this_host():
    # The bundled faces always resolve; system faces resolve on macOS.
    for name in ("montserrat", "pop"):
        style = style_for_presets(name)
        assert text_image.bundled_font_file(style.font).exists()
        assert text_image.caption_font(style.font, style.bold, style.italic)
    for name in CAPTION_STYLE_NAMES:
        style = style_for_presets(name)
        key = (style.font, style.bold, style.italic)
        assert (
            text_image.bundled_font_file(style.font) is not None
            or key in text_image._CAPTION_FACES
        ), name


# --- thumbnails ---------------------------------------------------------------


def _sample_declarations(css: str, name: str) -> str:
    """Every declaration that applies to the ``.sample-<name>`` thumbnail."""
    cls = ".sample-" + name.replace("_", "-")
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    found = []
    for selectors, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        if cls in [s.strip() for s in selectors.split(",")]:
            found.append(body)
    return ";".join(found)


def _var(declarations: str, var: str, default: str) -> str:
    values = re.findall(rf"{var}:\s*(#[0-9a-fA-F]{{3,6}})", declarations)
    return (values[-1] if values else default).lower()


def _hex(rrggbb: str) -> str:
    return f"#{rrggbb.lower()}"


@pytest.mark.parametrize("name", CAPTION_STYLE_NAMES)
def test_each_thumbnail_shows_the_style_colours_and_edge(name):
    css = (ROOT / "web/style.css").read_text(encoding="utf-8")
    style = style_for_presets(name)
    declarations = _sample_declarations(css, name)
    assert declarations, f"no thumbnail rule for {name}"

    text = _var(declarations, "--sample-text", "#ffffff")
    assert text == _hex(style.primary_color)
    assert _var(declarations, "--sample-hi", "") == _hex(style.highlight_color)
    edge = _var(declarations, "--sample-edge", "#000")
    assert edge == (
        "#000" if style.outline_color == "000000" else _hex(style.outline_color)
    )


def test_each_card_shows_a_text_word_and_a_highlighted_word():
    html = (ROOT / "web/index.html").read_text(encoding="utf-8")
    cards = re.findall(r'<label class="choice-card">(.*?)</label>', html)
    caption_cards = [c for c in cards if 'name="caption-style"' in c]
    assert len(caption_cards) == len(CAPTION_STYLE_NAMES)
    for card in caption_cards:
        assert 'Red <span class="caption-sample-hi">fox</span>' in card


def test_the_grid_groups_cards_by_family_in_catalogue_order():
    html = (ROOT / "web/index.html").read_text(encoding="utf-8")
    families = re.findall(
        r'<div class="caption-family" role="group" aria-label="([^"]+)">', html
    )
    assert families == ["Montserrat", "Punch", "Pop", "Neon", "More"]
    values = re.findall(r'name="caption-style" value="([^"]+)"', html)
    assert tuple(values) == CAPTION_STYLE_NAMES
    assert re.search(r'value="montserrat" checked', html)


def test_the_page_serves_only_the_bundled_fonts():
    client = TestClient(main.app, base_url="http://127.0.0.1:8000")
    for name in text_image.BUNDLED_FONTS.values():
        response = client.get(f"/fonts/{name}")
        assert response.status_code == 200
        assert response.content == (text_image.BUNDLED_FONTS_DIR / name).read_bytes()
    assert client.get("/fonts/OFL.txt").status_code == 404
    assert client.get("/fonts/..%2F..%2Fapp%2Fmain.py").status_code == 404
