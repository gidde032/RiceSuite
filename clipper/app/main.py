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

from app import jobs, probe
from app.models import JobState, RenderRequest
from render.pipeline import RenderError, render
from transcribe import whisper
from transcribe.whisper import transcribe

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
    yield
    # Shutdown: release the transcription model so ctranslate2 resources are torn
    # down deterministically (avoids leaked-semaphore warnings at exit).
    whisper.dispose()


app = FastAPI(title="RiceClipper", version="0.1.0", lifespan=lifespan)


@app.get("/api/health")
def health() -> dict:
    return {
        "ffmpeg": probe.ffmpeg_available(),
        "libass": probe.has_libass(),
    }


@app.post("/api/upload", response_model=JobState)
async def upload(file: UploadFile = File(...)) -> JobState:
    # Keep this request short: save the file and probe geometry, then return.
    # Transcription is a separate call (/transcribe) so the client's File handle
    # is released immediately and the local blob preview doesn't contend with a
    # long-open upload request.
    job = jobs.create_job()
    suffix = Path(file.filename or "clip.mp4").suffix or ".mp4"
    source = job.dir / f"source{suffix}"
    with source.open("wb") as fh:
        shutil.copyfileobj(file.file, fh)
    job.source_path = source

    try:
        job.info = probe.probe(str(source))
    except probe.ProbeError as exc:
        job.status = "error"
        job.error = f"could not read video: {exc}"
        return job.state()

    job.status = "transcribing"
    return job.state()


@app.post("/api/jobs/{job_id}/transcribe", response_model=JobState)
def transcribe_job(job_id: str) -> JobState:
    job = jobs.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    if job.source_path is None:
        raise HTTPException(status_code=409, detail="no source uploaded")

    try:
        job.words = transcribe(str(job.source_path))
        job.status = "ready"
    except Exception as exc:  # noqa: BLE001 - surface any transcription failure
        logger.exception("transcription failed")
        job.status = "error"
        job.error = f"transcription failed: {exc}"
        raise HTTPException(status_code=500, detail=job.error)
    return job.state()


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
async def upload_music(job_id: str, file: UploadFile = File(...)) -> dict:
    job = jobs.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    suffix = Path(file.filename or "music.mp3").suffix or ".mp3"
    dest = job.dir / f"music{suffix}"
    with dest.open("wb") as fh:
        shutil.copyfileobj(file.file, fh)
    return {"ok": True, "filename": dest.name}


@app.post("/api/jobs/{job_id}/render", response_model=JobState)
def render_job(job_id: str, req: RenderRequest) -> JobState:
    job = jobs.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    if job.source_path is None or job.info is None:
        raise HTTPException(status_code=409, detail="job not ready to render")

    job.status = "rendering"
    try:
        out = render(job.dir, job.source_path, job.info, req)
        job.output_path = out
        job.status = "done"
    except RenderError as exc:
        job.status = "error"
        job.error = str(exc)
        raise HTTPException(status_code=500, detail=str(exc))
    return job.state()


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
