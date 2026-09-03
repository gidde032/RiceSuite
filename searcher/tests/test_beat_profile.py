"""Beat-profile loader (D2)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ricesearcher.beat.profile import default_profile_path, load_profile


def test_load_default_profile() -> None:
    p = load_profile()
    assert p.version
    assert p.name == "example-beat"
    assert p.brief
    assert "personb" in p.keywords  # keywords are lowercased


def test_default_profile_path_exists() -> None:
    assert default_profile_path().is_file()


def test_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    custom = tmp_path / "beat.json"
    custom.write_text(
        json.dumps({"version": "v9", "name": "other", "brief": "b", "keywords": ["X"]})
    )
    monkeypatch.setenv("RICESEARCHER_BEAT_PROFILE", str(custom))
    p = load_profile()
    assert p.version == "v9"
    assert p.keywords == ["x"]


def test_missing_required_field_raises(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"name": "x"}))  # no version / brief
    with pytest.raises(ValueError):
        load_profile(bad)
