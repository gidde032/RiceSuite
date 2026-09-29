"""Offline migration checks on disposable synthetic stores only."""

import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from ricesuite import env, migration


def fixture(tmp_path: Path):
    old = tmp_path / "legacy é data"
    paths = {
        "RICESEARCHER_DATA_DIR": old / "searcher",
        "RICECLIPPER_WORK_DIR": old / "clipper",
        "RICEPOSTER_DATA_DIR": old / "poster",
        "RICESEARCHER_HANDOFF_DIR": old / "handoff-1",
        "RICECLIPPER_HANDOFF_DIR": old / "handoff-2",
    }
    for path in paths.values():
        path.mkdir(parents=True)
    cache = paths["RICESEARCHER_DATA_DIR"] / "cache"
    cache.mkdir()
    (cache / "source.mp4").write_bytes(b"video")
    with (
        closing(
            sqlite3.connect(paths["RICESEARCHER_DATA_DIR"] / "library.sqlite3")
        ) as conn,
        conn,
    ):
        conn.execute("CREATE TABLE sources (media_path TEXT)")
        conn.execute("INSERT INTO sources VALUES (?)", (str(cache / "source.mp4"),))
    job = paths["RICECLIPPER_WORK_DIR"] / "job-1"
    job.mkdir()
    (job / "source.mp4").write_bytes(b"clip")
    (paths["RICECLIPPER_WORK_DIR"] / ".open_searcher_batches.json").write_text(
        '[{"batch_id":"b1","job_ids":["job-1"]}]'
    )
    (paths["RICESEARCHER_HANDOFF_DIR"] / ".consumed.json").write_text('["b0"]')
    batch = paths["RICECLIPPER_HANDOFF_DIR"] / "batch-1"
    batch.mkdir()
    (batch / "clip_1.mp4").write_bytes(b"render")
    (batch / "manifest.json").write_text('{"batch_id":"batch-1"}')
    poster = paths["RICEPOSTER_DATA_DIR"]
    sessions = poster / "sessions/instagram/A"
    sessions.mkdir(parents=True)
    (sessions / "Preferences").write_text("fixture only")
    (poster / "sessions/instagram/SingletonLock").symlink_to("missing-pid")
    media = poster / "queue_media/q1"
    media.mkdir(parents=True)
    (media / "scheduled.mp4").write_bytes(b"scheduled")
    (poster / "queue.jsonl").write_text(
        json.dumps(
            {
                "id": "q1",
                "status": "pending",
                "slots": [{"media_path": str(media / "scheduled.mp4")}],
            }
        )
        + "\n"
    )
    (poster / "history.jsonl").write_text('{"id":"posted"}\n')
    (poster / ".post-in-flight.json").write_text('{"run_id":"uncertain"}')
    config = tmp_path / "ricesuite.env"
    config.write_text(
        "".join(f"{key}={value}\n" for key, value in paths.items()) + "POST_MODE=mock\n"
    )
    return paths, config, tmp_path / "new unified"


def test_copy_cutover_and_prewrite_rollback(tmp_path):
    paths, config, root = fixture(tmp_path)
    report = migration.plan(root, env.read_env_file(config))
    assert report["bytes"] > 0
    assert not root.exists()
    migration.copy(root, env.read_env_file(config))
    assert not (root / migration.MARKER).exists()
    assert (
        root / "poster/sessions/instagram/A/Preferences"
    ).read_text() == "fixture only"
    assert not (root / "poster/sessions/instagram/SingletonLock").exists()
    with closing(sqlite3.connect(root / "searcher/library.sqlite3")) as conn, conn:
        assert conn.execute("SELECT media_path FROM sources").fetchone()[0] == str(
            root / "searcher/cache/source.mp4"
        )
    queue = json.loads((root / "poster/queue.jsonl").read_text())
    assert queue["slots"][0]["media_path"] == str(
        root / "poster/queue_media/q1/scheduled.mp4"
    )
    assert (root / "poster/history.jsonl").read_text() == '{"id":"posted"}\n'
    assert (root / "poster/.post-in-flight.json").is_file()
    assert (root / "handoff/searcher-to-clipper/.consumed.json").is_file()
    assert (root / "handoff/clipper-to-poster/batch-1/manifest.json").is_file()
    old_config = config.read_bytes()
    migration.cutover(root, config, {})
    assert env.read_env_file(config)["RICESUITE_DATA_DIR"] == str(root)
    assert (root / migration.MARKER).is_file()
    migration.rollback(root, config, {})
    assert config.read_bytes() == old_config
    assert not (root / migration.MARKER).exists()
    assert (paths["RICEPOSTER_DATA_DIR"] / "queue.jsonl").is_file()


def test_new_activity_blocks_rollback(tmp_path):
    _, config, root = fixture(tmp_path)
    migration.copy(root, env.read_env_file(config))
    migration.cutover(root, config, {})
    with (root / "poster/history.jsonl").open("a") as stream:
        stream.write('{"id":"new"}\n')
    with pytest.raises(migration.MigrationError, match="data changed"):
        migration.rollback(root, config, {})
    assert (root / migration.MARKER).exists()


def test_source_change_blocks_cutover(tmp_path):
    paths, config, root = fixture(tmp_path)
    migration.copy(root, env.read_env_file(config))
    (paths["RICEPOSTER_DATA_DIR"] / "history.jsonl").write_text("changed\n")
    with pytest.raises(migration.MigrationError, match="original data changed"):
        migration.cutover(root, config, {})
    assert not (root / migration.MARKER).exists()


