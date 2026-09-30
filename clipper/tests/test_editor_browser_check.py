"""The editor browser check never writes into a Clipper data directory.

Its screenshots used to default to ``.riceclipper_work/editor-browser`` inside
the pillar, which is Clipper's legacy work directory: running it could write
into live data and make the suite detect legacy data (RiceSuite #20 review).
"""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load(monkeypatch, **env):
    for key in ("RICECLIPPER_EDITOR_BROWSER_DIR",):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    spec = importlib.util.spec_from_file_location(
        "check_editor_browser", ROOT / "scripts/check_editor_browser.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_default_output_is_outside_the_repository(monkeypatch):
    output = _load(monkeypatch).OUTPUT.resolve()
    assert ".riceclipper_work" not in output.parts
    assert not output.is_relative_to(ROOT.parent.resolve())


def test_output_can_be_chosen(monkeypatch, tmp_path):
    module = _load(monkeypatch, RICECLIPPER_EDITOR_BROWSER_DIR=str(tmp_path))
    assert module.OUTPUT == tmp_path
