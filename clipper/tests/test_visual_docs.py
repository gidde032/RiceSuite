from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _normalized_doc(name: str) -> str:
    return " ".join((ROOT / name).read_text(encoding="utf-8").split())


def test_spec_records_the_bounded_visual_preset_contract():
    spec = _normalized_doc("SPEC.md")
    assert "nineteen caption presets and three header treatments" in spec
    assert "Plain text is the default" in spec
    assert "user-authored preset persistence" in spec


def test_roadmap_keeps_custom_preset_work_deferred():
    roadmap = _normalized_doc("ROADMAP.md")
    assert "nineteen caption presets and three header treatments" in roadmap
    assert "persistent saved presets remain deferred" in roadmap


def test_editor_reference_keeps_narrow_text_panes_above_ratified_minimum():
    reference = _normalized_doc("docs/design/editor-layout-reference.html")
    assert "height: clamp(280px, 36vh, 440px)" in reference
    assert ".text-box { height: 220px !important; }" not in reference


def test_config_docs_say_the_key_and_model_also_drive_the_emoji_picker():
    """Contract review #66 (low): the key and model are not header-only now."""
    readme = _normalized_doc("README.md")
    env = _normalized_doc(".env.example")
    assert (
        "**✨ Suggest emoji**" in readme.split("| `ANTHROPIC_API_KEY`")[1].split("|")[2]
    )
    model_row = readme.split("| `RICECLIPPER_HEADER_MODEL`")[1].split(
        "| `RICECLIPPER_WHISPER"
    )[0]
    assert "caption emoji picker" in model_row
    assert "caption emoji picker" in env


def test_byte_identical_promise_is_limited_to_clips_without_emoji_rows():
    """Contract review #66 (low): emoji rows change the script, Motion off or on."""
    for name in ("SPEC.md", "CHANGELOG.md"):
        doc = _normalized_doc(name)
        for part in doc.split("byte-identical")[:-1]:
            assert "without emoji rows" in part[-160:], name
