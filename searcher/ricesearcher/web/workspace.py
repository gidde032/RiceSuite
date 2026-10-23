"""Local workspace routes; every mutation is deliberate, observation is read-only."""

from __future__ import annotations

import hashlib
import math
import os
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from ricesearcher.acquire.watchfolder import _MEDIA_SUFFIXES, ffprobe_duration
from ricesearcher.beat.profile import load_profile
from ricesearcher.config import Config
from ricesearcher.dedup.annotate import SIM_THRESHOLD
from ricesearcher.library.cache import MediaCache
from ricesearcher.library.store import Library
from ricesearcher.models import CandidateSlice, SliceStatus
from ricesearcher.pipeline import _now_iso
from ricesearcher.score.anthropic_scorer import (
    AnthropicScorer,
    MissingCredentialsError,
    require_credentials,
)
from ricesearcher.transcribe.base import DEFAULT_TRANSCRIPTION_MODEL
from ricesearcher.workspace import Item, Options, RunManager

Attempt = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")]


class Settings(BaseModel):
    profile: str
    score: bool = False
    offline: bool = False
    model: str | None = Field(default=None, max_length=200)
    transcription_model: str = Field(
        default=DEFAULT_TRANSCRIPTION_MODEL, min_length=1, max_length=80
    )
    threshold: float = Field(default=SIM_THRESHOLD, ge=0, le=1, allow_inf_nan=False)
    duplicates_only: bool = False


class Intake(Settings):
    attempt_key: Attempt
    requests: list[str] = Field(default_factory=list, max_length=100)
    source_ids: list[str] = Field(default_factory=list, max_length=100)


class Retry(BaseModel):
    attempt_key: Attempt


class Manual(BaseModel):
    profile: str
    target_in: float
    target_out: float


