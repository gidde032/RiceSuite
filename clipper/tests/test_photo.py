"""Still-photo support (Issue #54, SPEC.md D17)."""

from __future__ import annotations

import base64
import io
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi import HTTPException, UploadFile
from fastapi.testclient import TestClient
from PIL import Image, JpegImagePlugin
from pydantic import ValidationError
from starlette.datastructures import Headers

from app import jobs, main, photo
from app.models import LyricsRequest, MusicSettings, RenderRequest, Word
from render import pipeline


@pytest.fixture
def isolated_jobs(tmp_path, monkeypatch):
    root = tmp_path / ".riceclipper_work"
    root.mkdir()
    monkeypatch.setattr(jobs, "WORK_ROOT", root)
    previous = jobs._JOBS.copy()
    jobs._JOBS.clear()
    yield root
    jobs._JOBS.clear()
    jobs._JOBS.update(previous)


def _image_bytes(fmt="PNG", size=(40, 20), mode="RGB", color=(200, 10, 10), exif=None):
    buf = io.BytesIO()
    image = Image.new(mode, size, color)
    kwargs = {"exif": exif} if exif is not None else {}
    image.save(buf, format=fmt, **kwargs)
    return buf.getvalue()


def _upload(data: bytes, filename: str, content_type: str = "image/png"):
    return UploadFile(
        filename=filename,
        file=io.BytesIO(data),
        headers=Headers({"content-type": content_type}),
    )


def _photo_job(isolated_jobs, size=(40, 20)):
    return main.upload(_upload(_image_bytes(size=size), "quote.png"))


# --- classify ----------------------------------------------------------------


@pytest.mark.parametrize("name", ["a.png", "a.PNG", "a.jpg", "a.jpeg", "a.webp"])
def test_classify_accepts_the_three_photo_formats(name):
    assert photo.classify(name, "image/whatever") == "photo"


@pytest.mark.parametrize(
    ("name", "ctype"),
    [("a.heic", "image/heic"), ("a.gif", "image/gif"), ("a.bmp", "image/bmp")],
)
def test_classify_refuses_other_images(name, ctype):
    assert photo.classify(name, ctype) == "unsupported"


def test_classify_leaves_videos_on_the_video_path():
    assert photo.classify("clip.mp4", "video/mp4") == "video"
    assert photo.classify("clip.mov", None) == "video"


# --- normalize ---------------------------------------------------------------


def test_normalize_writes_an_upright_png_and_describes_it(tmp_path):
    raw = tmp_path / "in.jpg"
    raw.write_bytes(_image_bytes("JPEG", size=(41, 21)))
    dest = tmp_path / "source.png"

    info = photo.normalize(raw, dest)

    assert Image.open(dest).format == "PNG"
    assert (info.width, info.height) == (42, 22)
    assert info.still is True
    assert info.has_audio is False
    assert info.duration == photo.DEFAULT_SECONDS


def test_normalize_applies_exif_rotation(tmp_path):
    exif = Image.Exif()
    exif[0x0112] = 6  # Orientation: rotate 90 CW to display.
    raw = tmp_path / "phone.jpg"
    raw.write_bytes(_image_bytes("JPEG", size=(60, 20), exif=exif))
    dest = tmp_path / "source.png"

    info = photo.normalize(raw, dest)

    assert Image.open(dest).size == (20, 60)
    assert (info.width, info.height) == (20, 60)


def test_normalize_flattens_transparency_onto_black(tmp_path):
    raw = tmp_path / "clear.png"
    raw.write_bytes(_image_bytes(mode="RGBA", color=(255, 255, 255, 0)))
    dest = tmp_path / "source.png"

    photo.normalize(raw, dest)

    out = Image.open(dest)
    assert out.mode == "RGB"
    assert out.getpixel((0, 0)) == (0, 0, 0)


def test_normalize_bounds_the_long_edge(tmp_path, monkeypatch):
    monkeypatch.setattr(photo, "MAX_EDGE", 100)
    raw = tmp_path / "big.png"
    raw.write_bytes(_image_bytes(size=(400, 200)))

    info = photo.normalize(raw, tmp_path / "source.png")

    assert (info.width, info.height) == (100, 50)


