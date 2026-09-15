"""F2 crop statements, command file, and the pipeline crop branch (ADR-001)."""

from __future__ import annotations

import pytest

from app.models import CropPlan, CropSample
from app.probe import MediaInfo
from render import geometry
from render.pipeline import render


def _crop_plan(samples: list[tuple[float, int]], window=(608, 1080)) -> CropPlan:
    return CropPlan(
        decision="crop",
        reason="ok",
        face_rate=0.96,
        safe_rate=0.99,
        window_w=window[0],
        window_h=window[1],
        samples=[CropSample(t=t, x=x) for t, x in samples],
    )


def test_crop_statements_drive_x_via_sendcmd_and_scale_to_target():
    plan = _crop_plan([(0.0, 0), (0.2, 120)])
    stmts = geometry.crop_statements(plan, "[0:v]", "[base]")
    assert stmts == ["[0:v]sendcmd=f=crop.cmd,crop=608:1080:0:0,scale=1080:1920[base]"]


def test_crop_statements_keep_window_dims_literal_and_labels():
    plan = _crop_plan([(0.0, 10)], window=(720, 1280))
    (stmt,) = geometry.crop_statements(plan, "[v0]", "[out]")
    assert stmt == "[v0]sendcmd=f=crop.cmd,crop=720:1280:0:0,scale=1080:1920[out]"


def test_crop_command_file_is_one_semicolon_line_per_sample():
    plan = _crop_plan([(0.0, 0), (0.2, 120), (1.4, 8)])
    text = geometry.crop_command_file(plan)
    assert text == "0.000 crop x 0;\n0.200 crop x 120;\n1.400 crop x 8;\n"


def test_crop_command_file_ends_with_newline_even_for_one_sample():
    text = geometry.crop_command_file(_crop_plan([(2.0, 1200)]))
    assert text == "2.000 crop x 1200;\n"


def _capture_render(monkeypatch):
    """Run render() with run_owned stubbed; return the filter_complex string."""
    captured: dict[str, list[str]] = {}

    class Completed:
        returncode = 0
        stderr = ""

    def fake_run(cmd, *args, **kwargs):
        captured["cmd"] = cmd
        return Completed()

    monkeypatch.setattr("render.pipeline.run_owned", fake_run)
    return captured


def _filter_complex(cmd: list[str]) -> str:
    return cmd[cmd.index("-filter_complex") + 1]


def test_render_crop_plan_writes_cmd_and_uses_sendcmd_path(monkeypatch, tmp_path):
    from app.models import RenderRequest

    captured = _capture_render(monkeypatch)
    plan = _crop_plan([(0.0, 0), (0.2, 120)])

    render(
        tmp_path,
        tmp_path / "source.mp4",
        MediaInfo(width=1920, height=1080, duration=15.0, has_audio=False),
        RenderRequest(),
        plan=plan,
    )

    fc = _filter_complex(captured["cmd"])
    assert "sendcmd=f=crop.cmd,crop=608:1080:0:0,scale=1080:1920[base]" in fc
    assert "[base]subtitles=captions.ass[vout]" in fc
    assert "boxblur" not in fc
    cmd_text = (tmp_path / "crop.cmd").read_text()
    assert cmd_text == "0.000 crop x 0;\n0.200 crop x 120;\n"


def test_render_blur_pad_plan_keeps_blur_path_and_writes_no_cmd(monkeypatch, tmp_path):
    from app.models import RenderRequest

    captured = _capture_render(monkeypatch)
    plan = CropPlan(
        decision="blur_pad",
        reason="low_safe_rate",
        face_rate=0.9,
        safe_rate=0.5,
        window_w=608,
        window_h=1080,
        samples=[CropSample(t=0.0, x=0)],
    )

    render(
        tmp_path,
        tmp_path / "source.mp4",
        MediaInfo(width=1920, height=1080, duration=15.0, has_audio=False),
        RenderRequest(),
        plan=plan,
    )

    fc = _filter_complex(captured["cmd"])
    assert "boxblur" in fc
    assert "sendcmd" not in fc
    assert not (tmp_path / "crop.cmd").exists()


def _blur_pad_plan() -> CropPlan:
    return CropPlan(
        decision="blur_pad",
        reason="low_safe_rate",
        face_rate=0.9,
        safe_rate=0.5,
        window_w=608,
        window_h=1080,
        samples=[CropSample(t=0.0, x=0)],
    )


# resolve_geometry over (requested) x (plan) x (orientation). A 1080x1920 job
# always passes through; a landscape job dispatches on the request and plan.
@pytest.mark.parametrize(
    "requested,plan_kind,expected",
    [
        # auto follows the plan decision, blur_pad without a plan.
        ("auto", "crop", "crop"),
        ("auto", "blur_pad", "blur_pad"),
        ("auto", "none", "blur_pad"),
        # blur_pad is always blur_pad.
        ("blur_pad", "crop", "blur_pad"),
        ("blur_pad", "blur_pad", "blur_pad"),
        ("blur_pad", "none", "blur_pad"),
        # crop crops whenever a plan exists (overriding a blur_pad decision).
        ("crop", "crop", "crop"),
        ("crop", "blur_pad", "crop"),
        ("crop", "none", "blur_pad"),
    ],
)
def test_resolve_geometry_landscape_truth_table(requested, plan_kind, expected):
    plan = {
        "crop": _crop_plan([(0.0, 0)]),
        "blur_pad": _blur_pad_plan(),
        "none": None,
    }[plan_kind]
    assert geometry.resolve_geometry(requested, plan, 1920, 1080) == expected


@pytest.mark.parametrize("requested", ["auto", "blur_pad", "crop"])
@pytest.mark.parametrize("plan_kind", ["crop", "blur_pad", "none"])
def test_resolve_geometry_target_always_passes(requested, plan_kind):
    plan = {
        "crop": _crop_plan([(0.0, 0)]),
        "blur_pad": _blur_pad_plan(),
        "none": None,
    }[plan_kind]
    assert geometry.resolve_geometry(requested, plan, 1080, 1920) == "pass"


def test_render_vertical_with_crop_plan_uses_passthrough(monkeypatch, tmp_path):
    """A 1080x1920 job never crops, even if handed a forced crop plan."""
    from app.models import RenderRequest

    captured = _capture_render(monkeypatch)

    render(
        tmp_path,
        tmp_path / "source.mp4",
        MediaInfo(width=1080, height=1920, duration=15.0, has_audio=False),
        RenderRequest(),
        plan=_crop_plan([(0.0, 0), (0.2, 120)]),
    )

    fc = _filter_complex(captured["cmd"])
    assert fc.startswith("[0:v]subtitles=captions.ass")
    assert "sendcmd" not in fc
    assert "boxblur" not in fc
    assert not (tmp_path / "crop.cmd").exists()


def test_render_without_plan_is_unchanged_blur_pad(monkeypatch, tmp_path):
    from app.models import RenderRequest

    captured = _capture_render(monkeypatch)

    render(
        tmp_path,
        tmp_path / "source.mp4",
        MediaInfo(width=1920, height=1080, duration=15.0, has_audio=False),
        RenderRequest(),
    )

    fc = _filter_complex(captured["cmd"])
    assert "boxblur" in fc
    assert "sendcmd" not in fc
