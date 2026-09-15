"""Load a named beat profile (D2, ADR-002).

Profiles are plain JSON files under ``<data_dir>/profiles/<profile_id>.json`` so
they are easy to edit and diff. ``RICESEARCHER_PROFILES_DIR`` overrides the
directory. A packaged ``default.json`` seeds ``example-beat.json`` on first use.
The profile ``id`` (the file stem) and ``version`` are recorded on every scored
slice for provenance.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# A profile id is the file stem. Lowercase, digits, and hyphens; 1-40 chars.
PROFILE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")

# Seed target. Kept in sync with ``store.LEGACY_PROFILE_ID`` (the migration uses
# the same id). Named here so the loader does not import the library layer.
_SEED_PROFILE_ID = "example-beat"


@dataclass(frozen=True)
class BeatProfile:
    version: str
    name: str
    brief: str
    id: str = ""
    keywords: list[str] = field(default_factory=list)
    positive_examples: list[str] = field(default_factory=list)
    negative_examples: list[str] = field(default_factory=list)


def _packaged_default_path() -> Path:
    return Path(__file__).resolve().parent / "profiles" / "default.json"


def _resolve_dir(profiles_dir: str | Path | None) -> Path:
    if profiles_dir is not None:
        return Path(profiles_dir)
    from ricesearcher.config import load_config

    return load_config().profiles_dir


def _check_id(profile_id: str) -> None:
    if not PROFILE_ID_PATTERN.match(profile_id):
        raise ValueError(f"invalid profile id: {profile_id!r}")


def _parse_profile(path: Path, profile_id: str) -> BeatProfile:
    data = json.loads(path.read_text(encoding="utf-8"))
    missing = {"version", "name", "brief"} - data.keys()
    if missing:
        raise ValueError(f"beat profile {path} missing fields: {sorted(missing)}")
    return BeatProfile(
        id=profile_id,
        version=str(data["version"]),
        name=str(data["name"]),
        brief=str(data["brief"]),
        keywords=[str(k).lower() for k in data.get("keywords", [])],
        positive_examples=[str(e) for e in data.get("positive_examples", [])],
        negative_examples=[str(e) for e in data.get("negative_examples", [])],
    )


def load_profile(
    profile_id: str, *, profiles_dir: str | Path | None = None
) -> BeatProfile:
    """Load the named profile from the profiles directory.

    Raise ``ValueError`` for a bad id. Raise the underlying error if the file is
    missing or malformed (the caller asked for this exact profile).
    """
    _check_id(profile_id)
    path = _resolve_dir(profiles_dir) / f"{profile_id}.json"
    return _parse_profile(path, profile_id)


def list_profiles(profiles_dir: str | Path | None = None) -> list[BeatProfile]:
    """Return every valid profile, sorted by id. Malformed files are skipped."""
    directory = _resolve_dir(profiles_dir)
    if not directory.is_dir():
        return []
    profiles: list[BeatProfile] = []
    for path in sorted(directory.glob("*.json")):
        profile_id = path.stem
        if not PROFILE_ID_PATTERN.match(profile_id):
            logger.warning("skipping profile with invalid id: %s", path)
            continue
        try:
            profiles.append(_parse_profile(path, profile_id))
        except (ValueError, json.JSONDecodeError, OSError) as exc:
            logger.warning("skipping malformed profile %s: %s", path, exc)
    return sorted(profiles, key=lambda p: p.id)


def ensure_seed(profiles_dir: str | Path | None = None) -> None:
    """Copy the packaged default to ``example-beat.json`` if the dir has no files."""
    directory = _resolve_dir(profiles_dir)
    directory.mkdir(parents=True, exist_ok=True)
    if any(directory.glob("*.json")):
        return
    shutil.copyfile(_packaged_default_path(), directory / f"{_SEED_PROFILE_ID}.json")
