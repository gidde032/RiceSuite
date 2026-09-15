"""Fail-before-fix regressions for the PR #13 review findings (W1, W2, W3)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ricesearcher.config import Config
from ricesearcher.library.store import LEGACY_PROFILE_ID, Library
from ricesearcher.models import CandidateSlice, SliceStatus, Source, SourceKind
from ricesearcher.web.app import create_app


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    cfg = Config(data_dir=tmp_path / "d", handoff_dir=tmp_path / "h")
    cfg.ensure_dirs()
    with Library(cfg.db_path) as lib:
        lib.upsert_source(
            Source(
                id="src", kind=SourceKind.YOUTUBE, ref="r", media_path="/m", title="T"
            )
        )
        lib.upsert_slices(
            [
                CandidateSlice(
                    id="sl1",
                    source_id="src",
                    pad_in=0,
                    pad_out=100,
                    target_in=10,
                    target_out=40,
                    transcript_span="x",
                    score=0.8,
                    rationale="why",
                    dup_of="canon",
                    dup_score=0.7,
                    dup_kind="cross",
                    status=SliceStatus.CANDIDATE,
                    profile_id=LEGACY_PROFILE_ID,
                ),
            ]
        )
    return TestClient(create_app(cfg))


def _get(client: TestClient, sid: str) -> dict:
    url = f"/api/slices?profile={LEGACY_PROFILE_ID}"
    return {s["id"]: s for s in client.get(url).json()}[sid]


# -- W1: targeted updates don't clobber other fields --------------------------


def test_w1_status_change_preserves_window_and_dup(client: TestClient) -> None:
    assert (
        client.post("/api/slices/sl1/status", json={"status": "selected"}).status_code
        == 200
    )
    s = _get(client, "sl1")
    assert s["status"] == "selected"
    assert s["target_in"] == 10 and s["target_out"] == 40  # window untouched
    assert s["dup_of"] == "canon" and s["dup_score"] == 0.7  # dup annotation untouched
    assert s["rationale"] == "why" and s["score"] == 0.8


def test_w1_window_change_preserves_status_and_dup(client: TestClient) -> None:
    client.post("/api/slices/sl1/status", json={"status": "selected"})
    assert (
        client.patch(
            "/api/slices/sl1/window", json={"target_in": 12, "target_out": 30}
        ).status_code
        == 200
    )
    s = _get(client, "sl1")
    assert s["target_in"] == 12 and s["target_out"] == 30
    assert s["status"] == "selected"  # status untouched by the window edit
    assert s["dup_of"] == "canon"


# -- W2: the gate can't set handed_off ---------------------------------------


def test_w2_handed_off_rejected_from_gate(client: TestClient) -> None:
    r = client.post("/api/slices/sl1/status", json={"status": "handed_off"})
    assert r.status_code == 422
    # but the gate's own vocabulary works
    assert (
        client.post("/api/slices/sl1/status", json={"status": "reviewed"}).status_code
        == 200
    )


# -- W3: window validation rejects NaN/Inf and reversed input -----------------


def test_w3_nan_and_inf_rejected(client: TestClient) -> None:
    # Send raw JSON (the client refuses to serialize NaN) so the SERVER's
    # allow_inf_nan=False validation is exercised.
    hdr = {"Content-Type": "application/json"}
    r = client.patch(
        "/api/slices/sl1/window",
        content='{"target_in": NaN, "target_out": 20}',
        headers=hdr,
    )
    assert r.status_code == 422
    r = client.patch(
        "/api/slices/sl1/window",
        content='{"target_in": 5, "target_out": Infinity}',
        headers=hdr,
    )
    assert r.status_code == 422


def test_w3_reversed_input_rejected(client: TestClient) -> None:
    # {in:95, out:5} inside pad [0,100] must 422, not silently span the pad.
    r = client.patch("/api/slices/sl1/window", json={"target_in": 95, "target_out": 5})
    assert r.status_code == 422
    # unchanged
    assert _get(client, "sl1")["target_in"] == 10


def test_w3_valid_tighten_still_works(client: TestClient) -> None:
    r = client.patch("/api/slices/sl1/window", json={"target_in": 15, "target_out": 35})
    assert r.status_code == 200
    assert (r.json()["target_in"], r.json()["target_out"]) == (15, 35)
