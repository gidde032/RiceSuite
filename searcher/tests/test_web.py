"""Review UI API (D7, FR-8) — the select-and-approve gate over HTTP."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ricesearcher.config import Config
from ricesearcher.library.store import Library
from ricesearcher.models import CandidateSlice, SliceStatus, Source, SourceKind
from ricesearcher.web.app import create_app


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    data = tmp_path / "data"
    cfg = Config(data_dir=data, handoff_dir=tmp_path / "handoff")
    cfg.ensure_dirs()
    # A real media file in the content-addressed cache, referenced by the source.
    media = cfg.cache_dir / "ab" / "abc123.mp4"
    media.parent.mkdir(parents=True, exist_ok=True)
    media.write_bytes(b"\x00fake video bytes\x01")
    with Library(cfg.db_path) as lib:
        lib.upsert_source(
            Source(
                id="src1",
                kind=SourceKind.YOUTUBE,
                ref="https://y/x",
                media_path=str(media),
                title="Person A interview",
            )
        )
        lib.upsert_slices(
            [
                CandidateSlice(
                    id="sl1",
                    source_id="src1",
                    pad_in=8,
                    pad_out=42,
                    target_in=10,
                    target_out=40,
                    transcript_span="a funny moment",
                    score=0.8,
                    rationale="chemistry + quotable",
                    heuristic_score=0.6,
                    rights_risk="med",
                    status=SliceStatus.CANDIDATE,
                ),
                CandidateSlice(
                    id="sl2",
                    source_id="src1",
                    pad_in=50,
                    pad_out=80,
                    target_in=52,
                    target_out=78,
                    transcript_span="filler",
                    score=0.2,
                    status=SliceStatus.CANDIDATE,
                ),
            ]
        )
    return TestClient(create_app(cfg))


def test_index_serves_html(client: TestClient) -> None:
    r = client.get("/")
    assert r.status_code == 200
    assert "RiceSearcher" in r.text
    assert "/static/app.js" in r.text


def test_list_slices_dto(client: TestClient) -> None:
    r = client.get("/api/slices")
    assert r.status_code == 200
    data = {s["id"]: s for s in r.json()}
    s = data["sl1"]
    assert s["source_title"] == "Person A interview"
    assert s["media_url"] == "/cache/ab/abc123.mp4"
    assert s["score"] == 0.8 and s["rationale"] == "chemistry + quotable"
    assert s["target_in"] == 10 and s["pad_out"] == 42
    assert s["status"] == "candidate"


def test_list_filter_by_status(client: TestClient) -> None:
    assert client.get("/api/slices?status=selected").json() == []
    assert client.get("/api/slices?status=candidate").status_code == 200
    assert client.get("/api/slices?status=bogus").status_code == 422


def test_media_is_served_with_range(client: TestClient) -> None:
    # StaticFiles honours Range so the <video> element can seek.
    r = client.get("/cache/ab/abc123.mp4", headers={"Range": "bytes=0-3"})
    assert r.status_code == 206
    assert r.headers["content-range"].startswith("bytes 0-3/")


def test_set_status_is_the_gate(client: TestClient) -> None:
    r = client.post("/api/slices/sl1/status", json={"status": "selected"})
    assert r.status_code == 200 and r.json()["status"] == "selected"
    got = {s["id"]: s for s in client.get("/api/slices").json()}
    assert got["sl1"]["status"] == "selected"
    # invalid + missing
    assert (
        client.post("/api/slices/sl1/status", json={"status": "nope"}).status_code
        == 422
    )
    assert (
        client.post("/api/slices/ghost/status", json={"status": "selected"}).status_code
        == 404
    )


def test_window_tighten_clamps_to_pad(client: TestClient) -> None:
    # Ask for an out beyond the pad and an in below it → clamped to [pad_in, pad_out].
    r = client.patch("/api/slices/sl1/window", json={"target_in": 0, "target_out": 999})
    assert r.status_code == 200
    body = r.json()
    assert body["target_in"] == 8 and body["target_out"] == 42
    # inverted / empty window rejected
    assert (
        client.patch(
            "/api/slices/sl1/window", json={"target_in": 20, "target_out": 20}
        ).status_code
        == 422
    )
    assert (
        client.patch(
            "/api/slices/ghost/window", json={"target_in": 1, "target_out": 2}
        ).status_code
        == 404
    )


def test_no_posting_surface(tmp_path: Path) -> None:
    # The app exposes only review endpoints — no post/publish/upload route.
    cfg = Config(data_dir=tmp_path / "d", handoff_dir=tmp_path / "h")
    cfg.ensure_dirs()
    app = create_app(cfg)
    paths = {getattr(r, "path", "") for r in app.routes}
    assert not any(
        kw in p.lower() for p in paths for kw in ("publish", "upload", "/post")
    )
    assert "/api/slices" in paths
