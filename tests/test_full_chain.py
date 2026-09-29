"""Full chain in mock mode (ADR-001 Q16): local file → select → render → Poster draft.

Each pillar runs in its own subprocess, from its own directory, through the
same server calls its page makes, with the media toolchain faked the way the
pillar suites fake it (no ffmpeg, no Whisper model). Everything lives in
tmp_path (HOME included), every process has a socket guard that refuses
non-loopback connections, and Poster runs with POST_MODE=mock and its
scheduler off. The chain ends at a staged Poster draft: nothing is posted,
scheduled or queued, and a test double fails if any posting function runs.
"""

import json
import os
import subprocess
import sys

import pytest

from ricesuite import SUITE_ROOT
from ricesuite import env as suite_env

GUARD = r"""
import socket
_LOOP = {"127.0.0.1", "::1", "localhost", "testserver"}
_connect, _gai = socket.socket.connect, socket.getaddrinfo
def _guard_connect(self, address):
    host = address[0] if isinstance(address, tuple) else address
    if host not in _LOOP:
        raise OSError(f"network blocked in test: {address!r}")
    return _connect(self, address)
def _guard_gai(host, *a, **k):
    if host not in _LOOP:
        raise OSError(f"DNS blocked in test: {host!r}")
    return _gai(host, *a, **k)
socket.socket.connect, socket.getaddrinfo = _guard_connect, _guard_gai
"""

SEARCHER = r"""
import json, sys
from pathlib import Path
from fastapi.testclient import TestClient
from ricesearcher.acquire.watchfolder import WatchFolderAcquirer
from ricesearcher.beat.profile import ensure_seed, load_profile
from ricesearcher.config import load_config
from ricesearcher.handoff import writer as writer_mod
from ricesearcher.library.cache import MediaCache
from ricesearcher.library.store import LEGACY_PROFILE_ID, Library
from ricesearcher.models import TranscriptWord
from ricesearcher.pipeline import extract_and_score, pull
from ricesearcher.score.heuristic_scorer import HeuristicScorer
from ricesearcher.web.app import create_app

class FakeTranscriber:  # stands in for faster-whisper
    def transcribe(self, media_path):
        text = ("so here is the thing nobody tells you about this amazing story "
                "and honestly it changed everything for me today ") * 6
        return [TranscriptWord(w, i * 0.5, i * 0.5 + 0.4)
                for i, w in enumerate(text.split())]

class FakeExtractor:  # stands in for the ffmpeg window cut
    def extract(self, source, start, end, dest):
        Path(dest).write_bytes(b"clip " + Path(source).read_bytes()[:16])
        return end - start

writer_mod.FfmpegClipExtractor = FakeExtractor
writer_mod.ffprobe_duration = lambda _p: 30.0

cfg = load_config()
cfg.ensure_dirs()
ensure_seed(cfg.profiles_dir)
local_file = Path(sys.argv[1])
with Library(cfg.db_path) as lib:
    source = pull(str(local_file),
                  acquirers=[WatchFolderAcquirer(lambda _p: 60.0)],
                  transcriber=FakeTranscriber(),
                  cache=MediaCache(cfg.cache_dir), library=lib)
    profile = load_profile(LEGACY_PROFILE_ID, profiles_dir=cfg.profiles_dir)
    slices = extract_and_score(source, profile=profile,
                               scorer=HeuristicScorer(), library=lib)
assert slices, "the heuristic scorer produced no candidates"

client = TestClient(create_app(cfg))
chosen = slices[0].id
# The human gate: select in the review UI, then "Send selected".
r = client.post(f"/api/slices/{chosen}/status", json={"status": "selected"})
assert r.status_code == 200, r.text
r = client.post("/api/handoff", json={"profile": LEGACY_PROFILE_ID})
assert r.status_code == 200, r.text
print(json.dumps({"source_kind": str(source.kind), **r.json()}))
"""

CLIPPER = r"""
import json, sys
from pathlib import Path
from fastapi.testclient import TestClient
from app import jobs, main, probe
from app.models import Word
from app.probe import MediaInfo

assert jobs.WORK_ROOT == Path(sys.argv[1])
jobs.WORK_ROOT.mkdir(parents=True, exist_ok=True)
probe.probe = lambda _p: MediaInfo(
    width=1080, height=1920, duration=30.0, has_audio=True)
main.whisper.transcribe = lambda _p: [Word(text="hello", start=0.0, end=0.5),
                                      Word(text="world", start=0.5, end=1.0)]

def fake_render(work_dir, source_path, info, req, plan=None):  # stands in for ffmpeg
    out = Path(work_dir) / jobs.RENDERED_OUTPUT_FILENAME
    out.write_bytes(b"rendered " + Path(source_path).read_bytes()[:16])
    return out

main.render = fake_render
client = TestClient(main.app)

inbox = client.get("/api/searcher-inbox").json()["batches"]
assert len(inbox) == 1, inbox  # the page sees it waiting
pulled = client.post("/api/pull-from-searcher").json()           # automatic pull
assert pulled["clip_count"] >= 1, pulled
ids = [j["id"] for j in pulled["jobs"]]
for job_id in ids:  # automatic transcription
    r = client.post(f"/api/jobs/{job_id}/transcribe")
    assert r.status_code == 200 and r.json()["status"] == "ready", r.text
assert client.get("/api/searcher-inbox").json()["batches"] == []
for job_id in ids:                                               # human review + render
    r = client.post(f"/api/jobs/{job_id}/render", json={"words": [], "header": "Hi"})
    assert r.status_code == 200 and r.json()["status"] == "done", r.text
sent = client.post("/api/handoff", json={"clips": [                # automatic send
    {"job_id": j, "position": i + 1, "transcript": "hello world", "header": "Hi",
     "caption_style": "classic", "header_style": "plain"} for i, j in enumerate(ids)]})
assert sent.status_code == 200, sent.text
print(json.dumps({"searcher_batch": pulled["batch_id"], **sent.json()}))
"""

