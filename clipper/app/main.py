"""RiceClipper local FastAPI server + review UI (SPEC.md D10).

Localhost only. Hosts the human-in-the-loop review gate: upload a clip, get a
word-level transcript back, edit text / type a header / configure music, then
render and download. RiceClipper performs NO posting or upload of content
(SPEC.md §3) — every route reads and writes local files only.
"""

from __future__ import annotations

import logging
import shutil
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app import handoff, header_gen, jobs, probe, searcher_pickup
from app.models import HandoffRequest, HeaderRequest, JobState, RenderRequest
from app.process import terminate_all_owned_processes
from render import frame, geometry, subject
from render.pipeline import render
from transcribe import whisper

logger = logging.getLogger("riceclipper")

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


@app.post("/api/jobs/{job_id}/transcribe", response_model=JobState)
def transcribe_job(job_id: str) -> JobState:
    with jobs.job_operation_lock():
        job = jobs.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        if job.source_path is None:
            raise HTTPException(status_code=409, detail="no source uploaded")
        if job.status in {"transcribing", "rendering"}:
            raise HTTPException(status_code=409, detail="job is already active")

        job.status = "transcribing"
        job.error = None
        try:
            job.words = whisper.transcribe(str(job.source_path))
            # Landscape input gets a subject-crop plan at ingest. build_plan
            # never raises; a failure stores an analysis_failed blur-pad plan.
            if job.info and job.info.width > job.info.height:
                job.crop_plan = subject.build_plan(job.source_path, job.info)
            job.status = "ready"
        except Exception:
            logger.exception("transcription failed")
            job.status = "error"
            job.error = "transcription failed"
            raise HTTPException(status_code=500, detail=job.error) from None
        return job.state()


def _safe_thumbnail(job: jobs.Job) -> str:
    """Grab an early frame for the header agent, degrading to text-only on failure.

    A frame is the strongest signal (SPEC §6.2) but must never block header
    generation: a missing/undecodable frame falls back to a transcript-only hook.
    """
    if job.source_path is None:
        return ""
    try:
        return frame.grab_frame_b64(job.source_path, job.info, job.dir)
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
        if req.geometry == "crop" and job.crop_plan is None:
            raise HTTPException(
                status_code=400,
                detail="crop requires a landscape job with a crop plan",
            )

        # Resolve the per-clip geometry to the plan render() receives (ADR-001).
        # "crop" forces a crop even over a blur_pad decision; everything else
        # (pass-through / blur_pad) passes no plan.
        mode = geometry.resolve_geometry(
            req.geometry, job.crop_plan, job.info.width, job.info.height
        )
        if mode == "crop":
            plan = job.crop_plan.model_copy(update={"decision": "crop"})
        else:
            plan = None

        job.status = "rendering"
        job.error = None
        try:
            if job.output_path is not None:
                job.output_path.unlink(missing_ok=True)
                job.output_path = None
            out = render(job.dir, job.source_path, job.info, req, plan=plan)
            job.output_path = out
            job.status = "done"
        except Exception as exc:
            logger.exception("render failed")
            job.status = "error"
            job.error = "render failed"
            raise HTTPException(status_code=500, detail=job.error) from exc
        return job.state()


@app.post("/api/handoff")
def handoff_batch(req: HandoffRequest) -> dict:
    """Write a rendered batch into the RicePoster handoff directory.

    Producer side of the pickup contract (SPEC §7 Wave-1 #1). Reads job outputs
    and writes local files only — no posting, no network. The batch grouping and
    the reviewed transcript come from the client (the batch is client-driven);
    the server contributes the rendered mp4s and the manifest.
    """
    if not req.clips:
        raise HTTPException(status_code=400, detail="no clips provided")

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
        return handoff.write_batch(entries)
    except handoff.HandoffError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/pull-from-searcher")
def pull_from_searcher() -> dict:
    """Ingest the oldest RiceSearcher handoff batch as new review jobs.

    Reads local files from the searcher inbox (``~/ricesearcher-handoff``) and
    copies each clip into a job work dir — no posting, no network. The clips then
    flow through the normal review → render → "Send to RicePoster" path (which
    writes the separate ``~/riceclipper-handoff``).
    """
    try:
        return searcher_pickup.pull_next_batch()
    except searcher_pickup.PickupError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/jobs/{job_id}/output")
def get_output(job_id: str) -> FileResponse:
    job = jobs.get_job(job_id)
    if job is None or job.output_path is None or not job.output_path.exists():
        raise HTTPException(status_code=404, detail="no rendered output")
    # Serve inline (no attachment disposition) so the <video> element can play
    # it; the UI's download anchor sets its own filename for saving.
    return FileResponse(job.output_path, media_type="video/mp4")


# Static review UI mounted last so /api/* routes take precedence.
app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
