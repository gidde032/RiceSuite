"""Home view counts batches waiting at each handoff (FR-11). Read-only."""

import json

from ricesuite import home


def _batch(root, name, batch_id=None, clips=2, manifest=True):
    d = root / name
    d.mkdir(parents=True)
    for i in range(clips):
        (d / f"clip_{i + 1}.mp4").write_bytes(b"x")
    if manifest:
        (d / "manifest.json").write_text(
            json.dumps(
                {
                    "batch_id": batch_id or name,
                    "created_at": "2026-09-26T00:00:00Z",
                    "clips": [{"file": f"clip_{i + 1}.mp4"} for i in range(clips)],
                }
            )
        )
    return d


def _snapshot(root):
    return sorted(
        (str(p.relative_to(root)), p.stat().st_mtime_ns) for p in root.rglob("*")
    )


def test_counts_complete_batches_oldest_first(tmp_path):
    _batch(tmp_path, "batch_2", clips=1)
    _batch(tmp_path, "batch_1", clips=3)
    _batch(tmp_path, "batch_3", manifest=False)  # still being written
    got = home.ready_batches(tmp_path)
    assert [b["batch_id"] for b in got] == ["batch_1", "batch_2"]
    assert [b["clips"] for b in got] == [3, 1]


def test_clipper_consumed_batches_are_not_waiting(tmp_path):
    s2c = tmp_path / "s2c"
    c2p = tmp_path / "c2p"
    _batch(s2c, "batch_a")
    _batch(s2c, "batch_b")
    (s2c / ".riceclipper_consumed.json").write_text(json.dumps(["batch_a"]))
    stages = home.handoff_stages(
        {"RICESEARCHER_HANDOFF_DIR": str(s2c), "RICECLIPPER_HANDOFF_DIR": str(c2p)}
    )
    assert [b["batch_id"] for b in stages["to_clipper"]] == ["batch_b"]
    assert stages["to_poster"] == []


def test_posters_archive_is_not_waiting(tmp_path):
    _batch(tmp_path, "batch_live")
    _batch(tmp_path / ".riceposter-consumed", "batch_done")
    assert [b["batch_id"] for b in home.ready_batches(tmp_path)] == ["batch_live"]


def test_bad_manifests_and_registries_are_tolerated(tmp_path):
    d = tmp_path / "batch_bad"
    d.mkdir()
    (d / "manifest.json").write_text("{not json")
    (tmp_path / ".riceclipper_consumed.json").write_text("{}")
    assert home.ready_batches(tmp_path) == []
    assert home._clipper_consumed(tmp_path) == set()


def test_missing_handoff_dirs_are_empty(tmp_path):
    assert home.ready_batches(tmp_path / "absent") == []


def test_reading_changes_nothing(tmp_path):
    _batch(tmp_path, "batch_1")
    (tmp_path / ".riceclipper_consumed.json").write_text("[]")
    before = _snapshot(tmp_path)
    home.handoff_stages(
        {
            "RICESEARCHER_HANDOFF_DIR": str(tmp_path),
            "RICECLIPPER_HANDOFF_DIR": str(tmp_path),
        }
    )
    assert _snapshot(tmp_path) == before
