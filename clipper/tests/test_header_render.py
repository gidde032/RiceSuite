"""RiceSuite #65: one Pillow header renderer, header controls, and a live preview.

Every non-empty header is drawn by Pillow and composited with ``overlay``,
with or without emoji. libass draws captions only, plus a minimal text header
when the PNG render fails. The header controls ride on ``RenderRequest``; the
preview endpoint returns the exact PNG the render burns in; and the "face near
header" warning follows the drawn header.
"""

from __future__ import annotations

import base64
import io
import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageChops
from pydantic import ValidationError

from app import jobs, main
from app.models import CropPlan, CropSample, RenderRequest, TrackSample
from app.probe import MediaInfo
from render import framing, pipeline
from render.ass import build_ass

VERTICAL = MediaInfo(width=1080, height=1920, duration=2.0, has_audio=False)


def _text_font_or_skip():
    from render import text_image

    text_image.clear_font_caches()
    if text_image._resolve_text_font() is None:
        pytest.skip("no Pillow-compatible text font on this host")


@pytest.fixture
def fake_emoji(monkeypatch):
    """Draw each emoji cluster as a solid red square (CI has no emoji font)."""
    from render import text_image

    def square(_cluster, height):
        return Image.new("RGBA", (height, height), (255, 0, 0, 255))

    monkeypatch.setattr(text_image, "emoji_image", square)


@pytest.fixture
def captured(monkeypatch):
    seen: dict = {}

    class Completed:
        returncode = 0
        stderr = ""

    def fake_run(cmd, *args, **kwargs):
        seen["cmd"] = cmd
        return Completed()

    monkeypatch.setattr(pipeline, "run_owned", fake_run)
    return seen


def _filter(cmd):
    return cmd[cmd.index("-filter_complex") + 1]


def _inputs(cmd):
    return [cmd[i + 1] for i, arg in enumerate(cmd) if arg == "-i"]


# --- one renderer -------------------------------------------------------------


def test_text_only_header_is_drawn_by_pillow_and_overlaid(tmp_path, captured):
    _text_font_or_skip()
    pipeline.render(
        tmp_path, tmp_path / "src.mp4", VERTICAL, RenderRequest(header="Plain hook")
    )

    assert (tmp_path / "header.png").exists()
    assert _inputs(captured["cmd"])[-1] == "header.png"
    assert "[subbed][1:v]overlay=0:0[vout]" in _filter(captured["cmd"])
    ass = (tmp_path / "captions.ass").read_text()
    assert "Plain hook" not in ass
    assert "Style: Header" not in ass


def test_emoji_and_text_headers_take_the_same_path(
    tmp_path, captured, fake_emoji, monkeypatch
):
    _text_font_or_skip()
    calls = []
    real = pipeline.render_header_png

    def spy(text, *args, **kwargs):
        calls.append(text)
        return real(text, *args, **kwargs)

    monkeypatch.setattr(pipeline, "render_header_png", spy)
    for header in ("Plain hook", "Hook \U0001f602"):
        job = tmp_path / str(len(calls))
        job.mkdir()
        pipeline.render(
            job, tmp_path / "src.mp4", VERTICAL, RenderRequest(header=header)
        )
        assert "overlay=0:0" in _filter(captured["cmd"])

    assert calls == ["Plain hook", "Hook \U0001f602"]


def test_build_ass_draws_no_header_unless_asked_for_the_fallback():
    ass = build_ass([], duration=1.0)
    assert "Style: Header" not in ass
    assert "HeaderPlate" not in ass


def test_overlay_input_index_follows_music(tmp_path, captured):
    _text_font_or_skip()
    (tmp_path / "music.m4a").write_bytes(b"m")
    req = RenderRequest(
        header="Hook",
        music={"mode": "mix", "filename": "music.m4a"},
    )
    pipeline.render(tmp_path, tmp_path / "src.mp4", VERTICAL, req)

    assert _inputs(captured["cmd"]) == [
        str(tmp_path / "src.mp4"),
        str(tmp_path / "music.m4a"),
        "header.png",
    ]
    assert "[subbed][2:v]overlay=0:0[vout]" in _filter(captured["cmd"])


