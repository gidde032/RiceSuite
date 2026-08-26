from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _normalized_doc(name: str) -> str:
    return " ".join((ROOT / name).read_text(encoding="utf-8").split())


def test_spec_records_the_bounded_visual_preset_contract():
    spec = _normalized_doc("SPEC.md")
    assert "seven caption presets and three header treatments" in spec
    assert "Plain text is the default" in spec
    assert "user-authored preset persistence" in spec


def test_roadmap_keeps_custom_preset_work_deferred():
    roadmap = _normalized_doc("ROADMAP.md")
    assert "seven built-in caption presets, three compact header treatments" in roadmap
    assert "persistent saved presets remain deferred" in roadmap
