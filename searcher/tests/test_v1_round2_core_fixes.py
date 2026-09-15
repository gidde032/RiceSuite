"""Focused Round 2 regressions for scorer, custody, and adapter boundaries."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from ricesearcher.acquire.base import AcquiredSource
from ricesearcher.acquire.ytdlp import YtDlpAcquirer, _normalize_upload_date
from ricesearcher.library.cache import MediaCache, hash_file
from ricesearcher.library.store import Library
from ricesearcher.models import (
    CandidateSlice,
    SliceStatus,
    Source,
    SourceKind,
    TranscriptWord,
)
from ricesearcher.pipeline import pull
from ricesearcher.score.anthropic_scorer import ScorerParseError, parse_response
from ricesearcher.transcribe.whisper import WhisperTranscriber


def test_scorer_requires_numeric_scores_and_nonempty_rationales() -> None:
    for score in (True, "0.5"):
        with pytest.raises(ScorerParseError, match="invalid score"):
            parse_response(
                json.dumps([{"index": 0, "score": score, "rationale": "usable"}]),
                1,
            )

    for rationale in (None, "  "):
        with pytest.raises(ScorerParseError, match="empty rationale"):
            parse_response(
                json.dumps([{"index": 0, "score": 0.5, "rationale": rationale}]),
                1,
            )


def test_scorer_rejects_duplicate_indices() -> None:
    response = (
        '[{"index": 0, "score": 0.5, "rationale": "first"}, '
        '{"index": 0, "score": 0.6, "rationale": "second"}]'
    )
    with pytest.raises(ScorerParseError, match="duplicate index"):
        parse_response(response, 2)


def _source(source_id: str = "source") -> Source:
    return Source(
        id=source_id,
        kind=SourceKind.LOCAL,
        ref="local",
        media_path="/cache/source.mp4",
        words=[TranscriptWord("word", 0.0, 1.0)],
    )


def _slice(slice_id: str, status: SliceStatus, score: float) -> CandidateSlice:
    return CandidateSlice(
        id=slice_id,
        source_id="source",
        pad_in=0.0,
        pad_out=2.0,
        target_in=0.0,
        target_out=1.0,
        transcript_span=slice_id,
        score=score,
        rationale="kept",
        status=status,
        created_at="now",
    )


def test_candidate_replacement_rolls_back_delete_on_insert_failure(
    tmp_path: Path, monkeypatch
) -> None:
    with Library(tmp_path / "library.sqlite3") as library:
        library.upsert_source(_source())
        library.upsert_slices(
            [
                _slice("old", SliceStatus.CANDIDATE, 0.8),
                _slice("human", SliceStatus.REVIEWED, 0.9),
            ]
        )

        def fail_insert(_slices: list[CandidateSlice]) -> None:
            raise RuntimeError("score persistence failed")

        monkeypatch.setattr(library, "_upsert_slices", fail_insert)
        with pytest.raises(RuntimeError, match="score persistence failed"):
            library.replace_candidate_slices(
                "source", [_slice("new", SliceStatus.CANDIDATE, 0.7)], profile_id=""
            )

        old = library.get_slice("old")
        human = library.get_slice("human")
        assert old is not None and old.score == 0.8
        assert human is not None and human.status is SliceStatus.REVIEWED


class _PullAcquirer:
    def __init__(self, media_path: Path) -> None:
        self.media_path = media_path

    def can_handle(self, _request: str) -> bool:
        return True

    def acquire(self, request: str) -> AcquiredSource:
        return AcquiredSource(
            kind=SourceKind.LOCAL,
            ref=request,
            media_path=self.media_path,
        )


class _FailingTranscriber:
    def transcribe(self, _media_path: Path) -> list[TranscriptWord]:
        raise RuntimeError("decode failed")


def test_pull_removes_new_cache_file_after_transcription_failure(
    tmp_path: Path,
) -> None:
    media = tmp_path / "source.mp4"
    media.write_bytes(b"new cache bytes")
    cache = MediaCache(tmp_path / "cache")
    digest = hash_file(media)
    cached = cache.path_for(digest, media.suffix)

    with Library(tmp_path / "library.sqlite3") as library:
        with pytest.raises(RuntimeError, match="decode failed"):
            pull(
                "local",
                acquirers=[_PullAcquirer(media)],
                transcriber=_FailingTranscriber(),
                cache=cache,
                library=library,
            )

    assert not cached.exists()


def test_pull_removes_new_cache_file_after_persistence_failure(tmp_path: Path) -> None:
    media = tmp_path / "source.mp4"
    media.write_bytes(b"persistence cache bytes")
    cache = MediaCache(tmp_path / "cache")
    digest = hash_file(media)
    cached = cache.path_for(digest, media.suffix)

    class FailingLibrary(Library):
        def upsert_source(self, _source: Source) -> None:
            raise RuntimeError("database unavailable")

    class EmptyTranscriber:
        def transcribe(self, _media_path: Path) -> list[TranscriptWord]:
            return []

    with FailingLibrary(tmp_path / "library.sqlite3") as library:
        with pytest.raises(RuntimeError, match="database unavailable"):
            pull(
                "local",
                acquirers=[_PullAcquirer(media)],
                transcriber=EmptyTranscriber(),
                cache=cache,
                library=library,
            )

    assert not cached.exists()


def test_pull_keeps_preexisting_cache_file_after_failure(tmp_path: Path) -> None:
    media = tmp_path / "source.mp4"
    media.write_bytes(b"shared cache bytes")
    cache = MediaCache(tmp_path / "cache")
    digest, cached = cache.put(media)
    before = cached.read_bytes()

    with Library(tmp_path / "library.sqlite3") as library:
        with pytest.raises(RuntimeError, match="decode failed"):
            pull(
                "local",
                acquirers=[_PullAcquirer(media)],
                transcriber=_FailingTranscriber(),
                cache=cache,
                library=library,
            )

    assert cached == cache.path_for(digest, media.suffix)
    assert cached.read_bytes() == before


def test_whisper_wraps_lazy_segment_iteration_failure() -> None:
    class LazyModel:
        def transcribe(self, _path: str, *, word_timestamps: bool):
            assert word_timestamps is True

            def segments():
                raise ValueError("decoder exhausted")
                yield  # pragma: no cover

            return segments(), None

    transcriber = WhisperTranscriber()
    transcriber._model = LazyModel()
    with pytest.raises(
        RuntimeError, match=r"transcription failed.*clip\.mp4.*decoder exhausted"
    ):
        transcriber.transcribe(Path("clip.mp4"))


def test_whisper_wraps_model_load_failure() -> None:
    """Round 3 adapter review (MEDIUM): model startup errors retain file context."""
    transcriber = WhisperTranscriber()
    transcriber._load = lambda: (_ for _ in ()).throw(RuntimeError("model missing"))
    with pytest.raises(RuntimeError, match=r"transcription failed.*clip\.mp4"):
        transcriber.transcribe(Path("clip.mp4"))


def test_ytdlp_normalizes_upload_date_and_discards_bad_dates(
    tmp_path: Path, monkeypatch
) -> None:
    class FakeYoutubeDL:
        def __init__(self, options):
            self.options = options

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def extract_info(self, _request, *, download):
            assert download is True
            media_path = Path(
                self.options["outtmpl"].replace("%(id)s.%(ext)s", "video.mp4")
            )
            media_path.write_bytes(b"downloaded")
            return {
                "id": "video",
                "upload_date": "20240904",
                "requested_downloads": [{"filepath": str(media_path)}],
            }

    monkeypatch.setitem(sys.modules, "yt_dlp", SimpleNamespace(YoutubeDL=FakeYoutubeDL))
    got = YtDlpAcquirer(download_dir=tmp_path / "downloads").acquire(
        "https://youtu.be/video"
    )
    assert got.published_at == "2024-09-04"
    assert _normalize_upload_date(None) == ""
    assert _normalize_upload_date("20241340") == ""
    assert _normalize_upload_date("not-a-date") == ""


def test_ytdlp_prefers_final_merged_file(tmp_path: Path, monkeypatch) -> None:
    """Round 3 acquisition review (CRITICAL): ingest the merged A/V output."""

    class FakeYoutubeDL:
        def __init__(self, options):
            self.options = options

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def prepare_filename(self, _info):
            return str(tmp_path / "intermediate.webm")

        def extract_info(self, _request, *, download):
            assert download is True
            intermediate = tmp_path / "intermediate.webm"
            intermediate.write_bytes(b"video only")
            merged = tmp_path / "merged.mp4"
            merged.write_bytes(b"video and audio")
            return {
                "id": "video",
                "filepath": str(merged),
                "requested_downloads": [{"filepath": str(intermediate)}],
            }

    monkeypatch.setitem(sys.modules, "yt_dlp", SimpleNamespace(YoutubeDL=FakeYoutubeDL))
    got = YtDlpAcquirer(download_dir=tmp_path).acquire("https://youtu.be/video")
    assert got.media_path == tmp_path / "merged.mp4"
