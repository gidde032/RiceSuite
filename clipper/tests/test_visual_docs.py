from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _normalized_doc(name: str) -> str:
    return " ".join((ROOT / name).read_text(encoding="utf-8").split())


def test_spec_records_the_bounded_visual_preset_contract():
    spec = _normalized_doc("SPEC.md")
    assert "eleven caption presets and three header treatments" in spec
    assert "Plain text is the default" in spec
    assert "user-authored preset persistence" in spec


def test_roadmap_keeps_custom_preset_work_deferred():
    roadmap = _normalized_doc("ROADMAP.md")
    assert "eleven caption presets and three header treatments" in roadmap
    assert "persistent saved presets remain deferred" in roadmap


def test_editor_reference_keeps_narrow_text_panes_above_ratified_minimum():
    reference = _normalized_doc("docs/design/editor-layout-reference.html")
    assert "height: clamp(280px, 36vh, 440px)" in reference
    assert ".text-box { height: 220px !important; }" not in reference
