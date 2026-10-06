"""RiceClipper local FastAPI server + review UI (SPEC.md D10).

Localhost only. Hosts the human-in-the-loop review gate: upload a clip, get a
word-level transcript back, edit text / type a header / configure music, then
render and download. RiceClipper performs NO posting or upload of content
(SPEC.md §3) — every route reads and writes local files only.
"""

from __future__ import annotations

import base64
import logging
import shutil
import uuid
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from ricesuite.localguard import LocalGuard, gateway_origins
from ricesuite.progress import Progress, ProgressStore, notify

from app import env

# Before the imports below: transcribe.whisper reads its settings at import.
env.load_dotenv_file()

from app import (  # noqa: E402
    handoff,
    header_gen,
    jobs,
    photo,
    probe,
    searcher_pickup,
    send_keys,
)
from app.models import (  # noqa: E402
    HEADER_MAX_CHARS,
    HandoffRequest,
    HeaderPreviewRequest,
    HeaderRequest,
    JobState,
    LyricsRequest,
    LyricsResult,
    RenderRequest,
)
from app.process import terminate_all_owned_processes  # noqa: E402
from render import frame, framing, geometry, subject, text_image  # noqa: E402
from render.ass import HEADER_STYLE_NAMES, header_preset  # noqa: E402
from render.header_image import header_png_bytes  # noqa: E402
from render.pipeline import render, style_for_request  # noqa: E402
from transcribe import lyrics, whisper  # noqa: E402

logger = logging.getLogger("riceclipper")

PROGRESS = ProgressStore()


def _start_progress(operation_id: str | None, operation: str) -> Progress | None:
    if not operation_id:
        return None
    try:
        return PROGRESS.start(operation_id, operation)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception:
        return None


def _finish_progress(progress, status="complete", detail="", batch_id=""):
    if progress is not None:
        try:
            progress.finish(status=status, detail=detail, batch_id=batch_id)
        except Exception:
            pass


WEB_DIR = Path(__file__).resolve().parent.parent / "web"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Startup: surface toolchain problems loudly instead of failing mid-render.
    if not probe.ffmpeg_available():
        logger.warning("ffmpeg/ffprobe not found on PATH — rendering will fail.")
    elif not probe.has_libass():
        logger.warning(
            "This ffmpeg has no libass (no 'subtitles' filter). Caption/header "
            "burn-in WILL FAIL. Install a libass build (Homebrew's core ffmpeg "
            "omits libass):\n"
            "  brew unlink ffmpeg && "
            "brew install homebrew-ffmpeg/ffmpeg/ffmpeg"
        )
    try:
        yield
    finally:
        # Stop owned media children before releasing the transcription model.
        # This covers a graceful server shutdown while a synchronous render is
        # still in flight; request-local timeouts use the same process-group
        # mechanism.
        terminate_all_owned_processes()
        # Shutdown: release the transcription model so ctranslate2 resources are
        # torn down deterministically (avoids leaked-semaphore warnings at exit).
        whisper.dispose()


app = FastAPI(title="RiceClipper", version="0.1.0", lifespan=lifespan)


@app.middleware("http")
async def no_store_review_assets(request: Request, call_next):
    """A long-lived local tab must reload current HTML, CSS, and JavaScript."""
    response = await call_next(request)
    if request.url.path in {"/", "/index.html", "/app.js", "/style.css", "/slate.css"}:
        response.headers["Cache-Control"] = "no-store"
    return response


# A browser page can reach this port without passing the RiceSuite gateway, so
# the app refuses foreign Hosts and cross-origin state changes itself (suite
# SPEC FR-3, suite #14), on whatever port it is bound to. It also refuses to be
# framed by another site, and serves job media (uploads keep their own
# extension) so it can run no script (suite #38). Added last, so it is the
# outermost middleware.
app.add_middleware(
    LocalGuard, trusted_origins=gateway_origins(), sandboxed_paths=("/api/jobs/",)
)


@app.get("/api/health")
def health() -> dict:
    return {
        "ffmpeg": probe.ffmpeg_available(),
        "libass": probe.has_libass(),
    }


@app.get("/api/media-info")
def media_info() -> dict[str, int]:
    """Return the size of the server-side working-media cache."""
    return jobs.cache_info()


