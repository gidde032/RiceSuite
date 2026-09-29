"""GET /api/media-stat: the identity check behind Restore last batch (RiceSuite #30).

Restore puts a saved draft's caption back beside its staged media only when
that exact file is still in MEDIA_DIR. A name alone is not enough: upload
names (`{account}_{name}`) are reused after Clear media, so a new upload could
otherwise be paired with an old caption. The endpoint is read-only and never
resolves a name outside MEDIA_DIR.
"""

import os


def test_reports_size_and_mtime_for_staged_files_and_null_for_missing(client, tmp_media):
    staged = tmp_media / "creator-one_clip.mp4"
    staged.write_bytes(b"x" * 7)
    r = client.get("/api/media-stat", params=[("name", staged.name), ("name", "gone.mp4")])
    assert r.status_code == 200
    files = r.json()["files"]
    assert files[staged.name] == {"size": 7, "mtime_ns": staged.stat().st_mtime_ns}
    assert files["gone.mp4"] is None


def test_a_replaced_file_under_the_same_name_reports_a_different_identity(client, tmp_media):
    staged = tmp_media / "creator-one_clip.mp4"
    staged.write_bytes(b"first")
    before = client.get("/api/media-stat", params={"name": staged.name}).json()["files"][staged.name]
    staged.unlink()  # Clear media
    staged.write_bytes(b"second upload")  # a new upload reuses the name
    os.utime(staged, ns=(before["mtime_ns"] + 1_000_000, before["mtime_ns"] + 1_000_000))
    after = client.get("/api/media-stat", params={"name": staged.name}).json()["files"][staged.name]
    assert after != before


def test_never_resolves_a_name_outside_the_media_dir(client, tmp_media, tmp_path):
    (tmp_path / "secret.txt").write_text("outside")
    (tmp_media / "sub").mkdir()
    (tmp_media / "sub" / "nested.mp4").write_bytes(b"x")
    names = ["../secret.txt", "sub/nested.mp4", "sub", "", ".gitkeep"]
    (tmp_media / ".gitkeep").write_text("")
    r = client.get("/api/media-stat", params=[("name", n) for n in names])
    assert r.status_code == 200
    assert all(v is None for v in r.json()["files"].values())


def test_is_read_only(client, tmp_media):
    staged = tmp_media / "creator-one_clip.mp4"
    staged.write_bytes(b"x")
    before = sorted((p.name, p.stat().st_mtime_ns) for p in tmp_media.iterdir())
    client.get("/api/media-stat", params={"name": staged.name})
    assert sorted((p.name, p.stat().st_mtime_ns) for p in tmp_media.iterdir()) == before


def test_caps_the_number_of_names(client, tmp_media):
    r = client.get("/api/media-stat", params=[("name", f"f{i}.mp4") for i in range(101)])
    assert r.status_code == 400
