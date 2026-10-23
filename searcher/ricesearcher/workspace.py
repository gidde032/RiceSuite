"""Process-lifetime serial work, with durable source-stage checkpoints only.

No work is dispatched on startup or observation. Each admitted batch owns one
worker and one SQLite connection. Adapters are injected for offline verification.
"""

from __future__ import annotations

import copy
import hashlib
import json
import shutil
import threading
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ricesearcher.acquire.base import Acquirer
from ricesearcher.acquire.watchfolder import WatchFolderAcquirer
from ricesearcher.acquire.ytdlp import YtDlpAcquirer
from ricesearcher.beat.profile import BeatProfile
from ricesearcher.config import Config
from ricesearcher.dedup.annotate import SIM_THRESHOLD
from ricesearcher.dedup.base import Embedder
from ricesearcher.dedup.embed import SentenceTransformerEmbedder
from ricesearcher.library.cache import MediaCache
from ricesearcher.library.store import Library
from ricesearcher.models import Source
from ricesearcher.pipeline import (
    _cleanup_owned_download_dir,
    _now_iso,
    annotate_library_duplicates,
    extract_and_score,
)
from ricesearcher.score.anthropic_scorer import AnthropicScorer
from ricesearcher.score.base import Scorer
from ricesearcher.score.heuristic_scorer import HeuristicScorer
from ricesearcher.transcribe.base import DEFAULT_TRANSCRIPTION_MODEL, Transcriber
from ricesearcher.transcribe.whisper import WhisperTranscriber


@dataclass(frozen=True)
class Options:
    score: bool = False
    offline: bool = False
    model: str | None = None
    transcription_model: str = DEFAULT_TRANSCRIPTION_MODEL
    threshold: float = SIM_THRESHOLD
    duplicates_only: bool = False


@dataclass(frozen=True)
class Item:
    request: str = ""
    source_id: str = ""
    filename: str = ""
    upload_digest: str = ""


@dataclass
class Adapters:
    acquirers: Callable[[], Sequence[Acquirer]] = lambda: [
        WatchFolderAcquirer(),
        YtDlpAcquirer(),
    ]
    transcriber: Callable[[str], Transcriber] = lambda model: WhisperTranscriber(
        model_size=model
    )
    scorer: Callable[[Options], Scorer] = lambda opts: (
        HeuristicScorer() if opts.offline else AnthropicScorer(model=opts.model)
    )
    embedder: Callable[[], Embedder] = SentenceTransformerEmbedder