def test_normalize_refuses_a_gif_with_a_photo_name(tmp_path):
    raw = tmp_path / "fake.png"
    raw.write_bytes(_image_bytes("GIF"))

    with pytest.raises(photo.UnsupportedPhotoError):
        photo.normalize(raw, tmp_path / "source.png")


def test_normalize_refuses_unreadable_bytes(tmp_path):
    raw = tmp_path / "broken.jpg"
    raw.write_bytes(b"not an image")

    with pytest.raises(photo.PhotoError):
        photo.normalize(raw, tmp_path / "source.png")


# --- upload ------------------------------------------------------------------


def test_upload_stores_a_photo_job(isolated_jobs):
    state = _photo_job(isolated_jobs)

    job = jobs.get_job(state.id)
    assert state.kind == "photo"
    assert state.status == "ready"
    assert state.has_audio is False
    assert job.source_path.name == photo.PHOTO_SOURCE_NAME
    assert sorted(p.name for p in job.dir.iterdir()) == [photo.PHOTO_SOURCE_NAME]


def test_upload_refuses_other_image_types_before_making_a_job(isolated_jobs):
    with pytest.raises(HTTPException) as exc_info:
        main.upload(_upload(b"heic", "phone.heic", "image/heic"))

    assert exc_info.value.status_code == 400
    assert "PNG, JPEG, or WebP" in exc_info.value.detail
    assert jobs._JOBS == {}


def test_upload_reports_a_disguised_image_as_unsupported(isolated_jobs):
    with pytest.raises(HTTPException) as exc_info:
        main.upload(_upload(_image_bytes("GIF"), "anim.png"))

    assert exc_info.value.status_code == 400
    assert "PNG, JPEG, or WebP" in exc_info.value.detail


def test_upload_reports_an_unreadable_photo(isolated_jobs):
    with pytest.raises(HTTPException) as exc_info:
        main.upload(_upload(b"junk", "quote.jpg", "image/jpeg"))

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "could not read image"


def test_upload_reports_an_unexpected_photo_failure(isolated_jobs, monkeypatch):
    def boom(raw, dest):
        raise RuntimeError("disk")

    monkeypatch.setattr(main.photo, "normalize", boom)
    with pytest.raises(HTTPException) as exc_info:
        main.upload(_upload(_image_bytes(), "quote.png"))

    assert exc_info.value.status_code == 500


def test_upload_route_accepts_a_photo_over_http(isolated_jobs):
    with TestClient(main.app, base_url="http://127.0.0.1:8000") as client:
        res = client.post(
            "/api/upload",
            files={"file": ("quote.webp", _image_bytes("WEBP"), "image/webp")},
        )

    assert res.status_code == 200
    assert res.json()["kind"] == "photo"


# --- guards ------------------------------------------------------------------


def test_photo_jobs_refuse_transcription_and_lyrics(isolated_jobs):
    state = _photo_job(isolated_jobs)

    with pytest.raises(HTTPException) as tr:
        main.transcribe_job(state.id)
    with pytest.raises(HTTPException) as ly:
        main.lyrics_job(state.id, LyricsRequest(lyrics="la la"))

    assert tr.value.status_code == 409
    assert ly.value.status_code == 409


def test_header_frame_for_a_photo_is_grabbed_at_zero(isolated_jobs, monkeypatch):
    state = _photo_job(isolated_jobs)
    seen = {}

    def grab(source, info, work_dir):
        seen["info"] = info
        return "ZmFrZQ=="

    monkeypatch.setattr(main.frame, "grab_frame_b64", grab)
    monkeypatch.setattr(main.header_gen, "generate_header", lambda t, **k: "Hook")

    result = main.generate_header(state.id, main.HeaderRequest())

    assert result == {"header": "Hook"}
    assert seen["info"] is None


# --- render ------------------------------------------------------------------


def test_photo_duration_is_bounded_to_whole_seconds():
    assert RenderRequest().photo_duration == 10
    assert RenderRequest(photo_duration=3).photo_duration == 3
    assert RenderRequest(photo_duration=60).photo_duration == 60
    for bad in (2, 61, 10.5):
        with pytest.raises(ValidationError):
            RenderRequest(photo_duration=bad)