def register_workspace(app: FastAPI, cfg: Config, manager: RunManager) -> None:
    def admit(key: str, items: list[Item], settings: Settings) -> dict:
        if not items and not settings.duplicates_only:
            raise HTTPException(422, "choose sources or enter URLs/local paths")
        if settings.model and settings.offline:
            raise HTTPException(422, "choose offline scoring or a model override")
        try:
            profile = load_profile(settings.profile, profiles_dir=cfg.profiles_dir)
            opts = Options(**settings.model_dump(exclude={"profile"}))
            if opts.score and not opts.offline and manager.default_adapters:
                require_credentials()
                opts = Options(
                    **{
                        **settings.model_dump(exclude={"profile"}),
                        "model": AnthropicScorer(model=opts.model).model_name,
                    }
                )
            return manager.submit(key, items, profile, opts)
        except (ValueError, OSError, MissingCredentialsError) as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/api/settings")
    def settings() -> dict:
        return {
            "scoring_available": bool(
                os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN")
            ),
            "transcription_model": DEFAULT_TRANSCRIPTION_MODEL,
            "threshold": SIM_THRESHOLD,
        }

    @app.post("/api/runs")
    def start(body: Intake) -> dict:
        settings = Settings(
            **body.model_dump(exclude={"attempt_key", "requests", "source_ids"})
        )
        return admit(
            body.attempt_key,
            [
                *[Item(request=r.strip()) for r in body.requests if r.strip()],
                *[Item(source_id=s) for s in body.source_ids],
            ],
            settings,
        )

    @app.post("/api/runs/files")
    def files(
        attempt_key: Annotated[str, Form(pattern=r"^[A-Za-z0-9_-]{1,64}$")],
        settings_json: Annotated[str, Form()],
        files: Annotated[list[UploadFile], File()],
    ) -> dict:
        try:
            parsed = Settings.model_validate_json(settings_json)
        except ValueError as exc:
            raise HTTPException(422, "invalid intake settings") from exc
        if not 1 <= len(files) <= 100:
            raise HTTPException(422, "choose 1–100 files")
        staging_root = cfg.data_dir / "intake"
        staging_root.mkdir(exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix="intake-", dir=staging_root))
        items = []
        try:
            for uploaded in files:
                filename = uploaded.filename or "untitled.mp4"
                suffix = Path(filename).suffix.lower()
                if suffix not in _MEDIA_SUFFIXES:
                    raise HTTPException(422, "unsupported media filename")
                path = staging / (uuid.uuid4().hex + suffix)
                digest = hashlib.sha256()
                with path.open("wb") as out:
                    while chunk := uploaded.file.read(1024 * 1024):
                        digest.update(chunk)
                        out.write(chunk)
                items.append(
                    Item(
                        request=str(path),
                        filename=filename,
                        upload_digest=digest.hexdigest(),
                    )
                )
            result = admit(attempt_key, items, parsed)
            # Repeated multipart admission observes the first staged input.
            if manager.inputs[attempt_key][0] != items:
                shutil.rmtree(staging)
            return result
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        finally:
            for uploaded in files:
                uploaded.file.close()

    @app.get("/api/runs/current")
    def current() -> dict:
        return {"run": manager.snapshot()}

    @app.get("/api/runs/{key}")
    def observe(key: str) -> dict:
        result = manager.snapshot(key)
        if result is None:
            raise HTTPException(
                404, "run unavailable after expiry/restart; work is never replayed"
            )
        return result

    @app.post("/api/runs/{key}/stop")
    def stop(key: str) -> dict:
        try:
            return manager.stop(key)
        except KeyError as exc:
            raise HTTPException(404, "run unavailable") from exc

    @app.post("/api/runs/{key}/retry")
    def retry(key: str, body: Retry) -> dict:
        try:
            return manager.retry(key, body.attempt_key)
        except KeyError as exc:
            raise HTTPException(404, "run unavailable") from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/api/sources/{source_id}")
    def detail(source_id: str) -> dict:
        with Library(cfg.db_path) as lib:
            source = lib.get_source(source_id)
        if source is None:
            raise HTTPException(404, "no such source")
        return {
            "id": source.id,
            "transcript": source.transcript_text,
            "transcription_state": source.transcription_state,
            "words": [
                {"text": w.text, "start": w.start, "end": w.end} for w in source.words
            ],
        }

    @app.post("/api/sources/{source_id}/manual")
    def manual(source_id: str, body: Manual) -> dict:
        start, end = body.target_in, body.target_out
        if not (math.isfinite(start) and math.isfinite(end) and 0 <= start < end):
            raise HTTPException(422, "use finite timestamps with 0 ≤ start < end")
        try:
            profile = load_profile(body.profile, profiles_dir=cfg.profiles_dir)
        except (ValueError, OSError) as exc:
            raise HTTPException(422, str(exc)) from exc
        with Library(cfg.db_path) as lib:
            source = lib.get_source(source_id)
            if source is None:
                raise HTTPException(404, "no such source")
            try:
                duration = ffprobe_duration(Path(source.media_path))
                if not math.isfinite(duration) or not 0 < end <= duration:
                    raise ValueError("interval exceeds source duration")
            except Exception as exc:
                raise HTTPException(
                    422, "source duration unavailable or interval out of bounds"
                ) from exc
            segment = CandidateSlice(
                id=f"manual:{uuid.uuid4().hex}",
                source_id=source_id,
                pad_in=start,
                pad_out=end,
                target_in=start,
                target_out=end,
                transcript_span=" ".join(
                    w.text for w in source.words if w.end > start and w.start < end
                ),
                score=None,
                heuristic_score=None,
                profile_id=profile.id,
                beat_profile_version=profile.version,
                origin="manual",
                window_edited=True,
                rights_risk="low" if source.kind.value == "local" else "med",
                status=SliceStatus.REVIEWED,
                created_at=_now_iso(),
            )
            # Probe outside SQLite's write lock, then revalidate custody before
            # the short transaction. Existing source rows cannot become dangling.
            with MediaCache(cfg.cache_dir).custody():
                with lib.immediate_transaction():
                    latest = lib.get_source(source_id)
                    if (
                        latest is None
                        or latest.media_path != source.media_path
                        or not Path(source.media_path).is_file()
                    ):
                        raise HTTPException(
                            409, "source changed while saving; inspect it again"
                        )
                    lib._upsert_slices([segment])
        return {
            "id": segment.id,
            "source_id": source_id,
            "profile_id": profile.id,
            "status": "reviewed",
            "origin": "manual",
            "score": None,
        }