@app.post("/api/media/clear")
def clear_media() -> dict[str, int]:
    """Clear app-owned working media, but never while a job is active."""
    try:
        return jobs.clear_cache()
    except jobs.ActiveJobsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/upload", response_model=JobState)
def upload(file: UploadFile = File(...)) -> JobState:
    # Keep this request short: save the file and probe geometry, then return.
    # Transcription is a separate call (/transcribe) so the client's File handle
    # is released immediately and the local blob preview doesn't contend with a
    # long-open upload request.
    kind = photo.classify(file.filename, file.content_type)
    if kind == "unsupported":
        raise HTTPException(status_code=400, detail=photo.UnsupportedPhotoError.detail)
    if kind == "photo":
        return _upload_photo(file)
    job = jobs.create_job()
    suffix = Path(file.filename or "clip.mp4").suffix or ".mp4"
    source = job.dir / f"source{suffix}"
    try:
        with jobs.job_operation_lock():
            with source.open("wb") as fh:
                shutil.copyfileobj(file.file, fh)
            job.source_path = source
            job.info = probe.probe(str(source))
            # Transcription is a separate request; do not call an idle upload
            # "active" forever if the client disconnects between requests.
            job.status = "ready"
    except probe.ProbeError as exc:
        logger.warning("uploaded file could not be probed: %s", exc)
        job.status = "error"
        job.error = "could not read video"
        raise HTTPException(status_code=400, detail=job.error) from exc
    except Exception as exc:
        logger.exception("upload failed")
        job.status = "error"
        job.error = "upload failed"
        raise HTTPException(status_code=500, detail=job.error) from exc
    return job.state()


_PHOTO_NO_TRANSCRIPT = "a photo has no transcript"


def _upload_photo(file: UploadFile) -> JobState:
    """Store a still photo as an upright PNG source (Issue #54)."""
    job = jobs.create_job()
    job.kind = "photo"
    suffix = Path(file.filename or "").suffix.lower()
    raw = job.dir / f"upload{suffix}"
    source = job.dir / photo.PHOTO_SOURCE_NAME
    try:
        with jobs.job_operation_lock():
            with raw.open("wb") as fh:
                shutil.copyfileobj(file.file, fh)
            try:
                job.info = photo.normalize(raw, source)
            finally:
                raw.unlink(missing_ok=True)
            job.source_path = source
            job.status = "ready"
    except photo.PhotoError as exc:
        logger.warning("uploaded photo could not be read: %s", exc)
        job.status = "error"
        job.error = exc.detail
        raise HTTPException(status_code=400, detail=job.error) from exc
    except Exception as exc:
        logger.exception("photo upload failed")
        job.status = "error"
        job.error = "upload failed"
        raise HTTPException(status_code=500, detail=job.error) from exc
    return job.state()


@app.post("/api/jobs/{job_id}/transcribe", response_model=JobState)
def transcribe_job(job_id: str) -> JobState:
    with jobs.job_operation_lock():
        job = jobs.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        if job.source_path is None:
            raise HTTPException(status_code=409, detail="no source uploaded")
        if job.kind == "photo":
            raise HTTPException(status_code=409, detail=_PHOTO_NO_TRANSCRIPT)
        if job.status in {"transcribing", "rendering"}:
            raise HTTPException(status_code=409, detail="job is already active")

        job.status = "transcribing"
        job.error = None
        try:
            job.words = whisper.transcribe(str(job.source_path))
            job.reference_words = list(job.words)
            # The transcript changed, so any prior render has stale burned-in
            # captions; invalidate it so it can't reach the handoff (A4, mirrors
            # lyrics_job / restore_transcript).
            if job.output_path is not None:
                if job.output_path.exists():
                    job.output_path.unlink(missing_ok=True)
                job.output_path = None
            if job.info and job.info.width > job.info.height:
                job.crop_plan, job.music_plan = subject.build_plan(
                    job.source_path, job.info
                )
            if job.searcher_manifest is not None:
                jobs.persist_searcher_job(job)
            job.status = "ready"
        except Exception:
            logger.exception("transcription failed")
            job.status = "error"
            job.error = "transcription failed"
            raise HTTPException(status_code=500, detail=job.error) from None
        return job.state()


