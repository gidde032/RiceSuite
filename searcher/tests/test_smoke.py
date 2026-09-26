"""Fast-feedback smoke tier + its checked inventory (bootstrap Step 4).

The smoke set is a contract: ``test_smoke_inventory_is_pinned`` fails if the
number of ``@pytest.mark.smoke`` tests drifts from ``EXPECTED_SMOKE_COUNT``, so
the quick suite can't silently grow or shrink.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import ricesearcher
from ricesearcher.config import load_config
from ricesearcher.library.cache import MediaCache
from ricesearcher.library.store import Library
from tests.smoke_registry import SMOKE_NODEIDS

EXPECTED_SMOKE_COUNT = 5


@pytest.mark.smoke
def test_version_present() -> None:
    assert ricesearcher.__version__


@pytest.mark.smoke
def test_config_paths_under_data_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("RICESEARCHER_DATA_DIR", str(tmp_path / "d"))
    cfg = load_config()
    cfg.ensure_dirs()
    assert cfg.db_path.parent == cfg.data_dir
    assert cfg.cache_dir.is_dir()


@pytest.mark.smoke
def test_cache_roundtrip(tmp_path: Path, media_file: Path) -> None:
    cache = MediaCache(tmp_path / "cache")
    digest, dest = cache.put(media_file)
    assert dest.is_file() and digest


@pytest.mark.smoke
def test_library_opens_and_lists_empty(tmp_path: Path) -> None:
    with Library(tmp_path / "lib.sqlite3") as lib:
        assert lib.list_sources() == []


@pytest.mark.smoke
def test_pull_pipeline_end_to_end(tmp_path, fake_acquirer, fake_transcriber) -> None:
    from ricesearcher.pipeline import pull

    cache = MediaCache(tmp_path / "cache")
    with Library(tmp_path / "lib.sqlite3") as lib:
        src = pull(
            "fake",
            acquirers=[fake_acquirer],
            transcriber=fake_transcriber,
            cache=cache,
            library=lib,
        )
        assert lib.get_source(src.id) is not None


def test_smoke_inventory_is_pinned() -> None:
    # Not itself a smoke test: it guards the smoke set's size.
    assert len(SMOKE_NODEIDS) == EXPECTED_SMOKE_COUNT, (
        f"smoke count drifted to {len(SMOKE_NODEIDS)}; "
        f"update EXPECTED_SMOKE_COUNT deliberately.\n{SMOKE_NODEIDS}"
    )