def test_empty_header_adds_no_overlay(tmp_path, captured):
    pipeline.render(
        tmp_path, tmp_path / "src.mp4", VERTICAL, RenderRequest(header="  ")
    )

    assert "header.png" not in _inputs(captured["cmd"])
    assert "overlay" not in _filter(captured["cmd"])


# --- fallback -----------------------------------------------------------------


def test_png_failure_falls_back_to_a_libass_text_header(
    tmp_path, captured, monkeypatch
):
    def broken(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(pipeline, "render_header_png", broken)
    notes: list[str] = []
    pipeline.render(
        tmp_path,
        tmp_path / "src.mp4",
        VERTICAL,
        RenderRequest(header="Still here"),
        notes=notes,
    )

    ass = (tmp_path / "captions.ass").read_text()
    assert "Style: HeaderFallback," in ass
    assert ",HeaderFallback,,0,0,0,,Still here" in ass
    assert "overlay" not in _filter(captured["cmd"])
    assert notes and "disk full" in notes[0]


def test_render_job_reports_the_header_fallback(monkeypatch, isolated_jobs):
    job = _ready_job()

    def fake_render(work_dir, *_args, notes=None, **_kwargs):
        notes.append("header drawn by the basic renderer: no font")
        out = work_dir / "output.mp4"
        out.write_bytes(b"mp4")
        return out

    monkeypatch.setattr(main, "render", fake_render)
    state = main.render_job(job.id, RenderRequest(header="Hook"))

    assert state.status == "done"
    assert state.header_note == "header drawn by the basic renderer: no font"


# --- request contract ---------------------------------------------------------


@pytest.mark.parametrize(
    "look",
    [
        {"y": -1},
        {"y": 5000},
        {"size": 8},
        {"size": 400},
        {"color": "red"},
        {"outline": 40},
        {"plate": "neon"},
        {"plate_opacity": 101},
        {"plate_radius": 999},
        {"plate_padding": -2},
        {"align": "justify"},
        {"line_spacing": 9},
        {"line_spacing": float("nan")},
        {"font": "comic_sans"},
        {"unknown": 1},
    ],
)
def test_header_look_bounds_are_enforced(look):
    with pytest.raises(ValidationError):
        RenderRequest(header_look=look)


def test_header_text_length_is_bounded():
    RenderRequest(header="x" * 200)
    with pytest.raises(ValidationError):
        RenderRequest(header="x" * 201)


def test_default_header_look_is_the_plain_preset_at_the_issue_20_position():
    from app.models import HeaderLook
    from render.ass import StyleConfig, apply_header_look, style_for_presets

    look = HeaderLook()
    assert look.y == 210
    assert apply_header_look(StyleConfig(), look) == style_for_presets(
        "classic", "plain"
    )


def test_render_uses_the_requested_header_look(tmp_path, captured, monkeypatch):
    seen = []
    monkeypatch.setattr(
        pipeline, "render_header_png", lambda _t, _p, style, **_k: seen.append(style)
    )
    req = RenderRequest(
        header="Hook",
        header_style="black_plate",
        header_look={"y": 600, "size": 60, "color": "ffcc00", "plate": "solid"},
    )
    pipeline.render(tmp_path, tmp_path / "src.mp4", VERTICAL, req)

    style = seen[0]
    assert (style.header_margin_v, style.header_font_size) == (600, 60)
    assert style.header_color == "FFCC00"
    assert style.header_plate == "solid"


# --- drawn pixels -------------------------------------------------------------


def _alpha_bbox(img):
    return img.getchannel("A").getbbox()


def test_default_header_top_is_at_the_issue_20_position():
    _text_font_or_skip()
    from render.header_image import header_layer

    top = _alpha_bbox(header_layer("Hook Hook").image)[1]
    # The first line box starts at 210; cap-height ink starts a few px lower.
    assert 210 <= top <= 222


def test_header_moves_with_y_and_size():
    _text_font_or_skip()
    from dataclasses import replace

    from render.ass import StyleConfig
    from render.header_image import header_layer

    base = header_layer("Hook", StyleConfig()).box
    moved = header_layer("Hook", replace(StyleConfig(), header_margin_v=700)).box
    bigger = header_layer("Hook", replace(StyleConfig(), header_font_size=84)).box

    assert moved.top - base.top == 490
    assert bigger.bottom - bigger.top > 1.6 * (base.bottom - base.top)


def test_header_is_kept_above_the_caption_zone_and_inside_the_frame():
    _text_font_or_skip()
    from dataclasses import replace

    from render.ass import StyleConfig
    from render.header_image import header_layer

    low = replace(StyleConfig(), header_margin_v=1370, header_font_size=96)
    box = header_layer("Two lines\nof header", low).box
    assert box.bottom <= 1920 - framing.CAPTION_ZONE_PX

    high = replace(
        StyleConfig(), header_margin_v=0, header_plate="solid", header_padding=40
    )
    assert header_layer("Hook", high).box.top >= 0


def test_plate_is_one_union_without_double_alpha():
    _text_font_or_skip()
    from dataclasses import replace

    from render.ass import StyleConfig
    from render.header_image import header_layer

    style = replace(
        StyleConfig(),
        header_plate="translucent",
        header_plate_opacity=50,
        header_padding=30,
        header_outline=0,
    )
    img = header_layer("Line one here\nLine two here", style).image
    alpha = img.getchannel("A")
    # Between the two line boxes the padded boxes overlap: alpha stays 50%.
    box = alpha.getbbox()
    mid_y = (box[1] + box[3]) // 2
    left_x = box[0] + 3
    assert abs(alpha.getpixel((left_x, mid_y)) - 128) <= 2


@pytest.mark.parametrize("align", ["left", "center", "right"])
def test_alignment_moves_short_lines(align):
    _text_font_or_skip()
    from dataclasses import replace

    from render.ass import StyleConfig
    from render.header_image import header_layer

    # Wide line spacing keeps the second line's ink clear of the first's.
    style = replace(
        StyleConfig(), header_align=align, header_outline=0, header_line_spacing=1.8
    )
    img = header_layer("A much longer first line\nshort", style).image
    alpha = img.getchannel("A")
    first = alpha.crop((0, 0, 1080, 270)).getbbox()
    second = alpha.crop((0, 270, 1080, 400)).getbbox()
    if align == "left":
        assert abs(first[0] - second[0]) <= 6
    elif align == "right":
        assert abs(first[2] - second[2]) <= 6
    else:
        assert abs((first[0] + first[2]) - (second[0] + second[2])) <= 6


def test_soft_shadow_adds_ink_below_the_text():
    _text_font_or_skip()
    from dataclasses import replace

    from render.ass import StyleConfig
    from render.header_image import header_layer

    plain = header_layer("Hook", StyleConfig()).box
    shadow = header_layer("Hook", replace(StyleConfig(), header_shadow=True)).box
    assert shadow.bottom > plain.bottom


def _has_libass() -> bool:
    if shutil.which("ffmpeg") is None:
        return False
    out = subprocess.run(
        ["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True
    ).stdout
    return " ass " in out


@pytest.mark.skipif(not _has_libass(), reason="ffmpeg with libass is unavailable")
def test_pillow_default_header_matches_the_libass_header_it_replaces(tmp_path):
    """Same text box as the libass header: top within 2 px, width within 2%."""
    _text_font_or_skip()
    from render import text_image
    from render.header_image import header_layer

    if "Arial" not in text_image.resolve_font("arial").path:
        pytest.skip("the libass comparison is calibrated on Arial")
    text = "When your friend says one more game"
    style_line = (
        "Style: H,Arial,42,&H00FFFFFF,&H00FFFFFF,&H00000000,&HFF000000,"
        "-1,0,0,0,100,100,0,0,1,2,0,8,80,80,210,1"
    )
    ass = "\n".join(
        [
            "[Script Info]",
            "ScriptType: v4.00+",
            "ScaledBorderAndShadow: yes",
            "PlayResX: 1080",
            "PlayResY: 1920",
            "",
            "[V4+ Styles]",
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
            "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
            "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
            "Alignment, MarginL, MarginR, MarginV, Encoding",
            style_line,
            "",
            "[Events]",
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
            "Effect, Text",
            f"Dialogue: 0,0:00:00.00,0:00:01.00,H,,0,0,0,,{text}",
            "",
        ]
    )
    (tmp_path / "h.ass").write_text(ass)
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=0x808080:s=1080x1920",
            "-frames:v",
            "1",
            "-vf",
            "ass=h.ass",
            "frame.png",
        ],
        check=True,
        cwd=tmp_path,
    )
    frame = Image.open(tmp_path / "frame.png").convert("RGB")
    grey = Image.new("RGB", frame.size, (128, 128, 128))
    diff = ImageChops.difference(frame, grey).convert("L")
    libass = diff.point(lambda v: 255 if v > 24 else 0).getbbox()

    layer = header_layer(text).image
    pillow = layer.getchannel("A").point(lambda v: 255 if v > 24 else 0).getbbox()

    assert abs(libass[1] - pillow[1]) <= 2
    assert abs(libass[3] - pillow[3]) <= 2
    libass_w, pillow_w = libass[2] - libass[0], pillow[2] - pillow[0]
    assert abs(libass_w - pillow_w) <= 0.02 * libass_w