@app.post("/api/jobs/{job_id}/lyrics", response_model=LyricsResult)
def lyrics_job(job_id: str, req: LyricsRequest) -> LyricsResult:
    with jobs.job_operation_lock():
        job = jobs.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        if job.kind == "photo":
            raise HTTPException(status_code=409, detail=_PHOTO_NO_TRANSCRIPT)
        if job.status in {"transcribing", "rendering"}:
            raise HTTPException(status_code=409, detail="job is already active")
        if job.info is None:
            # No probe info (errored/never-probed job): there is no real clip
            # duration to align against, so refuse rather than emit words past a
            # zero-length clip (A5). Blank input still reports 422 for a
            # consistent contract.
            if not req.lyrics.strip():
                raise HTTPException(status_code=422, detail="empty lyric block")
            raise HTTPException(status_code=409, detail="job is not ready")
        try:
            result = lyrics.align(
                req.lyrics,
                job.reference_words,
                job.info.duration,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        job.words = result.words
        job.status = "ready"
        if job.output_path and job.output_path.exists():
            job.output_path.unlink(missing_ok=True)
        job.output_path = None
        if job.searcher_manifest is not None:
            jobs.persist_searcher_job(job)
        return result


@app.post("/api/jobs/{job_id}/restore-transcript", response_model=JobState)
def restore_transcript(job_id: str) -> JobState:
    with jobs.job_operation_lock():
        job = jobs.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        if job.status in {"transcribing", "rendering"}:
            raise HTTPException(status_code=409, detail="job is already active")
        if not job.reference_words:
            raise HTTPException(
                status_code=409, detail="no reference transcript available"
            )
        job.words = list(job.reference_words)
        job.status = "ready"
        if job.output_path and job.output_path.exists():
            job.output_path.unlink(missing_ok=True)
        job.output_path = None
        if job.searcher_manifest is not None:
            jobs.persist_searcher_job(job)
        return job.state()


def _safe_thumbnail(job: jobs.Job) -> str:
    """Grab an early frame for the header agent, degrading to text-only on failure.

    A frame is the strongest signal (SPEC §6.2) but must never block header
    generation: a missing/undecodable frame falls back to a transcript-only hook.
    """
    if job.source_path is None:
        return ""
    try:
        # A photo has one frame, so it is grabbed at 0 s, not ~1 s in.
        info = None if job.kind == "photo" else job.info
        return frame.grab_frame_b64(job.source_path, info, job.dir)
    except frame.FrameGrabError:
        logger.warning("header frame grab failed; using transcript only")
        return ""


@app.post("/api/jobs/{job_id}/header")
def generate_header(job_id: str, req: HeaderRequest) -> dict:
    """Generate an on-screen header from an early frame + transcript (SPEC §6.2).

    The design's only outbound call — it generates text and posts nothing. On any
    failure the review UI keeps the manual header, so this never blocks a render.
    """
    with jobs.job_operation_lock():
        job = jobs.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        if job.source_path is None or job.info is None:
            raise HTTPException(status_code=409, detail="job not ready")

        transcript = (
            req.transcript.strip() or " ".join(w.text for w in job.words).strip()
        )
        thumbnail = _safe_thumbnail(job)
        try:
            header = header_gen.generate_header(
                transcript,
                thumbnail_b64=thumbnail,
                note=req.note,
                feedback=req.feedback,
                avoid=req.avoid,
                style=req.style,
            )
        except header_gen.HeaderConfigError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except header_gen.HeaderGenerationError as exc:
            logger.warning("header generation failed: %s", exc)
            raise HTTPException(
                status_code=502, detail="header generation failed"
            ) from exc
        return {"header": header}


@app.get("/api/jobs/{job_id}", response_model=JobState)
def get_job(job_id: str) -> JobState:
    job = jobs.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return job.state()


@app.get("/api/jobs/{job_id}/source")
def get_source(job_id: str) -> FileResponse:
    job = jobs.get_job(job_id)
    if job is None or job.source_path is None or not job.source_path.exists():
        raise HTTPException(status_code=404, detail="source not found")
    return FileResponse(job.source_path)


@app.post("/api/jobs/{job_id}/music")
def upload_music(job_id: str, file: UploadFile = File(...)) -> dict:
    with jobs.job_operation_lock():
        job = jobs.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        suffix = Path(file.filename or "music.mp3").suffix or ".mp3"
        dest = job.dir / f"music{suffix}"
        try:
            with dest.open("wb") as fh:
                shutil.copyfileobj(file.file, fh)
        except Exception as exc:
            logger.exception("music upload failed")
            raise HTTPException(status_code=500, detail="music upload failed") from exc
        return {"ok": True, "filename": dest.name}


@app.post("/api/jobs/{job_id}/render", response_model=JobState)
def render_job(job_id: str, req: RenderRequest) -> JobState:
    with jobs.job_operation_lock():
        job = jobs.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        if job.source_path is None or job.info is None:
            raise HTTPException(status_code=409, detail="job not ready to render")
        if job.status not in {"ready", "done", "error"}:
            raise HTTPException(status_code=409, detail="job is not ready to render")
        if job.kind == "photo":
            # A photo has no captions and no crop plan: header and music only,
            # blur-padded (or passed through at exactly 1080x1920).
            req = req.model_copy(
                update={
                    "words": [],
                    "captions_on": False,
                    "geometry": "blur_pad",
                    "content": "speech",
                }
            )
        plan_source = job.music_plan if req.content == "music" else job.crop_plan
        if req.geometry == "crop" and plan_source is None:
            raise HTTPException(
                status_code=400,
                detail="crop requires a landscape job with a crop plan",
            )

        mode = geometry.resolve_geometry(
            req.geometry, plan_source, job.info.width, job.info.height
        )
        if mode == "crop":
            plan = plan_source.model_copy(update={"decision": "crop"})
        else:
            plan = None

        # Claim the render under the global lock, then release it so the ffmpeg
        # run does not pin this request behind other jobs (Issue #30). A second
        # render of a job already ``rendering`` is rejected by the status check
        # above with 409.
        # Clear the prior completion record before ffmpeg writes output.mp4.
        # This also removes a partial file left by an interrupted render.
        (job.dir / jobs.RENDERED_OUTPUT_FILENAME).unlink(missing_ok=True)
        job.output_path = None
        if job.searcher_manifest is not None:
            jobs.persist_searcher_job(job)
        job.status = "rendering"
        job.error = None
        job.header_note = None
        # From here, job state reports this render's outcome (RiceSuite #49).
        job.render_id = req.render_id
        render_lock = job.render_lock
        source_path = job.source_path
        info = job.info
        if job.kind == "photo":
            info = replace(info, duration=float(req.photo_duration))
        work_dir = job.dir

    with render_lock:
        notes: list[str] = []
        try:
            out = render(work_dir, source_path, info, req, plan=plan, notes=notes)
        except Exception as exc:
            logger.exception("render failed")
            with jobs.job_operation_lock():
                job.status = "error"
                job.error = "render failed"
            raise HTTPException(status_code=500, detail="render failed") from exc

        with jobs.job_operation_lock():
            job.output_path = out
            # A header that fell back to libass says why, e.g. a missing font
            # the user can install (Issue #3, RiceSuite #65).
            job.header_note = notes[0] if notes else None
            job.status = "done"
            if job.searcher_manifest is not None:
                jobs.persist_searcher_job(job)
            return job.state()


@app.get("/api/header-options")
def header_options() -> dict:
    """The header presets and curated fonts the editor's controls offer."""
    return {
        "presets": {name: header_preset(name) for name in HEADER_STYLE_NAMES},
        "fonts": [
            {
                "key": key,
                "label": choice.label,
                "available": text_image.font_available(key),
            }
            for key, choice in text_image.FONT_CHOICES.items()
        ],
        "max_chars": HEADER_MAX_CHARS,
    }


@app.post("/api/jobs/{job_id}/header-preview")
def header_preview(job_id: str, req: HeaderPreviewRequest) -> dict:
    """Draw the header the render would burn in, for the editor preview.

    Returns the same full-frame PNG the render overlays (as a data URL), its
    drawn box in output px, and the "face near header" warning re-checked for
    that box (RiceSuite #65). Nothing is written to disk.
    """
    job = jobs.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    style = style_for_request(req)
    image = box = note = None
    span: tuple[float, float] | None = None
    try:
        png, drawn = header_png_bytes(req.header, style)
    except Exception as exc:
        logger.warning("header preview failed: %s", exc)
        note = f"The header will use the basic text renderer: {exc}"
        if req.header.strip():
            top = style.header_margin_v
            span = (top, top + framing.HEADER_BLOCK_MAX_PX)
    else:
        if drawn is not None:
            image = "data:image/png;base64," + base64.b64encode(png).decode("ascii")
            box = [drawn.left, drawn.top, drawn.right, drawn.bottom]
            span = (drawn.top, drawn.bottom)
    return {
        "image": image,
        "box": box,
        "note": note,
        "warnings": {
            "crop_plan": framing.header_warning(job.crop_plan, span),
            "music_plan": framing.header_warning(job.music_plan, span),
        },
    }


@app.post("/api/handoff")
def handoff_batch(req: HandoffRequest) -> dict:
    progress = _start_progress(req.observation_id, "send")
    try:
        result = _handoff_batch(req, progress)
    except Exception as exc:
        if isinstance(exc.__cause__, send_keys.SendInProgress):
            # Refusing this retry says nothing about the original send's
            # eventual publication. Keep transport unchanged and report that
            # uncertainty explicitly to both response and progress readers.
            detail = str(getattr(exc, "detail", exc))
            _finish_progress(progress, "unconfirmed", detail)
            return JSONResponse(
                status_code=409,
                content={"detail": detail, "send_in_progress": True},
            )
        _finish_progress(progress, "failed", str(getattr(exc, "detail", exc)))
        raise
    if isinstance(result, JSONResponse):
        _finish_progress(
            progress, "failed", "These clips were already sent under another key."
        )
    else:
        _finish_progress(progress, batch_id=result["batch_id"])
    return result


def _handoff_batch(req: HandoffRequest, progress: Progress | None = None) -> dict:
    """Write a rendered batch into the RicePoster handoff directory.

    Producer side of the pickup contract (SPEC §7 Wave-1 #1). Reads job outputs
    and writes local files only — no posting, no network. The batch grouping and
    the reviewed transcript come from the client (the batch is client-driven);
    the server contributes the rendered mp4s and the manifest.

    A retry that carries the ``send_key`` of a send that already wrote its
    batch gets that batch back with ``replayed: true`` and writes nothing, so a
    lost reply never hands RicePoster the same clips twice (W1-01). Clips that
    already went under another key are refused with 409 and ``already_sent``
    unless ``resend`` confirms a deliberate second send (review S-1).
    """
    if not req.clips:
        raise HTTPException(status_code=400, detail="no clips provided")

    key = req.send_key or uuid.uuid4().hex
    job_ids = [c.job_id for c in req.clips]
    try:
        done = send_keys.begin(key, job_ids, resend=req.resend)
    except send_keys.SendInProgress as exc:
        raise HTTPException(
            status_code=409,
            detail="A send of these clips has not finished. Try again in a moment.",
        ) from exc
    except send_keys.KeyConflict as exc:
        raise HTTPException(
            status_code=409,
            detail="This send key already sent other clips. Use a new key.",
        ) from exc
    except send_keys.AlreadySent as exc:
        sent = exc.sent["batch_id"]
        return JSONResponse(
            status_code=409,
            content={
                "detail": f"These clips already went to RicePoster as batch {sent}. "
                "Send them again only if you mean to; that needs a confirmation.",
                "already_sent": sent,
            },
        )
    if done is not None:
        notify(
            progress,
            "items",
            items=[
                {
                    "id": c.job_id,
                    "title": c.header or f"Clip {c.position}",
                    "position": c.position,
                }
                for c in req.clips
            ],
        )
        notify(progress, "published", batch_id=done["batch_id"])
        searcher_pickup.close_open_batches(job_ids)
        return {
            "batch_id": done["batch_id"],
            "clip_count": done["clip_count"],
            "replayed": True,
        }
    try:
        result = (
            _write_handoff(req, progress)
            if progress is not None
            else _write_handoff(req)
        )
        send_keys.record(key, result, job_ids)
    finally:
        send_keys.end(key)
    searcher_pickup.close_open_batches(job_ids)
    return result


def _write_handoff(req: HandoffRequest, progress: Progress | None = None) -> dict:
    entries: list[handoff.HandoffEntry] = []
    with jobs.job_operation_lock():
        for clip in req.clips:
            job = jobs.get_job(clip.job_id)
            if job is None:
                raise HTTPException(
                    status_code=404, detail=f"job {clip.job_id} not found"
                )
            if job.output_path is None or not job.output_path.exists():
                raise HTTPException(
                    status_code=409, detail=f"job {clip.job_id} has no rendered output"
                )
            entries.append(
                handoff.HandoffEntry(
                    position=clip.position,
                    item_id=clip.job_id,
                    source=job.output_path,
                    transcript=clip.transcript,
                    header=clip.header,
                    caption_style=clip.caption_style,
                    header_style=clip.header_style,
                )
            )

    # Copy outside the job lock: gathering the source paths is quick, but the
    # file copies are not, and they must not block status/upload requests.
    try:
        return (
            handoff.write_batch(entries, progress=progress)
            if progress is not None
            else handoff.write_batch(entries)
        )
    except handoff.HandoffError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/workspace")
def open_workspace_batch(batch_id: str | None = None) -> dict:
    """A pulled Searcher batch not yet sent or discarded, shaped like a pull:
    ``batch_id``'s, or else the oldest. A reloaded tab restores its own batch
    this way (W1-02). Consumes nothing."""
    return searcher_pickup.open_batch(batch_id)


@app.get("/api/workspace-batches")
def list_workspace_batches() -> dict:
    """Read-only summaries of all batches awaiting Clipper review or render."""
    return {"batches": searcher_pickup.open_batches()}


@app.delete("/api/workspace")
def discard_workspace_batch(batch_id: str) -> dict:
    """The reviewer discarded this pulled batch (Start over): stop offering it."""
    return {"discarded": searcher_pickup.discard_open_batch(batch_id)}


@app.get("/api/searcher-inbox")
def searcher_inbox() -> dict:
    """Complete RiceSearcher batches waiting to be pulled (read-only)."""
    return {"batches": searcher_pickup.waiting_batches()}


@app.post("/api/pull-from-searcher")
def pull_from_searcher(
    pull_key: str | None = Query(default=None, pattern=r"^[A-Za-z0-9_-]{8,64}$"),
    observation_id: str | None = Query(default=None, pattern=r"^[A-Za-z0-9_-]{8,64}$"),
) -> dict:
    """Ingest the oldest RiceSearcher handoff batch as new review jobs.

    Reads local files from the searcher inbox (``~/ricesearcher-handoff``) and
    copies each clip into a job work dir — no posting, no network. The clips then
    flow through the normal review → render → "Send to RicePoster" path (which
    writes the separate ``~/riceclipper-handoff``).
    """
    progress = _start_progress(observation_id, "pull")
    try:
        result = (
            searcher_pickup.pull_next_batch(pull_key, progress=progress)
            if progress is not None
            else searcher_pickup.pull_next_batch(pull_key)
        )
    except Exception as exc:
        _finish_progress(progress, "failed", str(exc))
        if isinstance(exc, searcher_pickup.PickupError):
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        raise
    if result.get("replayed"):
        notify(
            progress,
            "items",
            items=[
                {"id": j["id"], "title": j.get("title", ""), "position": i + 1}
                for i, j in enumerate(result["jobs"])
            ],
        )
    _finish_progress(progress, batch_id=result.get("batch_id") or "")
    return result


@app.get("/api/progress/{operation}/{observation_id}")
def operation_progress(operation: str, observation_id: str) -> dict:
    result = PROGRESS.get(observation_id)
    if result is None or result["operation"] != operation:
        raise HTTPException(
            status_code=404, detail="Operation progress unavailable; outcome unknown."
        )
    return result


@app.get("/api/jobs/{job_id}/output")
def get_output(job_id: str) -> FileResponse:
    job = jobs.get_job(job_id)
    if job is None or job.output_path is None or not job.output_path.exists():
        raise HTTPException(status_code=404, detail="no rendered output")
    # Serve inline (no attachment disposition) so the <video> element can play
    # it; the UI's download anchor sets its own filename for saving.
    return FileResponse(
        job.output_path,
        media_type="video/mp4",
        headers={"Cache-Control": "no-store"},
    )


# Static review UI mounted last so /api/* routes take precedence.
@app.get("/slate.css", include_in_schema=False)
def slate_css() -> FileResponse:
    return FileResponse(
        Path(__file__).resolve().parents[2] / "ricesuite/shell/slate.css",
        media_type="text/css",
    )


app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
