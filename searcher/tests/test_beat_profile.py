"""Beat-profile loader (D2, ADR-002)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ricesearcher.beat.profile import (
    ensure_seed,
    list_profiles,
    load_profile,
)


def _write(profiles_dir: Path, profile_id: str, **fields: object) -> Path:
    profiles_dir.mkdir(parents=True, exist_ok=True)
    path = profiles_dir / f"{profile_id}.json"
    base = {"version": "v1", "name": profile_id, "brief": "b"}
    base.update(fields)
    path.write_text(json.dumps(base), encoding="utf-8")
    return path


def test_ensure_seed_copies_default_once(tmp_path: Path) -> None:
    profiles = tmp_path / "profiles"
    ensure_seed(profiles)
    seeded = profiles / "example-beat.json"
    assert seeded.is_file()
    seeded.write_text('{"version": "edited"}', encoding="utf-8")
    ensure_seed(profiles)  # dir is non-empty now: must not overwrite
    assert json.loads(seeded.read_text())["version"] == "edited"


def test_load_seeded_profile_sets_id(tmp_path: Path) -> None:
    profiles = tmp_path / "profiles"
    ensure_seed(profiles)
    p = load_profile("example-beat", profiles_dir=profiles)
    assert p.id == "example-beat"
    assert p.name == "example-beat"
    assert p.version
    assert "personb" in p.keywords  # keywords are lowercased


def test_load_lowercases_keywords(tmp_path: Path) -> None:
    profiles = tmp_path / "profiles"
    _write(profiles, "custom", version="v9", keywords=["Love", "SONG"])
    p = load_profile("custom", profiles_dir=profiles)
    assert p.version == "v9"
    assert p.keywords == ["love", "song"]


def test_load_bad_id_raises(tmp_path: Path) -> None:
    for bad in ["Bad", "has_space ", "under_score", "-lead", "x" * 41, ""]:
        with pytest.raises(ValueError):
            load_profile(bad, profiles_dir=tmp_path)


def test_load_missing_required_field_raises(tmp_path: Path) -> None:
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    (profiles / "bad.json").write_text(json.dumps({"name": "x"}), encoding="utf-8")
    with pytest.raises(ValueError):
        load_profile("bad", profiles_dir=profiles)


def test_list_profiles_sorted_and_skips_malformed(tmp_path: Path) -> None:
    profiles = tmp_path / "profiles"
    _write(profiles, "zebra")
    _write(profiles, "alpha")
    (profiles / "broken.json").write_text("{not json", encoding="utf-8")
    (profiles / "no-fields.json").write_text(
        json.dumps({"name": "x"}), encoding="utf-8"
    )
    ids = [p.id for p in list_profiles(profiles)]
    assert ids == ["alpha", "zebra"]


def test_list_profiles_empty_dir(tmp_path: Path) -> None:
    assert list_profiles(tmp_path / "missing") == []