# --- preview endpoint -----------------------------------------------------------


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


def _ready_job(info: MediaInfo = VERTICAL) -> jobs.Job:
    job = jobs.create_job()
    job.status = "ready"
    job.source_path = job.dir / "source.mp4"
    job.source_path.write_bytes(b"source")
    job.info = info
    return job


def _preview(job_id, body):
    with TestClient(main.app, base_url="http://127.0.0.1:8000") as client:
        return client.post(f"/api/jobs/{job_id}/header-preview", json=body)


def _decode(data_url: str) -> Image.Image:
    assert data_url.startswith("data:image/png;base64,")
    raw = base64.b64decode(data_url.split(",", 1)[1])
    return Image.open(io.BytesIO(raw)).convert("RGBA")


def test_preview_is_the_exact_png_the_render_burns_in(
    isolated_jobs, tmp_path, captured
):
    _text_font_or_skip()
    job = _ready_job()
    look = {
        "y": 420,
        "size": 56,
        "color": "FFD60A",
        "plate": "translucent",
        "plate_radius": 18,
        "shadow": True,
        "align": "left",
    }
    before = sorted(p.name for p in job.dir.iterdir())

    res = _preview(job.id, {"header": "Two lines\nof hook", "header_look": look})

    assert res.status_code == 200
    assert sorted(p.name for p in job.dir.iterdir()) == before  # nothing written
    preview = _decode(res.json()["image"])
    out = tmp_path / "render"
    out.mkdir()
    pipeline.render(
        out,
        tmp_path / "src.mp4",
        VERTICAL,
        RenderRequest(header="Two lines\nof hook", header_look=look),
    )
    burned = Image.open(out / "header.png").convert("RGBA")
    assert preview.size == burned.size == (1080, 1920)
    assert ImageChops.difference(preview, burned).getbbox() is None
    box = res.json()["box"]
    assert box == list(burned.getchannel("A").getbbox())


