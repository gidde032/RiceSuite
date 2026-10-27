"""Manual/null-score schema-1 output remains valid for the real Clipper consumer."""

import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

from ricesearcher.config import Config
from ricesearcher.handoff.writer import hand_off_selected
from ricesearcher.library.store import Library
from ricesearcher.models import CandidateSlice, SliceStatus, Source, SourceKind

ROOT = Path(__file__).resolve().parents[1]


class Extractor:
    def extract(self, source, start, end, dest):
        Path(dest).write_bytes(Path(source).read_bytes())
        return end - start


def test_manual_and_scored_handoff_order_bytes_and_clipper_metadata(tmp_path):
    cfg = Config(tmp_path / "searcher", tmp_path / "handoff")
    cfg.ensure_dirs()
    media = cfg.cache_dir / "fixture.mp4"
    media.write_bytes(b"synthetic-media")
    first = CandidateSlice(
        id="manual:old",
        source_id="source",
        pad_in=0,
        pad_out=3,
        target_in=0,
        target_out=3,
        transcript_span="",
        profile_id="fixture",
        origin="manual",
        score=None,
        heuristic_score=None,
        status=SliceStatus.SELECTED,
        created_at="2026-10-22T00:00:00Z",
    )
    rows = [
        replace(first, id="scored:high", origin="scored", score=0.99),
        replace(first, id="manual:new", created_at="2026-10-22T00:00:01Z"),
        replace(first, id="scored:low", origin="scored", score=0.01),
        first,
    ]
    with Library(cfg.db_path) as lib:
        lib.upsert_source(Source("source", SourceKind.LOCAL, "fixture", str(media)))
        lib.upsert_slices(rows)
        result = hand_off_selected(
            lib,
            config=cfg,
            profile_id="fixture",
            extractor=Extractor(),
            duration_prober=lambda _: 12,
        )
        assert all(s.status is SliceStatus.HANDED_OFF for s in lib.list_slices())
    batch = cfg.handoff_dir / result["batch_id"]
    manifest = json.loads((batch / "manifest.json").read_text())
    assert [c["score"] for c in manifest["clips"]] == [None, None, 0.99, 0.01]
    assert all(
        c["origin"] == "manual" and c["scorer_model"] == ""
        for c in manifest["clips"][:2]
    )
    assert all("origin" not in c for c in manifest["clips"][2:])
    assert all(
        (batch / c["file"]).read_bytes() == media.read_bytes()
        for c in manifest["clips"]
    )
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(tmp_path / "home"),
        "POST_MODE": "mock",
        "RICECLIPPER_WORK_DIR": str(tmp_path / "clipper"),
    }
    code = """
import json, sys
from pathlib import Path
from app.searcher_pickup import _validated_clips
batch = Path(sys.argv[1]); manifest = json.loads((batch / 'manifest.json').read_text())
rows = _validated_clips(batch, manifest)
assert rows == manifest['clips']
assert rows[0]['score'] is None and rows[0]['origin'] == 'manual'
print(len(rows))
"""
    completed = subprocess.run(
        [sys.executable, "-c", code, str(batch)],
        cwd=ROOT / "clipper",
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    assert completed.stdout.strip() == "4"
