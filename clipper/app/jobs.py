"""In-memory job store with per-job work directories.

Single-clip, single-process (SPEC.md §9: no batch in v1). Each job owns a work
dir under ``.riceclipper_work/<id>/`` holding the uploaded source, any music
track, the generated ASS, and the rendered output. The dir is gitignored.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from app.models import JobState, Word
from app.probe import MediaInfo

WORK_ROOT = Path(__file__).resolve().parent.parent / ".riceclipper_work"


@dataclass
class Job:
    id: str
    dir: Path
    source_path: Optional[Path] = None
    info: Optional[MediaInfo] = None
    words: list[Word] = field(default_factory=list)
    status: str = "transcribing"
    error: Optional[str] = None
    output_path: Optional[Path] = None

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
        )


_JOBS: dict[str, Job] = {}


def create_job() -> Job:
    job_id = uuid.uuid4().hex[:12]
    job_dir = WORK_ROOT / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    job = Job(id=job_id, dir=job_dir)
    _JOBS[job_id] = job
    return job


def get_job(job_id: str) -> Optional[Job]:
    return _JOBS.get(job_id)
