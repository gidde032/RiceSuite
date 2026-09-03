"""Load a versioned beat profile (D2).

The profile is a plain JSON file so it is easy to edit and diff. A default ships
in ``profiles/default.json``; override with ``RICESEARCHER_BEAT_PROFILE`` or by
passing a path. The ``version`` is recorded on every scored slice for provenance.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

_PROFILE_ENV = "RICESEARCHER_BEAT_PROFILE"


@dataclass(frozen=True)
class BeatProfile:
    version: str
    name: str
    brief: str
    keywords: list[str] = field(default_factory=list)
    positive_examples: list[str] = field(default_factory=list)
    negative_examples: list[str] = field(default_factory=list)


def default_profile_path() -> Path:
    return Path(__file__).resolve().parent / "profiles" / "default.json"


def load_profile(path: str | Path | None = None) -> BeatProfile:
    """Load the beat profile from ``path``, the env override, or the default."""
    resolved = Path(path or os.getenv(_PROFILE_ENV) or default_profile_path())
    data = json.loads(resolved.read_text(encoding="utf-8"))
    missing = {"version", "name", "brief"} - data.keys()
    if missing:
        raise ValueError(f"beat profile {resolved} missing fields: {sorted(missing)}")
    return BeatProfile(
        version=str(data["version"]),
        name=str(data["name"]),
        brief=str(data["brief"]),
        keywords=[str(k).lower() for k in data.get("keywords", [])],
        positive_examples=[str(e) for e in data.get("positive_examples", [])],
        negative_examples=[str(e) for e in data.get("negative_examples", [])],
    )