def test_photo_render_uses_the_chosen_length_and_drops_captions(
    isolated_jobs, monkeypatch
):
    state = _photo_job(isolated_jobs)
    seen = {}

    def fake_render(work_dir, source, info, req, plan=None):
        seen.update(info=info, req=req, plan=plan)
        out = work_dir / "output.mp4"
        out.write_bytes(b"mp4")
        return out

    monkeypatch.setattr(main, "render", fake_render)
    req = RenderRequest(
        words=[Word(text="hi", start=0.0, end=0.5)],
        captions_on=True,
        geometry="crop",
        content="music",
        photo_duration=12,
        header="Hook",
    )

    result = main.render_job(state.id, req)

    assert result.status == "done"
    assert seen["info"].duration == 12.0
    assert seen["info"].still is True
    assert seen["req"].words == []
    assert seen["req"].captions_on is False
    assert seen["req"].header == "Hook"
    assert seen["plan"] is None


def test_ffmpeg_command_loops_a_still_input():
    cmd = pipeline._ffmpeg_command(
        photo_path := "source.png",
        None,
        False,
        "[0:v]null[vout]",
        None,
        7.0,
        still=True,
    )

    i = cmd.index(photo_path)
    assert cmd[i - 1] == "-i"
    assert cmd[1 : i - 1] == ["-y", "-loop", "1", "-framerate", "30"]
    assert cmd[cmd.index("-t") + 1] == "7.0"


def test_pipeline_blur_pads_a_photo_to_the_chosen_length(isolated_jobs, monkeypatch):
    state = _photo_job(isolated_jobs, size=(400, 200))
    job = jobs.get_job(state.id)
    calls = []

    class Done:
        returncode = 0
        stderr = ""

    monkeypatch.setattr(
        pipeline, "run_owned", lambda cmd, **kw: calls.append(cmd) or Done()
    )
    music = job.dir / "music.mp3"
    music.write_bytes(b"m")
    info = main.replace(job.info, duration=9.0)
    req = RenderRequest(
        captions_on=False,
        music=MusicSettings(mode="replace", filename="music.mp3"),
    )

    pipeline.render(job.dir, job.source_path, info, req)

    cmd = calls[0]
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert "-loop" in cmd
    assert "boxblur" in graph
    assert "[1:a]volume=0.35,apad,atrim=duration=9.0[aout]" in graph
    assert cmd[cmd.index("-t") + 1] == "9.0"


def test_a_photo_and_a_video_share_one_handoff_batch(
    isolated_jobs, tmp_path, monkeypatch
):
    handoff_root = tmp_path / "handoff"
    monkeypatch.setenv("RICECLIPPER_HANDOFF_DIR", str(handoff_root))
    rendered = []
    for kind in ("video", "photo"):
        job = jobs.create_job()
        job.kind = kind
        job.status = "done"
        job.output_path = job.dir / "output.mp4"
        job.output_path.write_bytes(kind.encode())
        rendered.append(job)

    with TestClient(main.app, base_url="http://127.0.0.1:8000") as client:
        res = client.post(
            "/api/handoff",
            json={
                "clips": [
                    {"job_id": rendered[0].id, "position": 1, "transcript": "hi"},
                    {"job_id": rendered[1].id, "position": 2, "transcript": ""},
                ]
            },
        )

    assert res.status_code == 200
    batch = handoff_root / res.json()["batch_id"]
    manifest = json.loads((batch / "manifest.json").read_text())
    assert manifest["schema_version"] == 1
    assert [c["file"] for c in manifest["clips"]] == ["clip_1.mp4", "clip_2.mp4"]
    assert manifest["clips"][1]["transcript"] == ""
    assert (batch / "clip_2.mp4").read_bytes() == b"photo"


# --- review repairs (PR #57) -------------------------------------------------


def test_classify_trusts_a_photo_type_without_a_photo_name():
    assert photo.classify("IMG", "image/jpeg") == "photo"
    assert photo.classify("a.jfif", "image/jpeg") == "photo"
    assert photo.classify("a.gif", "image/gif") == "unsupported"


def test_normalize_scales_16_bit_grayscale_instead_of_clipping(tmp_path):
    raw = tmp_path / "deep.png"
    Image.new("I;16", (8, 8), 32768).save(raw)
    dest = tmp_path / "source.png"

    photo.normalize(raw, dest)

    assert Image.open(dest).getpixel((0, 0)) == (128, 128, 128)


