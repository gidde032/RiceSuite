"""Handoff writer (D8, FR-9, SPEC §7) + `handoff` CLI + UI endpoint."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ricesearcher import cli
from ricesearcher.config import Config
from ricesearcher.handoff import writer as writer_mod
from ricesearcher.handoff.writer import (
    HandoffEntry,
    HandoffError,
    hand_off_selected,
    write_batch,
)
from ricesearcher.library.store import Library
from ricesearcher.models import CandidateSlice, SliceStatus, Source, SourceKind


class FakeExtractor:
    """Writes a marker file instead of running ffmpeg; records calls."""

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[tuple] = []

    def extract(self, source: Path, start: float, end: float, dest: Path) -> None:
        self.calls.append((Path(source), start, end, Path(dest)))
        if self.fail:
            raise RuntimeError("ffmpeg boom")
        dest.write_bytes(f"clip {start}-{end}".encode())


def _entry(tmp: Path, position: int = 1) -> HandoffEntry:
    src = tmp / "source.mp4"
    src.write_bytes(b"source-media")
    return HandoffEntry(
        position=position,
        source_media=src,
        source_ref="https://y/x",
        source_title="Interview",
        published_at="20260101",
        pad_in=8.0,
        pad_out=42.0,
        target_in=10.0,
        target_out=40.0,
        transcript="a moment",
        score=0.8,
        rationale="good",
        rights_risk="med",
        beat_profile_version="v1",
    )


# -- write_batch --------------------------------------------------------------


def test_write_batch_layout_and_manifest_last(tmp_path: Path) -> None:
    root = tmp_path / "handoff"
    ex = FakeExtractor()
    res = write_batch([_entry(tmp_path)], extractor=ex, root=root)
    batch_dir = root / res["batch_id"]
    assert res["clip_count"] == 1
    assert (batch_dir / "clip_1.mp4").is_file()
    manifest = json.loads((batch_dir / "manifest.json").read_text())
    assert manifest["producer"] == "ricesearcher"
    assert manifest["schema_version"] == 1
    clip = manifest["clips"][0]
    assert clip["file"] == "clip_1.mp4"
    assert clip["source_window"] == {
        "pad_in": 8.0,
        "pad_out": 42.0,
        "target_in": 10.0,
        "target_out": 40.0,
    }
    # Clip-relative intended cut: target minus pad_in, within [0, duration].
    assert clip["clip"]["duration"] == 34.0
    assert clip["clip"]["target_in"] == 2.0
    assert clip["clip"]["target_out"] == 32.0
    assert clip["rights_risk"] == "med"
    # No leftover temp manifest.
    assert not (batch_dir / "manifest.json.tmp").exists()


def test_write_batch_rejects_empty_and_dup_positions(tmp_path: Path) -> None:
    ex = FakeExtractor()
    with pytest.raises(HandoffError):
        write_batch([], extractor=ex, root=tmp_path / "h")
    e1, e2 = _entry(tmp_path, 1), _entry(tmp_path, 1)
    with pytest.raises(HandoffError):
        write_batch([e1, e2], extractor=ex, root=tmp_path / "h")


def test_write_batch_leaves_no_orphan_on_failure(tmp_path: Path) -> None:
    root = tmp_path / "handoff"
    ex = FakeExtractor(fail=True)
    with pytest.raises(RuntimeError):
        write_batch([_entry(tmp_path)], extractor=ex, root=root)
    # The batch dir was cleaned up — no half-written, manifest-less artifact.
    assert list(root.iterdir()) == []


# -- hand_off_selected --------------------------------------------------------


def _lib_with_selected(tmp_path: Path) -> tuple[Config, Library]:
    cfg = Config(data_dir=tmp_path / "data", handoff_dir=tmp_path / "handoff")
    cfg.ensure_dirs()
    media = cfg.cache_dir / "m.mp4"
    media.write_bytes(b"media-bytes")
    lib = Library(cfg.db_path)
    lib.upsert_source(
        Source(
            id="s1",
            kind=SourceKind.YOUTUBE,
            ref="https://y/x",
            media_path=str(media),
            title="Ep",
        )
    )
    lib.upsert_slices(
        [
            CandidateSlice(
                id="a",
                source_id="s1",
                pad_in=0,
                pad_out=30,
                target_in=5,
                target_out=25,
                transcript_span="hi",
                score=0.9,
                status=SliceStatus.SELECTED,
            ),
            CandidateSlice(
                id="b",
                source_id="s1",
                pad_in=40,
                pad_out=70,
                target_in=45,
                target_out=65,
                transcript_span="yo",
                score=0.4,
                status=SliceStatus.CANDIDATE,
            ),
        ]
    )
    return cfg, lib


def test_hand_off_selected_writes_and_marks(tmp_path: Path) -> None:
    cfg, lib = _lib_with_selected(tmp_path)
    ex = FakeExtractor()
    res = hand_off_selected(lib, extractor=ex, config=cfg)
    assert res["clip_count"] == 1  # only the selected slice
    # The selected slice is now handed_off; the candidate is untouched.
    assert lib.get_slice("a").status is SliceStatus.HANDED_OFF
    assert lib.get_slice("b").status is SliceStatus.CANDIDATE
    lib.close()


def test_hand_off_nothing_selected_is_noop(tmp_path: Path) -> None:
    cfg = Config(data_dir=tmp_path / "d", handoff_dir=tmp_path / "h")
    cfg.ensure_dirs()
    with Library(cfg.db_path) as lib:
        res = hand_off_selected(lib, extractor=FakeExtractor(), config=cfg)
    assert res == {"batch_id": None, "clip_count": 0}


def test_hand_off_failure_does_not_mark(tmp_path: Path) -> None:
    cfg, lib = _lib_with_selected(tmp_path)
    with pytest.raises(RuntimeError):
        hand_off_selected(lib, extractor=FakeExtractor(fail=True), config=cfg)
    # Nothing marked handed_off — the batch is retryable.
    assert lib.get_slice("a").status is SliceStatus.SELECTED
    assert list((cfg.handoff_dir).iterdir()) == []  # no orphan
    lib.close()


# -- CLI + UI -----------------------------------------------------------------


def test_cli_handoff(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("RICESEARCHER_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("RICESEARCHER_HANDOFF_DIR", str(tmp_path / "handoff"))
    monkeypatch.setattr(writer_mod, "FfmpegClipExtractor", FakeExtractor)
    from ricesearcher.config import load_config

    cfg = load_config()
    cfg.ensure_dirs()
    media = cfg.cache_dir / "m.mp4"
    media.write_bytes(b"x")
    with Library(cfg.db_path) as lib:
        lib.upsert_source(
            Source(
                id="s1",
                kind=SourceKind.YOUTUBE,
                ref="r",
                media_path=str(media),
                title="Ep",
            )
        )
        lib.upsert_slices(
            [
                CandidateSlice(
                    id="a",
                    source_id="s1",
                    pad_in=0,
                    pad_out=30,
                    target_in=5,
                    target_out=25,
                    transcript_span="hi",
                    score=0.9,
                    status=SliceStatus.SELECTED,
                )
            ]
        )
    assert cli.main(["handoff"]) == 0
    assert "handed off 1 clip" in capsys.readouterr().out


def test_cli_handoff_nothing_selected(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("RICESEARCHER_DATA_DIR", str(tmp_path / "data"))
    assert cli.main(["handoff"]) == 0
    assert "no selected slices" in capsys.readouterr().out


def test_ui_handoff_endpoint(tmp_path: Path, monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from ricesearcher.web.app import create_app

    monkeypatch.setattr(writer_mod, "FfmpegClipExtractor", FakeExtractor)
    cfg, lib = _lib_with_selected(tmp_path)
    lib.close()
    client = TestClient(create_app(cfg))
    r = client.post("/api/handoff")
    assert r.status_code == 200
    assert r.json()["clip_count"] == 1
    # slice a is now handed_off (gone from the selected view)
    assert client.get("/api/slices?status=selected").json() == []


# -- Repair regressions (H1, L1, L2, L3) --------------------------------------


def test_h1_all_selected_marked_atomically(tmp_path: Path) -> None:
    cfg, lib = _lib_with_selected(tmp_path)
    # add a second selected slice
    lib.upsert_slices(
        [
            CandidateSlice(
                id="c",
                source_id="s1",
                pad_in=0,
                pad_out=20,
                target_in=2,
                target_out=18,
                transcript_span="c",
                score=0.7,
                status=SliceStatus.SELECTED,
            ),
        ]
    )
    hand_off_selected(lib, extractor=FakeExtractor(), config=cfg)
    assert lib.get_slice("a").status is SliceStatus.HANDED_OFF
    assert lib.get_slice("c").status is SliceStatus.HANDED_OFF
    lib.close()


def test_l1_inverted_window_rejected(tmp_path: Path) -> None:
    e = _entry(tmp_path)
    e.target_in, e.target_out = 40.0, 10.0  # reversed
    with pytest.raises(HandoffError):
        write_batch([e], extractor=FakeExtractor(), root=tmp_path / "h")


def test_l2_rmtree_failure_does_not_mask_original(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        writer_mod.shutil,
        "rmtree",
        lambda *a, **k: (_ for _ in ()).throw(OSError("busy")),
    )
    with pytest.raises(RuntimeError):  # the extractor error, NOT the rmtree OSError
        write_batch(
            [_entry(tmp_path)], extractor=FakeExtractor(fail=True), root=tmp_path / "h"
        )


def test_l3_batch_id_collision_is_handoff_error(tmp_path: Path, monkeypatch) -> None:
    import datetime

    fixed = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)
    monkeypatch.setattr(writer_mod, "_now", lambda: fixed)
    monkeypatch.setattr(
        writer_mod.uuid, "uuid4", lambda: __import__("uuid").UUID(int=0)
    )
    root = tmp_path / "h"
    write_batch([_entry(tmp_path)], extractor=FakeExtractor(), root=root)
    with pytest.raises(HandoffError):
        write_batch([_entry(tmp_path)], extractor=FakeExtractor(), root=root)
