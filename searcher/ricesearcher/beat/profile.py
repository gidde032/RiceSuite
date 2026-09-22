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
import os
import re
import tempfile
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

logger = logging.getLogger(__name__)

# A profile id is the file stem. Lowercase, digits, and hyphens; 1-40 chars.
# Anchored so the pattern is safe-by-default: an unanchored id would let a future
# ``.match()`` caller accept a trailing invalid suffix that then flows into a
# filesystem path join and a SQL parameter (review finding E). Every call site
# uses ``.fullmatch()`` today; the anchors make ``.match()``/``.search()`` safe too.
PROFILE_ID_PATTERN = re.compile(r"\A[a-z0-9][a-z0-9-]{0,39}\Z")

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


def _packaged_default_bytes() -> bytes:
    return (
        resources.files("ricesearcher.beat")
        .joinpath("profiles/default.json")
        .read_bytes()
    )


def _resolve_dir(profiles_dir: str | Path | None) -> Path:
    if profiles_dir is not None:
        return Path(profiles_dir)
    from ricesearcher.config import load_config

    return load_config().profiles_dir


def validate_profile_id(profile_id: str) -> str:
    """Return a profile id only when the whole value matches ADR-002."""
    if not PROFILE_ID_PATTERN.fullmatch(profile_id):
        raise ValueError(f"invalid profile id: {profile_id!r}")
    return profile_id


def _string_list(data: dict, field_name: str, path: Path) -> list[str]:
    value = data.get(field_name, [])
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(
            f"beat profile {path} field {field_name!r} must be a list of strings"
        )
    return value


def _parse_profile(path: Path, profile_id: str) -> BeatProfile:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"beat profile {path} must contain a JSON object")
    missing = {"version", "name", "brief"} - data.keys()
    if missing:
        raise ValueError(f"beat profile {path} missing fields: {sorted(missing)}")
    for field_name in ("version", "name", "brief"):
        if not isinstance(data[field_name], str):
            raise ValueError(
                f"beat profile {path} field {field_name!r} must be a string"
            )
    return BeatProfile(
        id=profile_id,
        version=data["version"],
        name=data["name"],
        brief=data["brief"],
        keywords=[item.lower() for item in _string_list(data, "keywords", path)],
        positive_examples=_string_list(data, "positive_examples", path),
        negative_examples=_string_list(data, "negative_examples", path),
    )


def load_profile(
    profile_id: str, *, profiles_dir: str | Path | None = None
) -> BeatProfile:
    """Load the named profile from the profiles directory.

    Raise ``ValueError`` for a bad id. Raise the underlying error if the file is
    missing or malformed (the caller asked for this exact profile).
    """
    validate_profile_id(profile_id)
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
        if not PROFILE_ID_PATTERN.fullmatch(profile_id):
            logger.warning("skipping profile with invalid id: %s", path)
            continue
        try:
            profiles.append(_parse_profile(path, profile_id))
        except (ValueError, json.JSONDecodeError, OSError) as exc:
            logger.warning("skipping malformed profile %s: %s", path, exc)
    return sorted(profiles, key=lambda p: p.id)


def ensure_seed(profiles_dir: str | Path | None = None) -> None:
    """Atomically install ``example-beat.json`` when that profile is absent."""
    directory = _resolve_dir(profiles_dir)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{_SEED_PROFILE_ID}.json"
    if target.exists():
        return

    payload = _packaged_default_bytes()
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{_SEED_PROFILE_ID}.", suffix=".tmp", dir=directory
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temp_path, target)
        except FileExistsError:
            # Another process completed the same seed while this one was writing.
            pass
    finally:
        temp_path.unlink(missing_ok=True)
