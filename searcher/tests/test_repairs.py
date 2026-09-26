"""Fail-before-fix regressions for the PR #10 review findings (C1-C3, S1-S3).

Each test is written to FAIL against the reviewed head and PASS after the repair.
Docs findings (S4 README, S5 handoff dedup) are covered by test_docs.py.
"""

from __future__ import annotations

import sqlite3
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from ricesearcher.acquire.watchfolder import WatchFolderAcquirer
from ricesearcher.library import cache as cache_mod
from ricesearcher.library.cache import MediaCache
from ricesearcher.library.store import Library
from ricesearcher.models import Source, SourceKind, TranscriptWord

# -- C1: concurrent cache puts of identical bytes must not crash --------------


def test_c1_concurrent_put_same_digest_no_crash(tmp_path: Path, monkeypatch) -> None:
    src = tmp_path / "big.mp4"
    src.write_bytes(b"x" * 4096)
    cache = MediaCache(tmp_path / "cache")

    barrier = threading.Barrier(4)
    real_copy = cache_mod.shutil.copy2

    def slow_copy(a, b):
        # Widen the race window so all threads are mid-put together.
        barrier.wait()
        return real_copy(a, b)

    monkeypatch.setattr(cache_mod.shutil, "copy2", slow_copy)

    errors: list[Exception] = []

    def worker() -> None:
        try:
            cache.put(src)
        except Exception as exc:  # noqa: BLE001 - the test records any failure
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"concurrent put crashed: {errors}"
    digest, dest = cache.put(src)
    assert dest.read_bytes() == src.read_bytes()


# -- C2: CLI pull maps any pipeline failure to a clean error + exit 2 ----------


def test_c2_pull_pipeline_error_is_handled(tmp_path: Path, monkeypatch, capsys) -> None:
    from ricesearcher import cli

    monkeypatch.setenv("RICESEARCHER_DATA_DIR", str(tmp_path / "data"))
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"bytes")

    class BoomTranscriber:
        def transcribe(self, media_path: Path):
            raise RuntimeError("whisper blew up")

    monkeypatch.setattr(cli, "_default_acquirers", lambda: [WatchFolderAcquirer()])
    monkeypatch.setattr(cli, "WhisperTranscriber", lambda **_: BoomTranscriber())

    rc = cli.main(["pull", str(media)])
    assert rc == 2
    assert "error" in capsys.readouterr().err.lower()


# -- C3: id-prefix resolution matches literally, not as a LIKE pattern ---------


def _src(sid: str) -> Source:
    return Source(
        id=sid,
        kind=SourceKind.LOCAL,
        ref="/x",
        media_path="/x",
        words=[TranscriptWord("hi", 0.0, 0.1)],
    )


def test_c3_resolve_prefix_treats_wildcards_literally(tmp_path: Path) -> None:
    with Library(tmp_path / "lib.sqlite3") as lib:
        lib.upsert_source(_src("abXd1234"))
        lib.upsert_source(_src("other000"))
        # '_' and '%' must NOT act as SQL wildcards.
        assert lib.resolve_source_id("ab_d") is None
        assert lib.resolve_source_id("ab%1234") is None
        # A real literal prefix still resolves.
        assert lib.resolve_source_id("abXd") == "abXd1234"


# -- S1: the advertised `ricesearcher` command is actually runnable -----------


def test_s1_module_entrypoint_runs(tmp_path: Path) -> None:
    import os

    env = {**os.environ, "RICESEARCHER_DATA_DIR": str(tmp_path / "data")}
    result = subprocess.run(
        [sys.executable, "-m", "ricesearcher", "list"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "empty" in result.stdout


# -- S2: Library closes its connection if migration fails ----------------------


def test_s2_init_closes_connection_on_migration_failure(
    tmp_path: Path, monkeypatch
) -> None:
    opened: list[sqlite3.Connection] = []
    real_connect = sqlite3.connect

    def tracking_connect(*a, **k):
        conn = real_connect(*a, **k)
        opened.append(conn)
        return conn

    monkeypatch.setattr("ricesearcher.library.store.sqlite3.connect", tracking_connect)
    monkeypatch.setattr(
        Library, "_migrate", lambda self: (_ for _ in ()).throw(RuntimeError("boom"))
    )

    with pytest.raises(RuntimeError):
        Library(tmp_path / "lib.sqlite3")

    assert opened, "no connection was opened"
    # A closed connection raises ProgrammingError on use.
    with pytest.raises(sqlite3.ProgrammingError):
        opened[0].execute("SELECT 1")


# -- S3: watch-folder acquirer records a real duration, with safe fallback -----


def test_s3_watchfolder_sets_probed_duration(media_file: Path) -> None:
    acq = WatchFolderAcquirer(duration_prober=lambda p: 12.5)
    got = acq.acquire(str(media_file))
    assert got.duration_s == 12.5


def test_s3_watchfolder_duration_falls_back_to_zero(media_file: Path) -> None:
    def broken(_p):
        raise RuntimeError("no ffprobe")

    acq = WatchFolderAcquirer(duration_prober=broken)
    got = acq.acquire(str(media_file))
    assert got.duration_s == 0.0
