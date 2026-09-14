"""Geometry normalisation for the render (SPEC.md §4 step 2, D12).

Exactly-9:16 (1080x1920) input passes through untouched. Anything else is
**blur-padded** to 1080x1920 — the same frame is scaled to fill and blurred as a
background, with the untouched frame scaled to fit and centred on top. Content is
never cropped.
"""

from __future__ import annotations

from app.models import CropPlan

TARGET_W = 1080
TARGET_H = 1920

# Bare filename for the sendcmd crop-window command file. cwd is the job dir at
# render time, the same convention as the ASS file (spike S1, crop-sendcmd.md).
CROP_CMD_NAME = "crop.cmd"


def is_target(width: int, height: int) -> bool:
    return width == TARGET_W and height == TARGET_H


def blur_pad_statements(
    input_label: str = "[0:v]", out_label: str = "[base]"
) -> list[str]:
    """filter_complex statements that blur-pad ``input_label`` to 1080x1920."""
    return [
        f"{input_label}split=2[bg][fg]",
        (
            f"[bg]scale={TARGET_W}:{TARGET_H}:force_original_aspect_ratio=increase,"
            f"crop={TARGET_W}:{TARGET_H},boxblur=20:2[bgb]"
        ),
        f"[fg]scale={TARGET_W}:{TARGET_H}:force_original_aspect_ratio=decrease[fgs]",
        f"[bgb][fgs]overlay=(W-w)/2:(H-h)/2{out_label}",
    ]


def crop_statements(
    plan: CropPlan,
    input_label: str = "[0:v]",
    out_label: str = "[base]",
) -> list[str]:
    """filter_complex statements that crop ``input_label`` to a moving 9:16 window.

    A single ``sendcmd`` reads the per-sample window x from ``CROP_CMD_NAME`` and
    drives the ``crop`` filter's x over time; ``crop=W:H:0:0`` stays literal and
    only x is overridden (spike S1). The window is then scaled to 1080x1920. The
    caller writes the command file with :func:`crop_command_file`.
    """
    return [
        f"{input_label}sendcmd=f={CROP_CMD_NAME},"
        f"crop={plan.window_w}:{plan.window_h}:0:0,"
        f"scale={TARGET_W}:{TARGET_H}{out_label}"
    ]


def crop_command_file(plan: CropPlan) -> str:
    """Return the ``sendcmd`` command file text for a crop plan.

    One line per plan sample: ``<t> crop x <x>;``. Times are seconds with a
    decimal point; x is the even source-pixel window left edge.
    """
    lines = [f"{sample.t:.3f} crop x {sample.x};" for sample in plan.samples]
    return "\n".join(lines) + "\n"