def test_preview_of_an_empty_header_has_no_image(isolated_jobs):
    job = _ready_job()
    res = _preview(job.id, {"header": "   "})
    assert res.status_code == 200
    assert res.json()["image"] is None
    assert res.json()["box"] is None


def test_preview_rejects_out_of_bounds_looks_and_unknown_jobs(isolated_jobs):
    job = _ready_job()
    assert (
        _preview(job.id, {"header": "x", "header_look": {"size": 999}}).status_code
        == 422
    )
    assert _preview("nope", {"header": "x"}).status_code == 404


def test_preview_reports_a_png_failure_without_failing(isolated_jobs, monkeypatch):
    job = _ready_job()

    def broken(*_args, **_kwargs):
        raise OSError("no font")

    monkeypatch.setattr(main, "header_png_bytes", broken)
    res = _preview(job.id, {"header": "Hook"})
    assert res.status_code == 200
    assert res.json()["image"] is None
    assert "no font" in res.json()["note"]


# --- the face-near-header zone follows the header -------------------------------


def _faces_at_output_top(output_top_px: float) -> list[TrackSample]:
    source_h, face_h = 1080, 120
    top_src = output_top_px * source_h / 1920
    cy = top_src + face_h / 2
    return [TrackSample(t=i * 0.2, cx=960, cy=cy, w=100, h=face_h) for i in range(50)]