def test_collision_symlink_and_partial_stage_are_refused(tmp_path):
    paths, config, root = fixture(tmp_path)
    values = env.read_env_file(config)
    with pytest.raises(migration.MigrationError, match="overlaps source"):
        migration.plan(paths["RICESEARCHER_DATA_DIR"] / "nested", values)
    root.mkdir()
    with pytest.raises(migration.MigrationError, match="already exists"):
        migration.plan(root, values)
    root.rmdir()
    (root.with_name(root.name + ".migration-stage")).mkdir()
    with pytest.raises(migration.MigrationError, match="partial stage"):
        migration.copy(root, values)
    (root.with_name(root.name + ".migration-stage")).rmdir()
    (paths["RICEPOSTER_DATA_DIR"] / "sessions/instagram/A/bad").symlink_to(
        "/etc/passwd"
    )
    with pytest.raises(migration.MigrationError, match="unsupported symlink"):
        migration.plan(root, values)


def test_bad_queue_reference_leaves_originals_and_stage(tmp_path):
    paths, config, root = fixture(tmp_path)
    queue = paths["RICEPOSTER_DATA_DIR"] / "queue.jsonl"
    queue.write_text(
        json.dumps({"id": "q1", "slots": [{"media_path": "/other/place"}]}) + "\n"
    )
    with pytest.raises(migration.MigrationError, match="outside queue_media"):
        migration.copy(root, env.read_env_file(config))
    assert not root.exists()
    assert root.with_name(root.name + ".migration-stage").exists()
    assert queue.exists()


def test_missing_searcher_cache_reference_blocks_copy(tmp_path):
    paths, config, root = fixture(tmp_path)
    with (
        closing(
            sqlite3.connect(paths["RICESEARCHER_DATA_DIR"] / "library.sqlite3")
        ) as conn,
        conn,
    ):
        conn.execute(
            "UPDATE sources SET media_path=?",
            (str(paths["RICESEARCHER_DATA_DIR"] / "cache/missing.mp4"),),
        )
    with pytest.raises(migration.MigrationError, match="missing Searcher cache"):
        migration.copy(root, env.read_env_file(config))
    assert not root.exists()


def test_copy_hash_mismatch_never_creates_destination(tmp_path, monkeypatch):
    _, config, root = fixture(tmp_path)
    actual_copy = migration._copy

    def corrupt_copy(source, target):
        actual_copy(source, target)
        if (
            target
            == root.with_name(root.name + ".migration-stage") / "poster/history.jsonl"
        ):
            target.write_text("corrupt")

    monkeypatch.setattr(migration, "_copy", corrupt_copy)
    with pytest.raises(migration.MigrationError, match="copy verification failed"):
        migration.copy(root, env.read_env_file(config))
    assert not root.exists()


def test_copy_verifies_empty_directories(tmp_path, monkeypatch):
    paths, config, root = fixture(tmp_path)
    (paths["RICEPOSTER_DATA_DIR"] / "sessions/tiktok/empty").mkdir(parents=True)
    actual_copy = migration._copy

    def drop_empty_directory(source, target):
        actual_copy(source, target)
        if target == root.with_name(root.name + ".migration-stage") / "poster/sessions":
            (target / "tiktok/empty").rmdir()

    monkeypatch.setattr(migration, "_copy", drop_empty_directory)
    with pytest.raises(migration.MigrationError, match="copy verification failed"):
        migration.copy(root, env.read_env_file(config))


def test_explicit_profile_override_is_carried_into_cutover(tmp_path):
    _, config, root = fixture(tmp_path)
    profiles = tmp_path / "custom profiles"
    profiles.mkdir()
    (profiles / "my-beat.json").write_text("{}")
    with config.open("a") as stream:
        stream.write(f"RICESEARCHER_PROFILES_DIR={profiles}\n")
    migration.copy(root, env.read_env_file(config))
    migration.cutover(root, config, {})
    assert env.read_env_file(config)["RICESEARCHER_PROFILES_DIR"] == str(
        root / "searcher-profiles"
    )
    assert (root / "searcher-profiles/my-beat.json").is_file()


def test_config_switch_failure_restores_old_configuration(tmp_path, monkeypatch):
    _, config, root = fixture(tmp_path)
    migration.copy(root, env.read_env_file(config))
    before = config.read_bytes()
    real_replace = migration.os.replace

    def fail_config_replace(source, destination):
        if Path(destination) == config:
            raise OSError("synthetic config switch failure")
        return real_replace(source, destination)

    monkeypatch.setattr(migration.os, "replace", fail_config_replace)
    with pytest.raises(OSError, match="synthetic"):
        migration.cutover(root, config, {})
    assert config.read_bytes() == before
    assert not (root / migration.MARKER).exists()


def test_rollback_refuses_config_edited_after_cutover(tmp_path):
    _, config, root = fixture(tmp_path)
    migration.copy(root, env.read_env_file(config))
    migration.cutover(root, config, {})
    with config.open("a") as stream:
        stream.write("LOG_LEVEL=DEBUG\n")
    with pytest.raises(migration.MigrationError, match="configuration changed"):
        migration.rollback(root, config, {})
