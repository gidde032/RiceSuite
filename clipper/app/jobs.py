"""In-memory job store with per-job work directories.

Single-clip, single-process (SPEC.md §9: no batch in v1). Each job owns a work
dir under ``.riceclipper_work/<id>/`` holding the uploaded source, any music
track, the generated ASS, and the rendered output. The dir is gitignored.
"""

from __future__ import annotations

import json
import os
import re
import stat
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from app.models import CropPlan, JobState, Word
from app.probe import MediaInfo

WORK_ROOT = Path(__file__).resolve().parent.parent / ".riceclipper_work"
JOB_METADATA_FILENAME = "job.json"
_JOB_METADATA_SCHEMA = 1
_SAFE_JOB_ID = re.compile(r"^[A-Za-z0-9_.-]+$")


@dataclass
class Job:
    id: str
    dir: Path
    source_path: Path | None = None
    info: MediaInfo | None = None
    words: list[Word] = field(default_factory=list)
    reference_words: list[Word] = field(default_factory=list)
    status: str = "transcribing"
    error: str | None = None
    output_path: Path | None = None
    # Subject-crop framing decision (ADR-001), set at ingest for landscape input.
    crop_plan: CropPlan | None = None
    music_plan: CropPlan | None = None
    # Searcher imports retain the source manifest on the job so review remains
    # independent of the inbox after the source batch is removed.
    searcher_title: str = ""
    searcher_metadata: dict[str, object] | None = None
    searcher_manifest: dict[str, object] | None = None

    def state(self) -> JobState:
        return JobState(
            id=self.id,
            status=self.status,  # type: ignore[arg-type]
            width=self.info.width if self.info else None,
            height=self.info.height if self.info else None,
            duration=self.info.duration if self.info else None,
            has_audio=self.info.has_audio if self.info else False,
            words=self.words,
            error=self.error,
            has_output=bool(self.output_path and self.output_path.exists()),
            crop_plan=self.crop_plan,
            music_plan=self.music_plan,
        )


_JOBS: dict[str, Job] = {}
_JOBS_LOCK = threading.RLock()

_ACTIVE_STATUSES = frozenset({"transcribing", "rendering"})
_DIRECTORY_OPEN_FLAGS = (
    os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
)


class ActiveJobsError(RuntimeError):
    """Raised when a cache operation would remove an active job."""


class CachePathError(RuntimeError):
    """Raised when the configured work root is not a real directory."""


@contextmanager
def job_operation_lock():
    """Serialize cache deletion with operations that mutate a job directory."""
    with _JOBS_LOCK:
        yield


def _ensure_work_root() -> Path:
    """Return ``WORK_ROOT`` after validating it is a real directory.

    Cache cleanup is intentionally anchored to this directory.  Refusing a
    symlinked root makes that anchor explicit instead of allowing a changed
    configuration to redirect cleanup somewhere else.
    """

    root = Path(WORK_ROOT)
    try:
        root_stat = root.lstat()
    except FileNotFoundError:
        root.mkdir(parents=True, exist_ok=True)
        root_stat = root.lstat()

    if stat.S_ISLNK(root_stat.st_mode) or not stat.S_ISDIR(root_stat.st_mode):
        raise CachePathError(f"work root must be a directory: {root}")
    return root


def persist_searcher_job(job: Job) -> None:
    """Atomically persist imported custody metadata for restart recovery (HIGH)."""
    if job.source_path is None or job.info is None:
        raise OSError("Searcher job has no source or probe information")
    if job.source_path.parent != job.dir:
        raise OSError("Searcher source must be a direct job file")
    if not _SAFE_JOB_ID.fullmatch(job.id):
        raise OSError("unsafe job id")
    payload = {
        "schema_version": _JOB_METADATA_SCHEMA,
        "id": job.id,
        "source": job.source_path.name,
        "info": {
            "width": job.info.width,
            "height": job.info.height,
            "duration": job.info.duration,
            "has_audio": job.info.has_audio,
            "coded_width": job.info.coded_width,
            "coded_height": job.info.coded_height,
            "rotation": job.info.rotation,
            "sample_aspect_ratio": job.info.sample_aspect_ratio,
            "field_order": job.info.field_order,
        },
        "title": job.searcher_title,
        "clip": job.searcher_metadata,
        "manifest": job.searcher_manifest,
        "words": [word.model_dump() for word in job.words],
        "reference_words": [word.model_dump() for word in job.reference_words],
        "crop_plan": job.crop_plan.model_dump() if job.crop_plan else None,
        "music_plan": job.music_plan.model_dump() if job.music_plan else None,
    }
    metadata = job.dir / JOB_METADATA_FILENAME
    temporary = metadata.with_suffix(".json.tmp")
    try:
        temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(temporary, metadata)
    finally:
        temporary.unlink(missing_ok=True)


