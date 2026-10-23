"""Fail-before-fix regressions from the three #96 cold reviews."""

from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ricesearcher.beat.profile import ensure_seed, load_profile
from ricesearcher.config import Config
from ricesearcher.handoff.writer import hand_off_selected
from ricesearcher.library.store import Library
from ricesearcher.models import SliceStatus, Source, SourceKind
from ricesearcher.pipeline import annotate_library_duplicates
from ricesearcher.web.app import create_app
from ricesearcher.workspace import Item, Options, RunManager
from tests.test_workspace import FakeAdapters, finished, segment


@pytest.fixture
def setup(tmp_path):
    cfg = Config(tmp_path / "data", tmp_path / "handoff")
    cfg.ensure_dirs()
    ensure_seed(cfg.profiles_dir)
    return (
        cfg,
        load_profile("example-beat", profiles_dir=cfg.profiles_dir),
        FakeAdapters(tmp_path),
    )


def test_retained_retry_owns_shared_staging_until_acquisition_finishes(setup):
    """Correctness/MEDIUM: evicting A must not delete retained retry B's input."""
    cfg, profile, fake = setup
    manager = RunManager(cfg, fake.injected())
    staging = cfg.cache_dir / "intake" / "upload"
    staging.mkdir(parents=True)
    media = staging / "fixture.mp4"
    media.write_bytes(b"staged upload")
    fail = True
    original_acquire = fake.acquire

    def acquire(request):
        assert Path(request).is_file(), "staged file missing"
        if fail:
            raise RuntimeError("acquisition failed")
        return original_acquire(request)

    fake.acquire = acquire
    manager.submit(
        "A",
        [Item(request=str(media), filename="fixture.mp4", upload_digest="digest")],
        profile,
        Options(),
    )
    finished(manager, "A")
    manager.retry("A", "B")
    finished(manager, "B")
    for n in range(31):
        key = f"observation-{n}"
        manager.submit(key, [], profile, Options(duplicates_only=True))
        finished(manager, key)
    assert manager.snapshot("A") is None and manager.snapshot("B") is not None
    assert media.is_file(), "a retained retry lost its acquisition input"
    fail = False
    manager.retry("B", "recovered")
    assert finished(manager, "recovered")["items"][0]["state"] == "complete"


def test_stopped_unstarted_upload_is_discarded(setup):
    """Skeptic/MEDIUM: Stop must not strand unstarted browser media."""
    cfg, _, fake = setup
    fake.release.clear()
    app = create_app(cfg, adapters=fake.injected())
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        result = client.post(
            "/api/runs/files",
            data={
                "attempt_key": "upload",
                "settings_json": '{"profile":"example-beat"}',
            },
            files=[
                ("files", ("first.mp4", b"first")),
                ("files", ("second.mp4", b"second")),
            ],
        )
        assert result.status_code == 200
        assert fake.entered.wait(2)
        unstarted = Path(app.state.runs.inputs["upload"][0][1].request)
        app.state.runs.stop("upload")
        fake.release.set()
        assert finished(app.state.runs, "upload")["items"][1]["state"] == "unstarted"
        assert not unstarted.exists()


def test_clear_entire_cache_removes_failed_browser_staging_after_restart(setup):
    """Skeptic/MEDIUM: intake bytes participate in the deliberate full purge."""
    cfg, _, fake = setup

    def acquire(_):
        raise RuntimeError("failed acquisition")

    fake.acquire = acquire
    app = create_app(cfg, adapters=fake.injected())
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        result = client.post(
            "/api/runs/files",
            data={
                "attempt_key": "upload",
                "settings_json": '{"profile":"example-beat"}',
            },
            files=[("files", ("failed.mp4", b"failed"))],
        )
        assert result.status_code == 200
        finished(app.state.runs, "upload")
        staged = Path(app.state.runs.inputs["upload"][0][0].request)
        assert staged.exists()
        assert client.post("/api/cache/clear").status_code == 200
        assert not staged.exists()
    assert create_app(cfg, adapters=fake.injected()).state.runs.snapshot() is None


def test_advisory_duplicate_changes_do_not_cancel_selected_handoff(setup):
    """Skeptic/MEDIUM: background advisory annotations never block delivery."""
    cfg, profile, fake = setup
    source = Source("source", SourceKind.LOCAL, "fixture", str(fake.media))

    class Extractor:
        def extract(self, media, start, end, dest):
            with Library(cfg.db_path) as concurrent:
                annotate_library_duplicates(concurrent, fake, profile_id=profile.id)
            Path(dest).write_bytes(b"exported")
            return end - start

    with Library(cfg.db_path) as lib:
        lib.upsert_source(source)
        first = segment(status=SliceStatus.SELECTED)
        lib.upsert_slices(
            [first, replace(first, id="second", target_in=1, target_out=19)]
        )
        result = hand_off_selected(
            lib,
            config=cfg,
            profile_id=profile.id,
            extractor=Extractor(),
            duration_prober=lambda _: 40,
        )
        assert result["clip_count"] == 2
        assert all(s.status is SliceStatus.HANDED_OFF for s in lib.list_slices())


def test_non_scoring_operations_ignore_mutually_exclusive_scorer_settings(setup):
    """Parent acceptance: Add only/dedup have no scorer-model dependency."""
    cfg, profile, fake = setup
    app = create_app(cfg, adapters=fake.injected())
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        for key, extra in [
            ("add", {"requests": ["fixture"]}),
            ("dedup", {"duplicates_only": True}),
        ]:
            result = client.post(
                "/api/runs",
                json={
                    "attempt_key": key,
                    "profile": profile.id,
                    "offline": True,
                    "model": "unused",
                    **extra,
                },
            )
            assert result.status_code == 200
            finished(app.state.runs, key)


def test_restart_discards_orphaned_intake_without_dispatching_work(setup):
    """Skeptic/MEDIUM: process-local observations do not leave restart orphans."""
    cfg, _, fake = setup
    orphan = cfg.cache_dir / "intake" / "orphan" / "source.mp4"
    orphan.parent.mkdir(parents=True)
    orphan.write_bytes(b"unstarted before crash")
    manager = RunManager(cfg, fake.injected())
    assert manager.snapshot() is None and not fake.calls
    assert not orphan.exists()
