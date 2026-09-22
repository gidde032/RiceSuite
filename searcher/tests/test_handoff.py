"""Handoff writer (D8, FR-9, SPEC §7) + `handoff` CLI + UI endpoint."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ricesearcher import cli
from ricesearcher.config import Config
from ricesearcher.handoff import writer as writer_mod
from ricesearcher.handoff.extract import ClipExtractError
from ricesearcher.handoff.writer import (
    HandoffEntry,
    HandoffError,
    hand_off_selected,
    write_batch,
)
from ricesearcher.library.store import LEGACY_PROFILE_ID, Library
from ricesearcher.models import (
    CandidateSlice,
    SliceStatus,
    Source,
    SourceKind,
    TranscriptWord,
)


class FakeExtractor:
    """Writes a marker file instead of running ffmpeg; records calls."""

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[tuple] = []

    def extract(self, source: Path, start: float, end: float, dest: Path) -> float:
        self.calls.append((Path(source), start, end, Path(dest)))
        if self.fail:
            raise RuntimeError("ffmpeg boom")
        dest.write_bytes(f"clip {start}-{end}".encode())
        return end - start


def _entry(tmp: Path, position: int = 1) -> HandoffEntry:
    src = tmp / "source.mp4"
    src.write_bytes(b"source-media")
    return HandoffEntry(
        position=position,
        source_media=src,
        source_ref="https://y/x",
        source_title="Interview",
        published_at="20260101",
        target_in=10.0,
        target_out=40.0,
        transcript_words=[TranscriptWord("a moment", 10.0, 40.0)],
        score=0.8,
        rationale="good",
        rights_risk="med",
        beat_profile_version="v1",
        profile_id="example-beat",
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
        "pad_in": 10.0,
        "pad_out": 40.0,
        "target_in": 10.0,
        "target_out": 40.0,
    }
    assert clip["clip"] == {"duration": 30.0, "target_in": 0.0, "target_out": 30.0}
    assert ex.calls == [
        (tmp_path / "source.mp4", 10.0, 40.0, root / res["batch_id"] / "clip_1.mp4")
    ]
    assert clip["rights_risk"] == "med"
    # No leftover temp manifest.
    assert not (batch_dir / "manifest.json.tmp").exists()


def test_write_batch_accepts_target_outside_original_candidate_pad(
    tmp_path: Path,
) -> None:
    entry = _entry(tmp_path)
    entry.target_in = 5.0
    entry.target_out = 45.0

    root = tmp_path / "handoff"
    ex = FakeExtractor()
    res = write_batch([entry], extractor=ex, root=root)
    manifest = json.loads((root / res["batch_id"] / "manifest.json").read_text())

    assert ex.calls[0][1:3] == (5.0, 45.0)
    assert manifest["clips"][0]["source_window"] == {
        "pad_in": 5.0,
        "pad_out": 45.0,
        "target_in": 5.0,
        "target_out": 45.0,
    }
    assert manifest["clips"][0]["clip"] == {
        "duration": 40.0,
        "target_in": 0.0,
        "target_out": 40.0,
    }


def test_entry_for_rebuilds_transcript_from_selected_interval(tmp_path: Path) -> None:
    source = Source(
        id="s1",
        kind=SourceKind.LOCAL,
        ref="/source.mp4",
        media_path=str(tmp_path / "source.mp4"),
        words=[
            TranscriptWord("ends-at-in", 1.0, 2.0),
            TranscriptWord("starts-at-in", 2.0, 3.0),
            TranscriptWord("middle", 3.0, 5.0),
            TranscriptWord("ends-at-out", 5.0, 6.0),
            TranscriptWord("starts-at-out", 6.0, 7.0),
        ],
    )
    slice_ = CandidateSlice(
        id="s1:example-beat:2000-6000",
        source_id="s1",
        pad_in=0.0,
        pad_out=10.0,
        target_in=2.0,
        target_out=6.0,
        transcript_span="stale scoring context",
    )

    entry = writer_mod._entry_for(slice_, source, 1)
    assert entry.transcript == "starts-at-in middle ends-at-out"

    slice_.target_in = 3.0
    slice_.target_out = 5.0
    narrowed = writer_mod._entry_for(slice_, source, 1)
    assert narrowed.transcript == "middle"


@pytest.mark.parametrize(
    ("target_in", "target_out"),
    [(-0.1, 1.0), (1.0, 1.0), (2.0, 1.0), (0.0, float("inf"))],
)
def test_write_batch_rejects_invalid_target_window(
    tmp_path: Path, target_in: float, target_out: float
) -> None:
    entry = _entry(tmp_path)
    entry.target_in = target_in
    entry.target_out = target_out
    with pytest.raises(HandoffError):
        write_batch([entry], extractor=FakeExtractor(), root=tmp_path / "h")


def test_manifest_clip_carries_profile_id(tmp_path: Path) -> None:
    root = tmp_path / "handoff"
    res = write_batch([_entry(tmp_path)], extractor=FakeExtractor(), root=root)
    manifest = json.loads((root / res["batch_id"] / "manifest.json").read_text())
    assert manifest["schema_version"] == 1  # additive change; version unchanged
    assert manifest["clips"][0]["profile_id"] == "example-beat"


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


def test_write_batch_cleans_up_on_keyboard_interrupt(tmp_path: Path) -> None:
    # A Ctrl-C while ffmpeg is extracting a clip raises KeyboardInterrupt, which
    # is a BaseException — an `except Exception` cleanup would skip and orphan a
    # manifest-less partial batch. Cleanup must still run on interrupt.
    root = tmp_path / "handoff"

    class InterruptOnSecond(FakeExtractor):
        def extract(self, source: Path, start: float, end: float, dest: Path) -> float:
            if self.calls:  # first clip already written; interrupt the second
                self.calls.append((Path(source), start, end, Path(dest)))
                raise KeyboardInterrupt
            return super().extract(source, start, end, dest)

    entries = [_entry(tmp_path, 1), _entry(tmp_path, 2)]
    with pytest.raises(KeyboardInterrupt):
        write_batch(entries, extractor=InterruptOnSecond(), root=root)
    # The partially-written, manifest-less batch dir was removed, not orphaned.
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
            words=[
                TranscriptWord("before", 1, 5),
                TranscriptWord("selected", 5, 10),
                TranscriptWord("words", 10, 25),
                TranscriptWord("after", 25, 30),
            ],
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
                profile_id=LEGACY_PROFILE_ID,
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
                profile_id=LEGACY_PROFILE_ID,
            ),
        ]
    )
    return cfg, lib


def test_hand_off_selected_writes_and_marks(tmp_path: Path) -> None:
    cfg, lib = _lib_with_selected(tmp_path)
    ex = FakeExtractor()
    res = hand_off_selected(
        lib,
        extractor=ex,
        duration_prober=lambda _path: 30.0,
        config=cfg,
        profile_id=LEGACY_PROFILE_ID,
    )
    assert res["clip_count"] == 1  # only the selected slice
    batch_dir = next(p for p in cfg.handoff_dir.iterdir() if p.is_dir())
    manifest = json.loads((batch_dir / "manifest.json").read_text())
    assert manifest["clips"][0]["transcript"] == "selected words"
    # The selected slice is now handed_off; the candidate is untouched.
    assert lib.get_slice("a").status is SliceStatus.HANDED_OFF
    assert lib.get_slice("b").status is SliceStatus.CANDIDATE
    lib.close()


def test_hand_off_selected_requires_profile_id(tmp_path: Path) -> None:
    # FA-1: profile_id is a required keyword-only str. Without it the handoff
    # must raise, never fall back to handing off every profile's selected rows.
    cfg, lib = _lib_with_selected(tmp_path)
    with pytest.raises(TypeError):
        hand_off_selected(lib, extractor=FakeExtractor(), config=cfg)
    lib.close()


def test_hand_off_nothing_selected_is_noop(tmp_path: Path) -> None:
    cfg = Config(data_dir=tmp_path / "d", handoff_dir=tmp_path / "h")
    cfg.ensure_dirs()
    with Library(cfg.db_path) as lib:
        res = hand_off_selected(
            lib, extractor=FakeExtractor(), config=cfg, profile_id=LEGACY_PROFILE_ID
        )
    assert res == {"batch_id": None, "clip_count": 0}


def test_hand_off_failure_does_not_mark(tmp_path: Path) -> None:
    cfg, lib = _lib_with_selected(tmp_path)
    with pytest.raises(RuntimeError):
        hand_off_selected(
            lib,
            extractor=FakeExtractor(fail=True),
            duration_prober=lambda _path: 30.0,
            config=cfg,
            profile_id=LEGACY_PROFILE_ID,
        )
    # Nothing marked handed_off — the batch is retryable.
    assert lib.get_slice("a").status is SliceStatus.SELECTED
    assert list((cfg.handoff_dir).iterdir()) == []  # no orphan
    lib.close()


def test_hand_off_rejects_window_entirely_beyond_source_before_terminal_mark(
    tmp_path: Path,
) -> None:
    # Slice "a" spans [5, 25]; a 3s source starts before target_in, so the window
    # is entirely unexportable and must fail closed (not silently clamp to empty).
    cfg, lib = _lib_with_selected(tmp_path)
    with pytest.raises(HandoffError, match="is at or before target_in"):
        hand_off_selected(
            lib,
            extractor=FakeExtractor(),
            duration_prober=lambda _path: 3.0,
            config=cfg,
            profile_id=LEGACY_PROFILE_ID,
        )
    assert lib.get_slice("a").status is SliceStatus.SELECTED
    assert not cfg.handoff_dir.exists() or not any(cfg.handoff_dir.iterdir())
    lib.close()


def test_hand_off_rejects_unverifiable_source_before_terminal_mark(
    tmp_path: Path,
) -> None:
    cfg, lib = _lib_with_selected(tmp_path)

    def unavailable(_path: Path) -> float:
        raise OSError("ffprobe unavailable")

    with pytest.raises(HandoffError, match="source duration could not be verified"):
        hand_off_selected(
            lib,
            extractor=FakeExtractor(),
            duration_prober=unavailable,
            config=cfg,
            profile_id=LEGACY_PROFILE_ID,
        )
    assert lib.get_slice("a").status is SliceStatus.SELECTED
    assert not cfg.handoff_dir.exists() or not any(cfg.handoff_dir.iterdir())
    lib.close()


@pytest.mark.parametrize("duration", [0.0, -1.0, float("nan"), float("inf")])
def test_hand_off_rejects_invalid_source_duration(
    tmp_path: Path, duration: float
) -> None:
    cfg, lib = _lib_with_selected(tmp_path)
    with pytest.raises(HandoffError, match="source duration could not be verified"):
        hand_off_selected(
            lib,
            extractor=FakeExtractor(),
            duration_prober=lambda _path: duration,
            config=cfg,
            profile_id=LEGACY_PROFILE_ID,
        )
    assert lib.get_slice("a").status is SliceStatus.SELECTED
    assert not cfg.handoff_dir.exists() or not any(cfg.handoff_dir.iterdir())
    lib.close()


def test_hand_off_probes_each_source_once(tmp_path: Path) -> None:
    cfg, lib = _lib_with_selected(tmp_path)
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
                profile_id=LEGACY_PROFILE_ID,
            ),
        ]
    )
    probed: list[Path] = []

    def probe(path: Path) -> float:
        probed.append(path)
        return 30.0

    result = hand_off_selected(
        lib,
        extractor=FakeExtractor(),
        duration_prober=probe,
        config=cfg,
        profile_id=LEGACY_PROFILE_ID,
    )
    assert result["clip_count"] == 2
    assert probed == [Path(lib.get_source("s1").media_path)]
    lib.close()


# -- CLI + UI -----------------------------------------------------------------


def test_cli_handoff(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("RICESEARCHER_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("RICESEARCHER_HANDOFF_DIR", str(tmp_path / "handoff"))
    monkeypatch.setattr(writer_mod, "FfmpegClipExtractor", FakeExtractor)
    monkeypatch.setattr(writer_mod, "ffprobe_duration", lambda _path: 30.0)
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
                    profile_id=LEGACY_PROFILE_ID,
                )
            ]
        )
    assert cli.main(["handoff", "--profile", "example-beat"]) == 0
    assert "handed off 1 clip" in capsys.readouterr().out


def test_cli_handoff_nothing_selected(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("RICESEARCHER_DATA_DIR", str(tmp_path / "data"))
    assert cli.main(["handoff", "--profile", "example-beat"]) == 0
    assert "no selected slices" in capsys.readouterr().out


def test_ui_handoff_endpoint(tmp_path: Path, monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from ricesearcher.web.app import create_app

    monkeypatch.setattr(writer_mod, "FfmpegClipExtractor", FakeExtractor)
    monkeypatch.setattr(writer_mod, "ffprobe_duration", lambda _path: 30.0)
    cfg, lib = _lib_with_selected(tmp_path)
    lib.close()
    client = TestClient(create_app(cfg))
    r = client.post("/api/handoff", json={"profile": LEGACY_PROFILE_ID})
    assert r.status_code == 200
    assert r.json()["clip_count"] == 1
    # slice a is now handed_off (gone from the selected view)
    assert (
        client.get(f"/api/slices?profile={LEGACY_PROFILE_ID}&status=selected").json()
        == []
    )


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
                profile_id=LEGACY_PROFILE_ID,
            ),
        ]
    )
    hand_off_selected(
        lib,
        extractor=FakeExtractor(),
        duration_prober=lambda _path: 30.0,
        config=cfg,
        profile_id=LEGACY_PROFILE_ID,
    )
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


def test_h2_concurrent_handoff_delivers_once(tmp_path: Path, monkeypatch) -> None:
    import threading
    import time

    from fastapi.testclient import TestClient

    from ricesearcher.web.app import create_app

    class SlowExtractor(FakeExtractor):
        def extract(self, source, start, end, dest):
            time.sleep(0.15)  # widen the overlap window
            return super().extract(source, start, end, dest)

    monkeypatch.setattr(writer_mod, "FfmpegClipExtractor", SlowExtractor)
    monkeypatch.setattr(writer_mod, "ffprobe_duration", lambda _path: 30.0)
    cfg, lib = _lib_with_selected(tmp_path)
    lib.close()
    client = TestClient(create_app(cfg))
    results: list[dict] = []
    barrier = threading.Barrier(2)

    def fire() -> None:
        barrier.wait()
        results.append(
            client.post("/api/handoff", json={"profile": LEGACY_PROFILE_ID}).json()
        )

    threads = [threading.Thread(target=fire) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    counts = sorted(r["clip_count"] for r in results)
    assert counts == [0, 1]  # delivered exactly once, not twice
    assert len(list(cfg.handoff_dir.iterdir())) == 1  # a single batch on disk


def test_concurrent_direct_handoff_delivers_once(tmp_path: Path) -> None:
    import threading
    import time

    class SlowExtractor(FakeExtractor):
        def extract(self, source, start, end, dest):
            time.sleep(0.15)
            return super().extract(source, start, end, dest)

    cfg, lib = _lib_with_selected(tmp_path)
    lib.close()
    barrier = threading.Barrier(2)
    results: list[dict] = []

    def fire() -> None:
        with Library(cfg.db_path) as thread_lib:
            barrier.wait()
            results.append(
                hand_off_selected(
                    thread_lib,
                    extractor=SlowExtractor(),
                    duration_prober=lambda _path: 30.0,
                    config=cfg,
                    profile_id=LEGACY_PROFILE_ID,
                )
            )

    threads = [threading.Thread(target=fire) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(result["clip_count"] for result in results) == [0, 1]
    assert len(list(cfg.handoff_dir.iterdir())) == 1


# -- Review-repair regressions (findings A, B1, B2, C) ------------------------


def test_b2_target_out_past_source_is_clamped_not_batch_rejected(
    tmp_path: Path,
) -> None:
    # Finding B2: slice "a" spans [5, 25]; the true source is 20s. target_out
    # runs 5s past the media end (e.g. an ASR word end beyond the container
    # duration). The batch must still deliver, clamped to the source end, rather
    # than 409-ing every selected clip. (Before the fix this raised
    # "exceeds source duration".)
    cfg, lib = _lib_with_selected(tmp_path)
    res = hand_off_selected(
        lib,
        extractor=FakeExtractor(),
        duration_prober=lambda _path: 20.0,
        config=cfg,
        profile_id=LEGACY_PROFILE_ID,
    )
    assert res["clip_count"] == 1
    batch_dir = next(p for p in cfg.handoff_dir.iterdir() if p.is_dir())
    clip = json.loads((batch_dir / "manifest.json").read_text())["clips"][0]
    # Export end clamped to the verified source duration (20), not the review 25.
    assert clip["source_window"]["target_out"] == 20.0
    assert clip["clip"]["duration"] == 15.0
    assert lib.get_slice("a").status is SliceStatus.HANDED_OFF
    lib.close()


def test_b1_manifest_records_measured_not_requested_duration(tmp_path: Path) -> None:
    # Finding B1: if ffmpeg produces a shorter file than requested, the manifest
    # must report the MEASURED clip length, never the requested one. (Before the
    # fix the manifest always echoed target_out - target_in.)
    class ShortExtractor(FakeExtractor):
        def extract(self, source, start, end, dest):
            super().extract(source, start, end, dest)
            return (end - start) - 4.0  # source ended early: 4s short

    cfg, lib = _lib_with_selected(tmp_path)
    hand_off_selected(
        lib,
        extractor=ShortExtractor(),
        duration_prober=lambda _path: 100.0,
        config=cfg,
        profile_id=LEGACY_PROFILE_ID,
    )
    batch_dir = next(p for p in cfg.handoff_dir.iterdir() if p.is_dir())
    clip = json.loads((batch_dir / "manifest.json").read_text())["clips"][0]
    # Slice "a" is [5, 25] → requested 20s, measured 16s.
    assert clip["clip"]["duration"] == 16.0
    assert clip["source_window"]["target_out"] == 21.0  # target_in 5 + measured 16
    lib.close()


def test_manifest_transcript_stops_at_measured_output_end(tmp_path: Path) -> None:
    class OneSecondExtractor(FakeExtractor):
        def extract(self, source, start, end, dest):
            super().extract(source, start, end, dest)
            return 1.0

    cfg, lib = _lib_with_selected(tmp_path)
    hand_off_selected(
        lib,
        extractor=OneSecondExtractor(),
        duration_prober=lambda _path: 30.0,
        config=cfg,
        profile_id=LEGACY_PROFILE_ID,
    )
    manifest_path = next(cfg.handoff_dir.glob("batch_*/manifest.json"))
    clip = json.loads(manifest_path.read_text())["clips"][0]
    assert clip["source_window"]["target_out"] == 6.0
    assert clip["transcript"] == "selected"
    lib.close()


@pytest.mark.parametrize("measured", [0.0, -1.0, float("nan"), float("inf")])
def test_write_batch_rejects_invalid_measured_duration(
    tmp_path: Path, measured: float
) -> None:
    class InvalidDurationExtractor(FakeExtractor):
        def extract(self, source, start, end, dest):
            super().extract(source, start, end, dest)
            return measured

    root = tmp_path / "handoff"
    with pytest.raises(HandoffError, match="invalid measured duration"):
        write_batch([_entry(tmp_path)], extractor=InvalidDurationExtractor(), root=root)
    assert list(root.iterdir()) == []


def test_hand_off_rejects_nonfinite_target_before_clamping(tmp_path: Path) -> None:
    cfg, lib = _lib_with_selected(tmp_path)
    selected = lib.get_slice("a")
    assert selected is not None
    selected.target_out = float("inf")
    lib.upsert_slices([selected])
    extractor = FakeExtractor()

    with pytest.raises(HandoffError, match="invalid window"):
        hand_off_selected(
            lib,
            extractor=extractor,
            duration_prober=lambda _path: 30.0,
            config=cfg,
            profile_id=LEGACY_PROFILE_ID,
        )

    assert extractor.calls == []
    assert lib.get_slice("a").status is SliceStatus.SELECTED
    assert not cfg.handoff_dir.exists() or not any(cfg.handoff_dir.iterdir())
    lib.close()


def test_window_change_during_encode_discards_unpublished_batch(
    tmp_path: Path,
) -> None:
    import threading

    cfg, lib = _lib_with_selected(tmp_path)
    lib.close()
    started = threading.Event()
    release = threading.Event()

    class GatedExtractor(FakeExtractor):
        def extract(self, source, start, end, dest):
            started.set()
            assert release.wait(10)
            return super().extract(source, start, end, dest)

    result: dict = {}

    def run_handoff() -> None:
        with Library(cfg.db_path) as worker_lib:
            result.update(
                hand_off_selected(
                    worker_lib,
                    extractor=GatedExtractor(),
                    duration_prober=lambda _path: 30.0,
                    config=cfg,
                    profile_id=LEGACY_PROFILE_ID,
                )
            )

    worker = threading.Thread(target=run_handoff)
    worker.start()
    assert started.wait(5)
    with Library(cfg.db_path) as reviewer:
        assert reviewer.update_slice_window("a", 6.0, 24.0)
    release.set()
    worker.join(10)

    assert result == {"batch_id": None, "clip_count": 0}
    assert list(cfg.handoff_dir.glob("*/manifest.json")) == []
    with Library(cfg.db_path) as check:
        selected = check.get_slice("a")
        assert selected is not None
        assert selected.status is SliceStatus.SELECTED
        assert (selected.target_in, selected.target_out) == (6.0, 24.0)


def test_concurrent_handoffs_do_not_publish_before_arbitration(
    tmp_path: Path, monkeypatch
) -> None:
    import threading
    from contextlib import contextmanager

    cfg, lib = _lib_with_selected(tmp_path)
    lib.close()
    original_transaction = Library.immediate_transaction
    waiting = 0
    waiting_lock = threading.Lock()
    both_waiting = threading.Event()
    release = threading.Event()

    @contextmanager
    def gated_transaction(self):
        nonlocal waiting
        with waiting_lock:
            waiting += 1
            if waiting == 2:
                both_waiting.set()
        assert release.wait(10)
        with original_transaction(self):
            yield

    monkeypatch.setattr(Library, "immediate_transaction", gated_transaction)
    barrier = threading.Barrier(2)
    results: list[dict] = []

    def run_handoff() -> None:
        with Library(cfg.db_path) as worker_lib:
            barrier.wait()
            results.append(
                hand_off_selected(
                    worker_lib,
                    extractor=FakeExtractor(),
                    duration_prober=lambda _path: 30.0,
                    config=cfg,
                    profile_id=LEGACY_PROFILE_ID,
                )
            )

    workers = [threading.Thread(target=run_handoff) for _ in range(2)]
    for worker in workers:
        worker.start()
    assert both_waiting.wait(5)
    visible_before_arbitration = list(cfg.handoff_dir.glob("*/manifest.json"))
    release.set()
    for worker in workers:
        worker.join(10)

    assert visible_before_arbitration == []
    assert sorted(result["clip_count"] for result in results) == [0, 1]
    assert len(list(cfg.handoff_dir.glob("*/manifest.json"))) == 1


def test_a_concurrent_review_write_not_blocked_during_handoff(tmp_path: Path) -> None:
    # Finding A: the ffmpeg encode must run OUTSIDE the write lock, so a
    # concurrent review write on another connection is not blocked into a
    # `database is locked` error. (Before the fix this raised OperationalError.)
    import threading

    cfg, lib = _lib_with_selected(tmp_path)
    lib.close()  # each connection is single-thread (sqlite check_same_thread)
    reviewer = Library(cfg.db_path)  # the concurrent reviewer's connection
    started = threading.Event()
    release = threading.Event()

    class GatedExtractor(FakeExtractor):
        def extract(self, source, start, end, dest):
            started.set()
            # Hold the "encode" open longer than SQLite's 5s default busy
            # timeout, so pre-fix (lock held across the encode) a concurrent
            # write reliably times out rather than racing the lock release.
            release.wait(30)
            return super().extract(source, start, end, dest)

    out: dict = {}

    def run_handoff() -> None:
        with Library(cfg.db_path) as worker_lib:
            out["res"] = hand_off_selected(
                worker_lib,
                extractor=GatedExtractor(),
                duration_prober=lambda _path: 100.0,
                config=cfg,
                profile_id=LEGACY_PROFILE_ID,
            )

    worker = threading.Thread(target=run_handoff)
    worker.start()
    assert started.wait(5)
    # This write must succeed immediately — no lock is held during the encode.
    assert reviewer.update_slice_status("b", SliceStatus.REVIEWED) is True
    release.set()
    worker.join(10)
    assert out["res"]["clip_count"] == 1
    assert reviewer.get_slice("a").status is SliceStatus.HANDED_OFF
    reviewer.close()


def test_c_unusable_clip_returns_503_not_500(tmp_path: Path, monkeypatch) -> None:
    # Finding C: a ClipExtractError (ffmpeg ran but produced an unusable clip)
    # must surface as a graceful 503 with the slices left selected for retry,
    # not an uncaught 500. (Before the fix do_handoff did not catch it.)
    from fastapi.testclient import TestClient

    from ricesearcher.web.app import create_app

    class BrokenExtractor(FakeExtractor):
        def extract(self, source, start, end, dest):
            raise ClipExtractError("ffmpeg output has no usable duration")

    monkeypatch.setattr(writer_mod, "FfmpegClipExtractor", BrokenExtractor)
    monkeypatch.setattr(writer_mod, "ffprobe_duration", lambda _path: 100.0)
    cfg, lib = _lib_with_selected(tmp_path)
    lib.close()
    client = TestClient(create_app(cfg))
    r = client.post("/api/handoff", json={"profile": LEGACY_PROFILE_ID})
    assert r.status_code == 503
    assert "remain selected" in r.json()["detail"]
    # The slice is still selectable for a retry.
    assert (
        client.get(f"/api/slices?profile={LEGACY_PROFILE_ID}&status=selected").json()
        != []
    )
