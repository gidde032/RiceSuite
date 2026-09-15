"""Review UI API (D7, FR-8) — the select-and-approve gate over HTTP."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ricesearcher.config import Config
from ricesearcher.library.store import LEGACY_PROFILE_ID, Library
from ricesearcher.models import CandidateSlice, SliceStatus, Source, SourceKind
from ricesearcher.web.app import create_app

# Every fixture slice lives in the legacy profile; the API now requires it.
SLICES = f"/api/slices?profile={LEGACY_PROFILE_ID}"


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
                    profile_id=LEGACY_PROFILE_ID,
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
                    profile_id=LEGACY_PROFILE_ID,
                ),
            ]
        )
    return TestClient(create_app(cfg))


def test_index_serves_html(client: TestClient) -> None:
    r = client.get("/")
    assert r.status_code == 200
    assert "RiceSearcher" in r.text
    assert "/static/app.js" in r.text


def test_selected_logo_asset_is_served_and_used(client: TestClient) -> None:
    for path in ("/", "/media"):
        html = client.get(path)
        assert html.status_code == 200
        assert "/static/mark.png" in html.text
        assert "/static/mark.svg" not in html.text

    asset = client.get("/static/mark.png")
    assert asset.status_code == 200
    assert asset.headers["content-type"].startswith("image/png")
    assert asset.content.startswith(b"\x89PNG\r\n\x1a\n")


def test_list_slices_dto(client: TestClient) -> None:
    r = client.get(SLICES)
    assert r.status_code == 200
    data = {s["id"]: s for s in r.json()}
    s = data["sl1"]
    assert s["source_title"] == "Person A interview"
    assert s["media_url"] == "/cache/ab/abc123.mp4"
    assert s["score"] == 0.8 and s["rationale"] == "chemistry + quotable"
    assert s["target_in"] == 10 and s["pad_out"] == 42
    assert s["status"] == "candidate"


def test_list_filter_by_status(client: TestClient) -> None:
    assert client.get(SLICES + "&status=selected").json() == []
    assert client.get(SLICES + "&status=candidate").status_code == 200
    assert client.get(SLICES + "&status=bogus").status_code == 422


def test_list_slices_requires_profile(client: TestClient) -> None:
    # The profile bridge is gone; a slices request without `profile` is a 400.
    r = client.get("/api/slices")
    assert r.status_code == 400
    assert "profile" in r.json()["detail"]


def test_list_profiles_api(client: TestClient) -> None:
    rows = {p["id"]: p for p in client.get("/api/profiles").json()}
    prof = rows[LEGACY_PROFILE_ID]
    assert prof["name"] == LEGACY_PROFILE_ID
    assert prof["version"]  # non-empty, from the seeded file
    assert prof["sources"] == 1  # one source backs the fixture slices
    assert prof["candidates"] == 2  # sl1 + sl2 both candidate
    assert prof["selected"] == 0 and prof["handed_off"] == 0


def test_slice_stale_flag_tracks_the_file_version(client: TestClient) -> None:
    # The fixture slices carry no stored version, so they differ from the file.
    stale = {s["id"]: s["stale"] for s in client.get(SLICES).json()}
    assert stale["sl1"] is True and stale["sl2"] is True


def test_slice_fresh_when_version_matches_file(tmp_path: Path) -> None:
    # A slice stamped with the current file version is not stale.
    cfg = Config(data_dir=tmp_path / "data", handoff_dir=tmp_path / "handoff")
    cfg.ensure_dirs()
    client = TestClient(create_app(cfg))  # seeds example-beat.json
    file_version = client.get("/api/profiles").json()[0]["version"]
    with Library(cfg.db_path) as lib:
        lib.upsert_source(
            Source(
                id="src1",
                kind=SourceKind.YOUTUBE,
                ref="https://y/x",
                media_path="/m.mp4",
            )
        )
        lib.upsert_slices(
            [
                CandidateSlice(
                    id="fresh",
                    source_id="src1",
                    pad_in=0,
                    pad_out=10,
                    target_in=2,
                    target_out=8,
                    transcript_span="a moment",
                    status=SliceStatus.CANDIDATE,
                    profile_id=LEGACY_PROFILE_ID,
                    beat_profile_version=file_version,
                )
            ]
        )
    rows = {s["id"]: s for s in client.get(SLICES).json()}
    assert rows["fresh"]["stale"] is False
    # FC-1 (PR #26 review): when the profile file disappears, the version can no
    # longer be confirmed, so the same slice reads stale instead of erroring.
    (cfg.profiles_dir / f"{LEGACY_PROFILE_ID}.json").unlink()
    rows = {s["id"]: s for s in client.get(SLICES).json()}
    assert rows["fresh"]["stale"] is True


@pytest.mark.parametrize("bad", ["../x", "UPPER", "a" * 41, "-lead", "sp ace"])
def test_invalid_profile_id_is_400_on_slices_and_handoff(
    client: TestClient, bad: str
) -> None:
    # FA-1 (PR #26 review): an id that fails the profile-id rule is a client
    # error, not an empty 200 that hides a typo in the UI's stored choice.
    r = client.get("/api/slices", params={"profile": bad})
    assert r.status_code == 400, r.text
    assert "profile" in r.json()["detail"]
    r = client.post("/api/handoff", json={"profile": bad})
    assert r.status_code == 400, r.text
    assert "profile" in r.json()["detail"]


def test_media_is_served_with_range(client: TestClient) -> None:
    # StaticFiles honours Range so the <video> element can seek.
    r = client.get("/cache/ab/abc123.mp4", headers={"Range": "bytes=0-3"})
    assert r.status_code == 206
    assert r.headers["content-range"].startswith("bytes 0-3/")


def test_set_status_is_the_gate(client: TestClient) -> None:
    r = client.post("/api/slices/sl1/status", json={"status": "selected"})
    assert r.status_code == 200 and r.json()["status"] == "selected"
    got = {s["id"]: s for s in client.get(SLICES).json()}
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


def test_handed_off_slice_is_terminal_in_review_api(tmp_path: Path) -> None:
    cfg = Config(data_dir=tmp_path / "data", handoff_dir=tmp_path / "handoff")
    cfg.ensure_dirs()
    with Library(cfg.db_path) as lib:
        lib.upsert_source(
            Source(
                id="src1",
                kind=SourceKind.YOUTUBE,
                ref="https://y/x",
                media_path="/media.mp4",
            )
        )
        lib.upsert_slices(
            [
                CandidateSlice(
                    id="sl1",
                    source_id="src1",
                    pad_in=0,
                    pad_out=10,
                    target_in=2,
                    target_out=8,
                    transcript_span="a moment",
                    status=SliceStatus.HANDED_OFF,
                    profile_id=LEGACY_PROFILE_ID,
                )
            ]
        )

    client = TestClient(create_app(cfg))
    response = client.post(
        "/api/slices/sl1/status", json={"status": SliceStatus.SELECTED.value}
    )

    assert response.status_code == 409
    assert "terminal" in response.json()["detail"]
    with Library(cfg.db_path) as lib:
        assert lib.get_slice("sl1").status is SliceStatus.HANDED_OFF


def test_handoff_execution_failure_is_structured_and_retryable(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import ricesearcher.web.app as web_app

    assert (
        client.post("/api/slices/sl1/status", json={"status": "selected"}).status_code
        == 200
    )

    def fail(*args: object, **kwargs: object) -> dict:
        raise OSError("ffmpeg: encoder unavailable")

    monkeypatch.setattr(web_app, "hand_off_selected", fail)
    response = client.post("/api/handoff", json={"profile": LEGACY_PROFILE_ID})

    assert response.status_code == 503
    assert "retry" in response.json()["detail"]
    assert "ffmpeg" in response.json()["detail"]
    got = {s["id"]: s for s in client.get(SLICES).json()}
    assert got["sl1"]["status"] == SliceStatus.SELECTED.value


def test_handoff_requires_profile(client: TestClient) -> None:
    # No body → 400; the legacy fallback is gone.
    r = client.post("/api/handoff")
    assert r.status_code == 400
    assert "profile" in r.json()["detail"]


def test_static_handoff_refresh_preserves_success_message(client: TestClient) -> None:
    script = client.get("/static/app.js")
    assert script.status_code == 200
    assert "await load(true)" in script.text
    assert "if (!preserveStatus)" in script.text


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
    # The app exposes only review + local-media endpoints — no post/publish/upload.
    cfg = Config(data_dir=tmp_path / "d", handoff_dir=tmp_path / "h")
    cfg.ensure_dirs()
    app = create_app(cfg)
    paths = {getattr(r, "path", "") for r in app.routes}
    assert not any(
        kw in p.lower() for p in paths for kw in ("publish", "upload", "/post")
    )
    assert "/api/slices" in paths
    assert "/api/sources" in paths  # media-management routes must not be a surface


def test_profiles_page_serves_html(client: TestClient) -> None:
    r = client.get("/profiles")
    assert r.status_code == 200
    assert "RiceSearcher" in r.text
    assert "/static/profiles.js" in r.text


def test_profiles_and_review_scripts_share_the_storage_key(client: TestClient) -> None:
    # FC-2 (PR #26 review): both pages scope by the same localStorage key, and
    # a profiles-page row click writes it before it navigates to the review page.
    profiles_js = client.get("/static/profiles.js").text
    app_js = client.get("/static/app.js").text
    key = 'PROFILE_KEY = "ricesearcher.profile"'
    assert key in profiles_js and key in app_js
    assert profiles_js.index("localStorage.setItem(PROFILE_KEY") < profiles_js.index(
        'window.location.assign("/")'
    )


def test_media_page_serves_html(client: TestClient) -> None:
    r = client.get("/media")
    assert r.status_code == 200
    assert "RiceSearcher" in r.text
    assert "/static/media.js" in r.text


def test_list_sources_dto(client: TestClient) -> None:
    r = client.get("/api/sources")
    assert r.status_code == 200
    rows = {s["id"]: s for s in r.json()}
    src = rows["src1"]
    assert src["ref"] == "https://y/x"
    assert src["title"] == "Person A interview"
    assert src["media_url"] == "/cache/ab/abc123.mp4"
    assert src["size_bytes"] == len(b"\x00fake video bytes\x01")
    assert src["slice_count"] == 2  # sl1 + sl2
    assert "media_path" not in src  # absolute local path is not surfaced (skeptic #3)


def test_list_sources_reports_missing_media(client: TestClient, tmp_path: Path) -> None:
    # A source whose cached media has been unlinked stays listed (size None) and
    # remains deletable — the media page shows "media missing" (frontend #2).
    media = tmp_path / "data" / "cache" / "ab" / "abc123.mp4"
    media.unlink()
    rows = {s["id"]: s for s in client.get("/api/sources").json()}
    assert rows["src1"]["size_bytes"] is None
    assert client.post("/api/sources/src1/delete").status_code == 200


def test_delete_source_survives_cache_unlink_oserror(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Regression (F4): the DB row is purged before the media unlink; if the
    # unlink hits a real OSError the endpoint must still report 200 honestly
    # (media_removed False), not 500 while the row is already gone.
    import ricesearcher.web.app as web_app

    def boom(self: object, path: object) -> bool:
        raise OSError("read-only filesystem")

    monkeypatch.setattr(web_app.MediaCache, "delete", boom)
    r = client.post("/api/sources/src1/delete")
    assert r.status_code == 200
    assert r.json()["media_removed"] is False
    assert client.get("/api/sources").json() == []  # row still purged


def test_delete_source_full_purge(client: TestClient, tmp_path: Path) -> None:
    # Delete removes the source row, its slices, AND the on-disk media file.
    media = tmp_path / "data" / "cache" / "ab" / "abc123.mp4"
    assert media.is_file()
    r = client.post("/api/sources/src1/delete")
    assert r.status_code == 200
    body = r.json()
    assert body["deleted"] is True and body["media_removed"] is True
    assert not media.exists()
    assert client.get("/api/sources").json() == []
    assert client.get(SLICES).json() == []  # slices cascaded away
    # deleting a missing source is a clean 404
    assert client.post("/api/sources/ghost/delete").status_code == 404


def test_delete_keeps_media_file_shared_by_another_source(tmp_path: Path) -> None:
    # Content-addressed cache: two sources can share one file. Deleting one must
    # not unlink a file the other still references.
    cfg = Config(data_dir=tmp_path / "data", handoff_dir=tmp_path / "handoff")
    cfg.ensure_dirs()
    media = cfg.cache_dir / "cd" / "shared.mp4"
    media.parent.mkdir(parents=True, exist_ok=True)
    media.write_bytes(b"shared bytes")
    with Library(cfg.db_path) as lib:
        for sid in ("srcA", "srcB"):
            lib.upsert_source(
                Source(
                    id=sid,
                    kind=SourceKind.YOUTUBE,
                    ref=f"https://y/{sid}",
                    media_path=str(media),
                )
            )
    client = TestClient(create_app(cfg))

    first = client.post("/api/sources/srcA/delete").json()
    assert first["media_removed"] is False  # srcB still references it
    assert media.is_file()

    second = client.post("/api/sources/srcB/delete").json()
    assert second["media_removed"] is True  # last reference gone
    assert not media.exists()


def test_clear_cache_purges_everything(client: TestClient, tmp_path: Path) -> None:
    cache_dir = tmp_path / "data" / "cache"
    assert any(cache_dir.rglob("*.mp4"))
    r = client.post("/api/cache/clear")
    assert r.status_code == 200
    body = r.json()
    assert body["sources_deleted"] == 1 and body["files_removed"] >= 1
    assert client.get("/api/sources").json() == []
    assert client.get(SLICES).json() == []
    assert not any(cache_dir.rglob("*.mp4"))  # disk wiped
    assert cache_dir.is_dir()  # ...but the cache root is recreated, still usable