POSTER = r"""
import json
from pathlib import Path
from fastapi.testclient import TestClient
from backend import config, main

assert config.POST_MODE == "mock"
def forbidden(*a, **k):
    raise AssertionError("the transport chain must never post or schedule")
main.post_all_api = main.post_all_browser = main.add_batch = forbidden

with TestClient(main.app) as client:
    inbox = client.get("/api/handoff/inbox").json()
    assert len(inbox["batches"]) == 1, inbox                     # waiting in the inbox
    pulled = client.post("/api/pull-from-clipper").json()        # automatic ingest
    assert pulled["pulled"], pulled
    ack = client.post(f"/api/pull-from-clipper/{pulled['batch_id']}/ack")
    assert ack.status_code == 200, ack.text
    queue = client.get("/api/queue").json()
    history = client.get("/api/history").json()
    after = client.get("/api/handoff/inbox").json()
staged = [s["filename"] for s in pulled["slots"]]
print(json.dumps({
    "batch_id": pulled["batch_id"],
    "slots": [s["slot"] for s in pulled["slots"]],
    "staged_exist": all((config.MEDIA_DIR / f).is_file() for f in staged),
    "media_dir": str(config.MEDIA_DIR),
    "queue": queue["batches"], "history": history, "inbox_after": after,
}))
"""


def _run(pillar, script, env, *args):
    result = subprocess.run(
        [sys.executable, "-c", GUARD + script, *args],
        cwd=SUITE_ROOT / pillar,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, f"{pillar} step failed:\n{result.stderr[-4000:]}"
    return json.loads(result.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize("unified", [False, True])
def test_local_file_to_poster_draft_in_mock_mode(tmp_path, unified):
    home = tmp_path / "home"
    data_root = home / ".ricesuite"
    poster_data = data_root / "poster" if unified else tmp_path / "poster-data"
    for d in (home, poster_data):
        d.mkdir(parents=True, exist_ok=True)
    local_file = tmp_path / "interview.mp4"
    local_file.write_bytes(b"not really a video, the toolchain is faked")
    s2c = (
        data_root / "handoff/searcher-to-clipper"
        if unified
        else tmp_path / "searcher-handoff"
    )
    c2p = (
        data_root / "handoff/clipper-to-poster"
        if unified
        else tmp_path / "clipper-handoff"
    )
    clipper_work = data_root / "clipper" if unified else tmp_path / "clipper-work"
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(home),
        "RICESEARCHER_DATA_DIR": str(tmp_path / "searcher-data"),
        "RICECLIPPER_WORK_DIR": str(clipper_work),
        "RICESEARCHER_HANDOFF_DIR": str(s2c),
        "RICECLIPPER_SEARCHER_INBOX": str(s2c),
        "RICECLIPPER_HANDOFF_DIR": str(c2p),
        "HANDOFF_DIR": str(c2p),
        "RICEPOSTER_DATA_DIR": str(poster_data),
        "POST_MODE": "mock",
        "SCHEDULER_ENABLED": "false",
        "ANTHROPIC_API_KEY": "",
    }
    if unified:
        configured = suite_env.load(
            tmp_path / "missing.env",
            base={
                "HOME": str(home),
                "RICESUITE_DATA_DIR": str(data_root),
                "PATH": os.environ["PATH"],
                "POST_MODE": "mock",
                "SCHEDULER_ENABLED": "false",
                "ANTHROPIC_API_KEY": "",
            },
        )
        env = configured

    searcher = _run("searcher", SEARCHER, env, str(local_file))
    assert searcher["source_kind"] == "local"
    assert searcher["clip_count"] == 1
    assert (s2c / searcher["batch_id"] / "manifest.json").is_file()

    clipper = _run("clipper", CLIPPER, env, str(clipper_work))
    assert clipper["searcher_batch"] == searcher["batch_id"]
    assert clipper["clip_count"] == 1
    assert (c2p / clipper["batch_id"] / "manifest.json").is_file()

    poster = _run("poster", POSTER, env)
    assert poster["batch_id"] == clipper["batch_id"]
    assert len(poster["slots"]) == 1
    assert poster["staged_exist"] is True
    assert poster["media_dir"] == str(poster_data / "media")
    # A draft, and nothing more: no queue entry, no post history, no sessions.
    assert poster["queue"] == []
    assert poster["history"] == {"entries": []}
    assert poster["inbox_after"]["batches"] == []
    assert poster["inbox_after"]["unacknowledged"] is None
    # Importing Poster's browser modules creates empty session folders, and a
    # pull records the account roster; no browser profile may appear.
    session_files = [
        str(p.relative_to(poster_data))
        for p in (poster_data / "sessions").rglob("*")
        if p.is_file()
    ]
    assert set(session_files) <= {"sessions/.account-state.json"}
    assert not (poster_data / ".post-in-flight.json").exists()