def _recover_searcher_job(job_dir: Path) -> Job | None:
    """Recover a compact Searcher custody sidecar, or skip malformed data."""
    metadata = job_dir / JOB_METADATA_FILENAME
    try:
        payload = json.loads(metadata.read_text(encoding="utf-8"))
        job_id = payload["id"]
        source_name = payload["source"]
        info = MediaInfo(**payload["info"])
        clip = payload["clip"]
        manifest = payload["manifest"]
        title = payload.get("title", "")
        raw_words = payload.get("words", [])
        words = [Word.model_validate(word) for word in raw_words]
        raw_ref = payload.get("reference_words", raw_words)
        reference_words = [Word.model_validate(w) for w in raw_ref]
        raw_plan = payload.get("crop_plan")
        crop_plan = CropPlan.model_validate(raw_plan) if raw_plan is not None else None
        raw_music = payload.get("music_plan")
        music_plan = (
            CropPlan.model_validate(raw_music) if raw_music is not None else None
        )
        if (
            payload["schema_version"] != _JOB_METADATA_SCHEMA
            or not isinstance(job_id, str)
            or not _SAFE_JOB_ID.fullmatch(job_id)
            or job_id != job_dir.name
            or not isinstance(source_name, str)
            or not source_name
            or "/" in source_name
            or "\\" in source_name
            or Path(source_name).name != source_name
            or not isinstance(clip, dict)
            or not isinstance(manifest, dict)
            or not isinstance(title, str)
            or not isinstance(raw_words, list)
        ):
            return None
    except (KeyError, OSError, TypeError, ValueError):
        return None
    source_path = job_dir / source_name
    try:
        source_stat = source_path.lstat()
    except OSError:
        return None
    if stat.S_ISLNK(source_stat.st_mode) or not stat.S_ISREG(source_stat.st_mode):
        return None
    return Job(
        id=job_id,
        dir=job_dir,
        source_path=source_path,
        info=info,
        words=words,
        reference_words=reference_words,
        status="ready",
        searcher_title=title,
        searcher_metadata=clip,
        searcher_manifest=manifest,
        crop_plan=crop_plan,
        music_plan=music_plan,
    )


def recover_jobs() -> tuple[Job, ...]:
    """Load Searcher jobs from direct work directories after a restart."""
    with _JOBS_LOCK:
        try:
            entries = tuple(_ensure_work_root().iterdir())
        except OSError:
            return ()
        recovered: list[Job] = []
        for entry in entries:
            try:
                entry_stat = entry.lstat()
            except OSError:
                continue
            if entry.name in _JOBS or not stat.S_ISDIR(entry_stat.st_mode):
                continue
            job = _recover_searcher_job(entry)
            if job is None:
                continue
            _JOBS[job.id] = job
            recovered.append(job)
        return tuple(recovered)


def find_searcher_job(clip: dict, manifest: dict) -> Job | None:
    """Find a custody job to resume an interrupted Searcher pull (HIGH)."""
    with _JOBS_LOCK:
        for job in _JOBS.values():
            if job.searcher_metadata == clip and job.searcher_manifest == manifest:
                return job
        return None


def _open_directory(path: Path | str, *, dir_fd: int | None = None) -> int:
    """Open a directory without following a symlink at that path."""

    if dir_fd is None:
        return os.open(path, _DIRECTORY_OPEN_FLAGS)
    return os.open(path, _DIRECTORY_OPEN_FLAGS, dir_fd=dir_fd)


def _count_directory_files(dir_fd: int) -> tuple[int, int]:
    """Count regular files/symlinks and their own bytes below ``dir_fd``.

    ``dir_fd`` traversal never follows symlinks.  Symlink entries count as one
    file using the link's own byte length, never the target's size.
    """

    files = 0
    total_bytes = 0
    with os.scandir(dir_fd) as entries:
        for entry in entries:
            try:
                entry_stat = entry.stat(follow_symlinks=False)
            except FileNotFoundError:
                continue

            if stat.S_ISDIR(entry_stat.st_mode):
                child_fd = _open_directory(entry.name, dir_fd=dir_fd)
                try:
                    child_files, child_bytes = _count_directory_files(child_fd)
                finally:
                    os.close(child_fd)
                files += child_files
                total_bytes += child_bytes
            elif stat.S_ISREG(entry_stat.st_mode) or stat.S_ISLNK(entry_stat.st_mode):
                files += 1
                total_bytes += entry_stat.st_size

    return files, total_bytes


def _remove_directory_contents(dir_fd: int) -> None:
    """Remove entries below an open directory without following symlinks."""

    with os.scandir(dir_fd) as entries:
        for entry in entries:
            try:
                entry_stat = entry.stat(follow_symlinks=False)
            except FileNotFoundError:
                continue

            if stat.S_ISDIR(entry_stat.st_mode):
                child_fd = _open_directory(entry.name, dir_fd=dir_fd)
                try:
                    _remove_directory_contents(child_fd)
                finally:
                    os.close(child_fd)
                os.rmdir(entry.name, dir_fd=dir_fd)
            else:
                # This unlinks regular files, symlinks, and any unexpected
                # non-directory entry without ever traversing it.
                os.unlink(entry.name, dir_fd=dir_fd)


def active_jobs() -> tuple[Job, ...]:
    """Return jobs whose transcription or render work is still in progress."""
    with _JOBS_LOCK:
        # Cache protection must see durable jobs after a process restart too.
        recover_jobs()
        return tuple(job for job in _JOBS.values() if job.status in _ACTIVE_STATUSES)


