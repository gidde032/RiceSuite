"""Issue #96: durable manual origin and observable, explicit serial work."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ricesearcher.acquire.base import AcquiredSource
from ricesearcher.beat.profile import ensure_seed, load_profile
from ricesearcher.config import Config
from ricesearcher.dedup.annotate import SIM_THRESHOLD
from ricesearcher.library.store import MIGRATIONS, Library
from ricesearcher.models import (
    CandidateSlice,
    SliceStatus,
    Source,
    SourceKind,
    TranscriptWord,
)
from ricesearcher.pipeline import extract_and_score
from ricesearcher.score.base import ScoredResult
from ricesearcher.transcribe.base import DEFAULT_TRANSCRIPTION_MODEL
from ricesearcher.web.app import create_app
from ricesearcher.workspace import Adapters, Item, Options, RunManager


class FakeAdapters:
    def __init__(self, tmp_path: Path):
        self.media = tmp_path / "fixture.mp4"
        self.media.write_bytes(b"synthetic media")
        self.calls = []
        self.asr_fail = False
        self.score_fail = False
        self.embed_fail = False
        self.entered = threading.Event()
        self.release = threading.Event()
        self.release.set()

    def can_handle(self, request):
        return request != "unsupported"

    def acquire(self, request):
        self.calls.append(("acquire", request))
        self.entered.set()
        assert self.release.wait(5)
        if request == "bad":
            raise RuntimeError("acquisition failed")
        return AcquiredSource(
            kind=SourceKind.LOCAL,
            ref=request,
            media_path=self.media,
            title="Synthetic",
            duration_s=40,
        )

    def transcribe(self, path):
        self.calls.append(("asr", path))
        if self.asr_fail:
            raise RuntimeError("ASR failed")
        return [TranscriptWord("hello", n, n + 0.5) for n in range(40)]

    model_name = "fake"

    def score(self, windows, profile):
        self.calls.append(("score", profile.version))
        if self.score_fail:
            raise RuntimeError("scoring failed")
        return [ScoredResult(score=0.7, rationale="fixture") for _ in windows]

    def embed(self, texts):
        self.calls.append(("embed", len(texts)))
        if self.embed_fail:
            raise RuntimeError("embedding failed")
        return [[1.0, 0.0] for _ in texts]

    def injected(self):
        return Adapters(
            acquirers=lambda: [self],
            transcriber=lambda _: self,
            scorer=lambda _: self,
            embedder=lambda: self,
        )


@pytest.fixture
def setup(tmp_path):
    cfg = Config(tmp_path / "data", tmp_path / "handoff")
    cfg.ensure_dirs()
    ensure_seed(cfg.profiles_dir)
    profile = load_profile("example-beat", profiles_dir=cfg.profiles_dir)
    fake = FakeAdapters(tmp_path)
    return cfg, profile, fake


def finished(manager, key):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        run = manager.snapshot(key)
        if run and run["status"] != "active" and manager.active is None:
            return run
        time.sleep(0.005)
    raise AssertionError("fixture worker did not finish")


def segment(source="source", **values):
    return CandidateSlice(
        id="segment",
        source_id=source,
        pad_in=0,
        pad_out=20,
        target_in=0,
        target_out=20,
        transcript_span="",
        profile_id="example-beat",
        **values,
    )


def test_v4_migration_preserves_all_rows_links_indexes_and_identifiable_edits(tmp_path):
    db = tmp_path / "old.sqlite3"
    with sqlite3.connect(db) as c:
        c.executescript(
            "CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);"
            + MIGRATIONS[1]
            + MIGRATIONS[2]
            + MIGRATIONS[3]
        )
        c.execute("INSERT INTO meta VALUES ('schema_version','3')")
        c.execute(
            "INSERT INTO sources(id,kind,ref,media_path) "
            "VALUES ('src','local','ref','/file')"
        )
        for ident, start in [
            ("src:example-beat:0-20000", 1),
            ("src:example-beat:20000-30000", 20),
        ]:
            c.execute(
                "INSERT INTO candidate_slices(id,source_id,profile_id,pad_in,pad_out,"
                "target_in,target_out,dup_of,status) "
                "VALUES (?,?,?,0,30,?,?,?,'candidate')",
                (
                    ident,
                    "src",
                    "example-beat",
                    start,
                    20 if start == 1 else 30,
                    "canonical",
                ),
            )
    with Library(db) as lib:
        assert lib._stored_version() == 4
        rows = lib.list_slices()
        assert {s.id for s in rows} == {
            "src:example-beat:0-20000",
            "src:example-beat:20000-30000",
        }
        assert all(s.dup_of == "canonical" and s.origin == "scored" for s in rows)
        assert lib.get_slice("src:example-beat:0-20000").window_edited
        assert not lib.get_slice("src:example-beat:20000-30000").window_edited
        assert lib.get_source("src").transcription_state == "complete"
        indexes = {
            r[1] for r in lib._conn.execute("PRAGMA index_list(candidate_slices)")
        }
        assert {
            "idx_slices_source",
            "idx_slices_score",
            "idx_slices_profile_status",
        } <= indexes


def test_manual_null_scores_and_human_edits_survive_rescore_and_reset(setup):
    cfg, profile, fake = setup
    source = Source(
        "source",
        SourceKind.LOCAL,
        "ref",
        str(fake.media),
        duration_s=40,
        words=fake.transcribe(fake.media),
    )
    with Library(cfg.db_path) as lib:
        lib.upsert_source(source)
        fresh = extract_and_score(source, profile=profile, scorer=fake, library=lib)
        assert fresh
        edited = fresh[0]
        lib.update_slice_window(edited.id, 2, 9)
        manual = segment(
            origin="manual",
            score=None,
            heuristic_score=None,
            status=SliceStatus.REVIEWED,
        )
        lib.upsert_slices([manual])
        lib.update_slice_status(manual.id, SliceStatus.CANDIDATE)
        extract_and_score(source, profile=profile, scorer=fake, library=lib)
        assert lib.get_slice(manual.id).score is None
        assert lib.get_slice(manual.id).origin == "manual"
        assert lib.get_slice(edited.id).target_in == 2
        assert lib.list_slices()[0].id == manual.id
        assert lib.list_slices(kind="manual")[0].heuristic_score is None
        lib.delete_candidate_slices(source.id)
        assert lib.get_slice(edited.id) and lib.get_slice(manual.id)


def test_batch_admission_is_atomic_frozen_stop_finishes_current(setup):
    cfg, profile, fake = setup
    fake.release.clear()
    manager = RunManager(cfg, fake.injected())
    run = manager.submit(
        "one",
        [Item(request="first"), Item(request="next")],
        profile,
        Options(score=True),
    )
    assert fake.entered.wait(2)
    assert (
        manager.submit(
            "one",
            [Item(request="first"), Item(request="next")],
            profile,
            Options(score=True),
        )["id"]
        == "one"
    )
    with pytest.raises(ValueError, match="different input"):
        manager.submit("one", [Item(request="changed")], profile, Options())
    with pytest.raises(ValueError, match="active"):
        manager.submit("two", [Item(request="x")], profile, Options())
    manager.stop("one")
    profile.keywords.append("mutated")
    snapshot = manager.snapshot("one")
    snapshot["items"].clear()
    assert len(manager.snapshot("one")["items"]) == 2
    fake.release.set()
    run = finished(manager, "one")
    assert [i["state"] for i in run["items"]] == ["complete", "unstarted"]
    assert manager.inputs["one"][1].keywords != profile.keywords
    assert run["options"]["transcription_model"] == DEFAULT_TRANSCRIPTION_MODEL
    assert run["options"]["threshold"] == SIM_THRESHOLD
    with pytest.raises(KeyError):
        manager.stop("missing")
    with pytest.raises(ValueError, match="no failed"):
        manager.retry("one", "retry")


@pytest.mark.parametrize(
    "failure,stage", [("asr_fail", "transcribing"), ("score_fail", "scoring")]
)
def test_retry_reuses_completed_stages_and_source_checkpoint(setup, failure, stage):
    cfg, profile, fake = setup
    setattr(fake, failure, True)
    manager = RunManager(cfg, fake.injected())
    manager.submit(
        "one",
        [Item(request="first"), Item(request="bad")],
        profile,
        Options(score=True),
    )
    run = finished(manager, "one")
    assert run["items"][0]["stage"] == stage
    assert run["items"][1]["stage"] == "acquiring"
    sid = run["items"][0]["source_id"]
    with Library(cfg.db_path) as lib:
        source = lib.get_source(sid)
        assert Path(source.media_path).is_file()
        assert source.transcription_state == (
            "pending" if failure == "asr_fail" else "complete"
        )
    count = len([c for c in fake.calls if c[0] == "asr"])
    setattr(fake, failure, False)
    manager.retry("one", "retry")
    retry = finished(manager, "retry")
    assert retry["items"][0]["state"] == "complete"
    assert len([c for c in fake.calls if c == ("acquire", "first")]) == 1
    if failure == "score_fail":
        assert len([c for c in fake.calls if c[0] == "asr"]) == count
    assert RunManager(cfg, fake.injected()).snapshot("one") is None


def test_sources_reused_empty_transcription_is_complete_and_dedup_retry(setup):
    cfg, profile, fake = setup
    fake.transcribe = lambda _: []
    manager = RunManager(cfg, fake.injected())
    manager.submit(
        "one", [Item(request="first"), Item(request="again")], profile, Options()
    )
    run = finished(manager, "one")
    assert all(i["state"] == "complete" for i in run["items"])
    sid = run["items"][0]["source_id"]
    with Library(cfg.db_path) as lib:
        lib.upsert_slices([segment(source=sid, status=SliceStatus.SELECTED)])
    fake.embed_fail = True
    manager.submit("dup", [], profile, Options(duplicates_only=True))
    assert finished(manager, "dup")["duplicate_error"] == "embedding failed"
    fake.embed_fail = False
    manager.retry("dup", "dup-retry")
    assert not finished(manager, "dup-retry")["duplicate_error"]
    manager.submit(
        "missing",
        [Item(source_id="absent"), Item(request="unsupported")],
        profile,
        Options(),
    )
    assert all(i["state"] == "failed" for i in finished(manager, "missing")["items"])
    with pytest.raises(KeyError):
        manager.retry("expired", "new")


def test_workspace_routes_manual_filters_and_profile_separation(setup, monkeypatch):
    cfg, profile, fake = setup
    app = create_app(cfg, adapters=fake.injected())
    monkeypatch.setattr("ricesearcher.web.workspace.ffprobe_duration", lambda _: 40)
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        payload = {"attempt_key": "first", "profile": profile.id, "requests": ["fake"]}
        assert client.post("/api/runs", json=payload).status_code == 200
        run = finished(app.state.runs, "first")
        sid = run["items"][0]["source_id"]
        assert client.get("/api/runs/current").json()["run"]["id"] == "first"
        assert client.get("/api/runs/first").status_code == 200
        assert client.get("/api/runs/missing").status_code == 404
        detail = client.get(f"/api/sources/{sid}").json()
        assert detail["transcription_state"] == "complete" and detail["words"]
        assert client.get("/api/sources/missing").status_code == 404
        for start, end in [(2, 2), (-1, 3), (0, 41)]:
            assert (
                client.post(
                    f"/api/sources/{sid}/manual",
                    json={"profile": profile.id, "target_in": start, "target_out": end},
                ).status_code
                == 422
            )
        manual = client.post(
            f"/api/sources/{sid}/manual",
            json={"profile": profile.id, "target_in": 2, "target_out": 9},
        ).json()
        assert manual["score"] is None and manual["status"] == "reviewed"
        slices = client.get(
            f"/api/slices?profile={profile.id}&kind=manual&source={sid}&status=reviewed"
        ).json()
        assert (
            len(slices) == 1
            and slices[0]["stale"] is False
            and slices[0]["scorer_model"] == ""
        )
        assert client.get(f"/api/slices?profile=other&source={sid}").json() == []
        assert (
            client.get(f"/api/slices?profile={profile.id}&kind=bad").status_code == 422
        )
        assert (
            client.post(
                f"/api/sources/{sid}/manual",
                json={"profile": "missing", "target_in": 2, "target_out": 9},
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/api/sources/missing/manual",
                json={"profile": profile.id, "target_in": 2, "target_out": 9},
            ).status_code
            == 404
        )
        assert (
            client.post(
                "/api/runs", json={"attempt_key": "empty", "profile": profile.id}
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/api/runs",
                json={
                    **payload,
                    "attempt_key": "bad-model",
                    "offline": True,
                    "model": "override",
                },
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/api/runs/missing/retry", json={"attempt_key": "retry"}
            ).status_code
            == 404
        )
        assert client.post("/api/runs/missing/stop").status_code == 404
        assert client.post("/api/runs/first/stop").status_code == 200
        assert (
            client.post(
                "/api/runs/first/retry", json={"attempt_key": "retry"}
            ).status_code
            == 409
        )
        assert client.get("/sources").status_code == 200
        assert client.get("/api/settings").json()["threshold"] == SIM_THRESHOLD


def test_browser_intake_safe_names_idempotence_and_cleanup_guards(setup):
    cfg, profile, fake = setup
    fake.release.clear()
    app = create_app(cfg, adapters=fake.injected())
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        settings = json.dumps({"profile": profile.id})
        data = {"attempt_key": "upload", "settings_json": settings}
        files = [("files", ("../../Original clip.mp4", b"fixture bytes", "video/mp4"))]
        assert client.post("/api/runs/files", data=data, files=files).status_code == 200
        assert fake.entered.wait(2)
        assert client.post("/api/runs/files", data=data, files=files).status_code == 200
        original = app.state.runs.inputs["upload"][0][0]
        assert Path(original.request).parent.parent == cfg.data_dir / "intake"
        assert Path(original.request).name != "Original clip.mp4"
        assert original.filename == "../../Original clip.mp4"
        assert client.post("/api/cache/clear").status_code == 409
        assert client.post("/api/sources/any/delete").status_code == 409
        fake.release.set()
        assert finished(app.state.runs, "upload")["items"][0]["state"] == "complete"
        assert not Path(original.request).exists()
        assert (
            client.post(
                "/api/runs/files", data={**data, "settings_json": "bad"}, files=files
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/api/runs/files",
                data={**data, "attempt_key": "bad-ext"},
                files=[("files", ("x.exe", b"x"))],
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/api/runs/files", data={**data, "attempt_key": "conflict"}, files=files
            ).status_code
            == 200
        )
        finished(app.state.runs, "conflict")


def test_credential_policy_and_admission(setup, monkeypatch):
    cfg, profile, fake = setup
    app = create_app(cfg)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        assert client.get("/api/settings").json()["scoring_available"] is False
        body = {
            "attempt_key": "paid",
            "profile": profile.id,
            "score": True,
            "requests": ["fake"],
        }
        assert client.post("/api/runs", json=body).status_code == 409
        assert app.state.runs.snapshot() is None
        monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "fixture-token")
        assert client.get("/api/settings").json()["scoring_available"] is True
        monkeypatch.setattr(
            app.state.runs, "submit", lambda *args: {"options": args[-1].__dict__}
        )
        monkeypatch.setenv("RICESEARCHER_SCORER_MODEL", "fixture-model")
        assert (
            client.post("/api/runs", json=body).json()["options"]["model"]
            == "fixture-model"
        )