class RunManager:
    """Atomic admission/stop/retry and bounded, detached read snapshots."""

    def __init__(self, config: Config, adapters: Adapters | None = None) -> None:
        self.config = config
        self.default_adapters = adapters is None
        self.adapters = adapters or Adapters()
        self.lock = threading.RLock()
        self.runs: dict[str, dict[str, Any]] = {}
        self.inputs: dict[str, tuple[list[Item], BeatProfile, Options]] = {}
        self.active: str | None = None
        self.latest: str | None = None
        # Intake is transient, not a restart-resumable queue. The single app
        # discards abandoned process-owned staging under normal cache custody.
        with MediaCache(config.cache_dir).custody():
            staging = config.cache_dir / "intake"
            if staging.is_symlink():
                staging.unlink()
            else:
                shutil.rmtree(staging, ignore_errors=True)

    def snapshot(self, key: str | None = None) -> dict | None:
        with self.lock:
            run = self.runs.get(key or self.active or self.latest or "")
            return copy.deepcopy(run) if run else None

    def _set(self, key: str, **values: Any) -> None:
        with self.lock:
            self.runs[key].update(values)

    def _item(self, key: str, index: int, **values: Any) -> None:
        with self.lock:
            self.runs[key]["items"][index].update(values)

    def submit(
        self, key: str, items: list[Item], profile: BeatProfile, options: Options
    ) -> dict:
        identity = [
            {**asdict(i), "request": "" if i.upload_digest else i.request}
            for i in items
        ]
        signature = hashlib.sha256(
            json.dumps(
                [identity, asdict(profile), asdict(options)], sort_keys=True
            ).encode()
        ).hexdigest()
        with self.lock:
            old = self.runs.get(key)
            if old:
                if old["signature"] != signature:
                    raise ValueError("attempt key already used with different input")
                return copy.deepcopy(old)
            if self.active:
                raise ValueError("another processing batch is active")
            # Completed observations are bounded. Expired keys are never replayed by
            # the UI; an unavailable record requires a new explicit user action.
            expired_uploads: list[Item] = []
            while len(self.runs) >= 32:
                oldest = next(iter(self.runs))
                self.runs.pop(oldest)
                expired, _, _ = self.inputs.pop(oldest)
                expired_uploads.extend(expired)
            run = {
                "id": key,
                "signature": signature,
                "profile": profile.id,
                "profile_version": profile.version,
                "options": asdict(options),
                "status": "active",
                "stage": "waiting",
                "stop_requested": False,
                "detail": "",
                "duplicate_error": "",
                "items": [
                    {
                        "request": i.filename or i.request or i.source_id,
                        "source_id": i.source_id,
                        "state": "waiting",
                        "stage": "waiting",
                        "error": "",
                        "slices": 0,
                    }
                    for i in items
                ],
            }
            self.runs[key] = run
            self.inputs[key] = (copy.deepcopy(items), copy.deepcopy(profile), options)
            self._cleanup_uploads(expired_uploads)
            self.active = self.latest = key
            threading.Thread(target=self._work, args=(key,), daemon=True).start()
            return copy.deepcopy(run)

    def stop(self, key: str) -> dict:
        with self.lock:
            if key not in self.runs:
                raise KeyError(key)
            if self.runs[key]["status"] == "active":
                self.runs[key]["stop_requested"] = True
            return copy.deepcopy(self.runs[key])

    def retry(self, previous: str, key: str) -> dict:
        with self.lock:
            old = self.runs.get(previous)
            if old is None:
                raise KeyError(previous)
            if old["status"] == "active":
                raise ValueError("batch is still active")
            original, profile, options = self.inputs[previous]
            failed = [
                Item(source_id=r["source_id"]) if r["source_id"] else original[n]
                for n, r in enumerate(old["items"])
                if r["state"] == "failed"
            ]
            if not failed and old["duplicate_error"]:
                return self.submit(
                    key,
                    [],
                    profile,
                    Options(threshold=options.threshold, duplicates_only=True),
                )
            if not failed:
                raise ValueError("no failed items to retry")
            return self.submit(key, failed, profile, options)

    def _cleanup_uploads(self, candidates: list[Item]) -> None:
        """Discard only staging no retained failed acquisition still needs.

        Call with self.lock held. Retried items may share their original file;
        source checkpoints replace that need only after acquisition succeeds.
        Cleanup cannot change work results or strand the admission lock.
        """
        needed = {
            item.request
            for key, (items, _, _) in self.inputs.items()
            for n, item in enumerate(items)
            if item.upload_digest
            and not self.runs[key]["items"][n]["source_id"]
            and self.runs[key]["items"][n]["state"] != "unstarted"
        }
        for item in candidates:
            if not item.upload_digest or item.request in needed:
                continue
            path = Path(item.request)
            try:
                path.unlink(missing_ok=True)
                path.parent.rmdir()  # only this empty app-owned staging directory
            except OSError:
                pass

    def _acquire(self, item: Item, library: Library, cache: MediaCache) -> Source:
        acquirer = next(
            (a for a in self.adapters.acquirers() if a.can_handle(item.request)), None
        )
        if acquirer is None:
            raise ValueError("unsupported URL or local media path")
        acquired = acquirer.acquire(item.request)
        try:
            with cache.custody():
                digest, path = cache.put(acquired.media_path)
                previous = library.get_source(digest)
                if previous is not None:
                    # Never replace a successful transcript with a pending attempt.
                    return previous
                source = Source(
                    id=digest,
                    kind=acquired.kind,
                    ref=item.filename or acquired.ref,
                    media_path=str(path),
                    title=Path(item.filename).stem if item.filename else acquired.title,
                    channel=acquired.channel,
                    published_at=acquired.published_at,
                    acquired_at=_now_iso(),
                    duration_s=acquired.duration_s,
                    transcription_state="pending",
                )
                library.upsert_source(source)
                return source
        finally:
            _cleanup_owned_download_dir(acquired.owned_temp_dir)

    def _work(self, key: str) -> None:
        items, profile, options = self.inputs[key]
        cache = MediaCache(self.config.cache_dir)
        try:
            with Library(self.config.db_path) as library:
                for n, item in enumerate(items):
                    with self.lock:
                        if self.runs[key]["stop_requested"]:
                            for rest in range(n, len(items)):
                                self._item(key, rest, state="unstarted")
                            break
                    stage = "acquiring"
                    try:
                        self._set(key, stage=stage)
                        self._item(key, n, state="active", stage=stage)
                        source = (
                            library.get_source(item.source_id)
                            if item.source_id
                            else None
                        )
                        if item.source_id and source is None:
                            raise ValueError("stored source is unavailable")
                        if source is None:
                            source = self._acquire(item, library, cache)
                        self._item(key, n, source_id=source.id)
                        if not Path(source.media_path).is_file():
                            raise ValueError(
                                "cached source media is missing; add the source again"
                            )
                        if source.transcription_state != "complete":
                            stage = "transcribing"
                            self._set(key, stage=stage)
                            self._item(key, n, stage=stage)
                            words = self.adapters.transcriber(
                                options.transcription_model
                            ).transcribe(Path(source.media_path))
                            with cache.custody():
                                if (
                                    library.get_source(source.id) is None
                                    or not Path(source.media_path).is_file()
                                ):
                                    raise ValueError(
                                        "source was removed while transcribing"
                                    )
                                source.words = list(words)
                                source.transcription_state = "complete"
                                library.upsert_source(source)
                        if options.score:
                            stage = "scoring"
                            self._set(key, stage=stage)
                            self._item(key, n, stage=stage)
                            slices = extract_and_score(
                                source,
                                profile=profile,
                                scorer=self.adapters.scorer(options),
                                library=library,
                            )
                            self._item(key, n, slices=len(slices))
                        self._item(key, n, state="complete", stage="complete")
                    except Exception as exc:
                        self._item(key, n, state="failed", stage=stage, error=str(exc))
                if options.score or options.duplicates_only:
                    self._set(key, stage="duplicates")
                    try:
                        annotate_library_duplicates(
                            library,
                            self.adapters.embedder(),
                            profile_id=profile.id,
                            sim_threshold=options.threshold,
                        )
                    except Exception as exc:
                        self._set(key, duplicate_error=str(exc))
            self._set(key, status="complete", stage="complete")
        except Exception as exc:
            self._set(key, status="failed", detail=str(exc))
        finally:
            # Browser intake staging belongs to this admitted run. Its cached
            # checkpoint survives; unsuccessful acquisition can explicitly retry
            # the staged file while its observation is retained.
            with self.lock:
                try:
                    self._cleanup_uploads(items)
                finally:
                    self.active = None