def has_active_jobs() -> bool:
    """Return whether cache deletion must currently be refused."""

    return bool(active_jobs())


def cache_info() -> dict[str, int]:
    """Return cache contents without racing a clear or directory mutation."""
    with job_operation_lock():
        return _cache_info_unlocked()


def _cache_info_unlocked() -> dict[str, int]:
    """Describe direct job directories, contained files, and cached bytes.

    ``files`` and ``total_bytes`` include regular files and symlink entries
    below each direct job directory, plus regular/symlink files directly below
    ``WORK_ROOT``.  Symlink targets are never traversed or measured.
    """

    root = _ensure_work_root()
    root_fd = _open_directory(root)
    try:
        job_dirs = 0
        files = 0
        total_bytes = 0
        with os.scandir(root_fd) as entries:
            for entry in entries:
                try:
                    entry_stat = entry.stat(follow_symlinks=False)
                except FileNotFoundError:
                    continue

                if stat.S_ISDIR(entry_stat.st_mode):
                    job_dirs += 1
                    child_fd = _open_directory(entry.name, dir_fd=root_fd)
                    try:
                        child_files, child_bytes = _count_directory_files(child_fd)
                    finally:
                        os.close(child_fd)
                    files += child_files
                    total_bytes += child_bytes
                elif stat.S_ISREG(entry_stat.st_mode) or stat.S_ISLNK(
                    entry_stat.st_mode
                ):
                    files += 1
                    total_bytes += entry_stat.st_size

        return {"job_dirs": job_dirs, "files": files, "total_bytes": total_bytes}
    finally:
        os.close(root_fd)


def clear_cache() -> dict[str, int]:
    """Delete the cache while excluding concurrent job-directory mutations."""
    with job_operation_lock():
        return _clear_cache_unlocked()


def _clear_cache_unlocked() -> dict[str, int]:
    """Delete only direct children of ``WORK_ROOT`` and report what was removed.

    A direct directory is treated as one job cache and removed recursively;
    direct files are removed individually.  The root itself is opened and
    retained, and all descendant traversal is descriptor- and symlink-safe.
    Active transcription/render jobs refuse the operation so a parent route
    can translate ``ActiveJobsError`` into HTTP 409.
    """

    if has_active_jobs():
        raise ActiveJobsError("cannot clear media cache while a job is active")

    root = _ensure_work_root()
    root_fd = _open_directory(root)
    removed_job_dirs: set[str] = set()
    files_removed = 0
    bytes_removed = 0

    try:
        with os.scandir(root_fd) as entries:
            for entry in entries:
                try:
                    entry_stat = entry.stat(follow_symlinks=False)
                except FileNotFoundError:
                    continue

                if stat.S_ISDIR(entry_stat.st_mode):
                    child_fd = _open_directory(entry.name, dir_fd=root_fd)
                    try:
                        child_files, child_bytes = _count_directory_files(child_fd)
                        _remove_directory_contents(child_fd)
                    finally:
                        os.close(child_fd)
                    os.rmdir(entry.name, dir_fd=root_fd)
                    removed_job_dirs.add(entry.name)
                    files_removed += child_files
                    bytes_removed += child_bytes
                else:
                    os.unlink(entry.name, dir_fd=root_fd)
                    if stat.S_ISREG(entry_stat.st_mode) or stat.S_ISLNK(
                        entry_stat.st_mode
                    ):
                        files_removed += 1
                        bytes_removed += entry_stat.st_size
    finally:
        os.close(root_fd)

    # Remove stale in-memory handles for cache directories that were deleted.
    # This keeps the existing get_job API from returning jobs whose files are
    # known to be gone, while leaving unrelated in-memory state untouched.
    for job_id, job in list(_JOBS.items()):
        job_dir = Path(job.dir)
        if job_dir.parent == root and job_dir.name in removed_job_dirs:
            _JOBS.pop(job_id, None)

    return {
        "job_dirs_removed": len(removed_job_dirs),
        "files_removed": files_removed,
        "bytes_removed": bytes_removed,
    }


def create_job() -> Job:
    with _JOBS_LOCK:
        root = _ensure_work_root()
        # A durable directory may outlive the in-memory process.  Avoid even
        # an improbable UUID collision rather than attaching a new live job to
        # somebody else's custody.
        while True:
            job_id = uuid.uuid4().hex[:12]
            job_dir = root / job_id
            if job_id in _JOBS or job_dir.exists():
                continue
            job_dir.mkdir()
            job = Job(id=job_id, dir=job_dir)
            _JOBS[job_id] = job
            return job


def get_job(job_id: str) -> Job | None:
    with _JOBS_LOCK:
        if not isinstance(job_id, str) or not _SAFE_JOB_ID.fullmatch(job_id):
            return None
        existing = _JOBS.get(job_id)
        if existing is not None:
            return existing
        root = _ensure_work_root()
        job_dir = root / job_id
        job = _recover_searcher_job(job_dir)
        if job is not None:
            _JOBS[job.id] = job
        return job