def test_every_alpha_mode_is_flattened_onto_black():
    for image in (
        Image.new("PA", (2, 2), (0, 0)),
        Image.new("RGBa", (2, 2), (255, 255, 255, 0)),
        Image.new("LA", (2, 2), (255, 0)),
    ):
        image.putpalette([255, 255, 255] * 256) if image.mode == "PA" else None
        assert photo._opaque(image).getpixel((0, 0)) == (0, 0, 0), image.mode


def test_normalize_refuses_an_image_over_the_pixel_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(photo, "MAX_PIXELS", 100)
    raw = tmp_path / "huge.png"
    raw.write_bytes(_image_bytes(size=(20, 20)))

    with pytest.raises(photo.PhotoError) as exc_info:
        photo.normalize(raw, tmp_path / "source.png")

    assert "too large" in exc_info.value.detail


def test_normalize_draft_decodes_a_jpeg_near_the_size_cap(tmp_path, monkeypatch):
    seen = []
    real_draft = JpegImagePlugin.JpegImageFile.draft

    def spy(self, mode, size):
        seen.append(size)
        return real_draft(self, mode, size)

    monkeypatch.setattr(JpegImagePlugin.JpegImageFile, "draft", spy)
    raw = tmp_path / "big.jpg"
    raw.write_bytes(_image_bytes("JPEG", size=(64, 48)))

    photo.normalize(raw, tmp_path / "source.png")

    assert seen == [(photo.MAX_EDGE, photo.MAX_EDGE)]


def test_upload_reports_an_oversize_photo(isolated_jobs, monkeypatch):
    monkeypatch.setattr(photo, "MAX_PIXELS", 100)
    with pytest.raises(HTTPException) as exc_info:
        main.upload(_upload(_image_bytes(size=(20, 20)), "quote.png"))

    assert exc_info.value.status_code == 400
    assert "too large" in exc_info.value.detail


def test_photo_length_field_is_hidden_on_video_cards():
    css = (Path(__file__).resolve().parents[1] / "web" / "style.css").read_text()

    # `.field-row` sets display: grid with the same specificity, so the hide
    # rule must outrank it or every video card shows a Length field.
    assert re.search(r"\.field-row\.photo-length\s*{[^}]*display:\s*none", css)
    shown = re.search(
        r"\.photo-card \.field-row\.photo-length\s*{[^}]*display:\s*grid", css
    )
    assert shown


def test_photo_cards_hide_every_caption_control():
    css = (Path(__file__).resolve().parents[1] / "web" / "style.css").read_text()
    rule = re.search(r"([^{}]*\.photo-card \.text-row[^{}]*){([^}]*)}", css)

    assert rule and "display: none" in rule.group(2)
    for name in ("content", "geometry", "captions-row", "caption-style", "text-row"):
        assert f".photo-card .{name}" in rule.group(1), name


def test_photo_length_input_sits_inside_the_card_template():
    html = (Path(__file__).resolve().parents[1] / "web" / "index.html").read_text()
    template = html.split('<template id="clip-card-template">', 1)[1]
    template = template.split("</template>", 1)[0]

    # The card listens for input/change on itself, so a Length edit marks a
    # finished render stale only while the field is inside the card.
    assert 'class="photo-length-input"' in template


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is unavailable")
def test_a_real_photo_render_is_1080x1920_at_the_chosen_length(isolated_jobs):
    state = main.upload(_upload(_image_bytes("JPEG", size=(1200, 800)), "q.jpg"))

    main.render_job(state.id, RenderRequest(header="Hook", photo_duration=4))

    out = jobs.get_job(state.id).output_path
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height:format=duration",
            "-of",
            "json",
            str(out),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(probe.stdout)
    assert (data["streams"][0]["width"], data["streams"][0]["height"]) == (1080, 1920)
    assert abs(float(data["format"]["duration"]) - 4.0) < 0.1


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is unavailable")
def test_a_real_header_frame_is_grabbed_from_a_photo(isolated_jobs):
    state = _photo_job(isolated_jobs)
    job = jobs.get_job(state.id)

    data = main.frame.grab_frame_b64(job.source_path, None, job.dir)

    assert base64.b64decode(data)[:2] == b"\xff\xd8"