def test_plan_keeps_face_spans_for_later_zone_checks():
    plan = framing.plan_crop(_faces_at_output_top(800), [], 1920, 1080)
    assert len(plan.face_spans) == 50
    top, _bottom = plan.face_spans[0]
    assert round(top * 1920 / 1080) == 800


def test_header_warning_follows_the_header_box():
    plan = framing.plan_crop(_faces_at_output_top(800), [], 1920, 1080)
    assert plan.warning is None  # clear of the default header

    assert framing.header_warning(plan, (190, 300)) is None
    assert framing.header_warning(plan, (760, 900)) == "header_zone"
    assert framing.header_warning(plan, None) is None


def test_no_header_means_no_header_warning():
    plan = framing.plan_crop(_faces_at_output_top(230), [], 1920, 1080)
    assert plan.warning == "header_zone"  # the default header zone, at ingest
    assert framing.header_warning(plan, None) is None


def test_old_plans_without_face_spans_keep_their_ingest_warning():
    plan = CropPlan(
        decision="crop",
        reason="ok",
        face_rate=1.0,
        safe_rate=1.0,
        window_w=608,
        window_h=1080,
        samples=[CropSample(t=0.0, x=0)],
        warning="header_zone",
    )
    assert framing.header_warning(plan, (700, 800)) == "header_zone"


def test_preview_returns_the_warning_for_the_drawn_header(isolated_jobs):
    _text_font_or_skip()
    job = _ready_job(MediaInfo(width=1920, height=1080, duration=2.0, has_audio=False))
    job.crop_plan = framing.plan_crop(_faces_at_output_top(800), [], 1920, 1080)
    job.music_plan = framing.plan_crop(
        _faces_at_output_top(800), [], 1920, 1080, profile="music"
    )

    default = _preview(job.id, {"header": "Hook"}).json()
    moved = _preview(job.id, {"header": "Hook", "header_look": {"y": 790}}).json()

    assert default["warnings"] == {"crop_plan": None, "music_plan": None}
    assert moved["warnings"] == {
        "crop_plan": "header_zone",
        "music_plan": "header_zone",
    }


def test_emoji_row_composes_centred_images():
    from render.text_image import compose_row

    a = Image.new("RGBA", (10, 10), (255, 0, 0, 255))
    b = Image.new("RGBA", (20, 20), (0, 0, 255, 255))
    row = compose_row([a, b], gap=4)
    assert row.size == (34, 20)
    assert row.getpixel((5, 10))[:3] == (255, 0, 0)
    assert row.getpixel((5, 2))[3] == 0  # the small image is centred vertically
    assert row.getpixel((20, 10))[:3] == (0, 0, 255)


def test_header_options_list_the_presets_and_fonts():
    with TestClient(main.app, base_url="http://127.0.0.1:8000") as client:
        data = client.get("/api/header-options").json()

    assert set(data["presets"]) == {"plain", "black_plate", "white_plate"}
    assert data["presets"]["plain"]["y"] == 210
    assert data["presets"]["black_plate"]["plate"] == "translucent"
    assert data["fonts"][0]["key"] == "arial"
    assert all(isinstance(f["available"], bool) for f in data["fonts"])
    assert data["max_chars"] == 200
