"""Geometry normalisation for the render (SPEC.md §4 step 2, D12).

Every input first enters one display-oriented, square-pixel coordinate space.
Exactly-9:16 (1080x1920) input then passes through. Landscape input can use the
subject-crop plan or **blur-pad** to 1080x1920; other input blur-pads.
"""

from __future__ import annotations

from typing import Literal

from app.models import CropPlan, Geometry

TARGET_W = 1080
TARGET_H = 1920

# Bare filename for the sendcmd crop-window command file. cwd is the job dir at
# render time, the same convention as the ASS file (spike S1, crop-sendcmd.md).
CROP_CMD_NAME = "crop.cmd"


def normalize_statement(
    width: int,
    height: int,
    input_label: str = "[0:v]",
    out_label: str = "[src]",
) -> str:
    """Normalize autorotated input into the shared square-pixel coordinate space."""
    return f"{input_label}scale={width}:{height},setsar=1{out_label}"


def is_target(width: int, height: int) -> bool:
    return width == TARGET_W and height == TARGET_H


def resolve_geometry(
    requested: Geometry,
    plan: CropPlan | None,
    width: int,
    height: int,
) -> Literal["pass", "blur_pad", "crop"]:
    """Resolve a per-clip geometry request to a concrete render mode (ADR-001).

    Exactly-9:16 input always passes through, whatever was requested. Otherwise
    ``blur_pad`` and ``crop`` are honoured (``crop`` degrades to ``blur_pad``
    with no plan), and ``auto`` follows the plan decision.
    """
    if is_target(width, height):
        return "pass"
    if requested == "blur_pad":
        return "blur_pad"
    if requested == "crop":
        return "crop" if plan is not None else "blur_pad"
    # auto
    return plan.decision if plan is not None else "blur_pad"


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
        f"[bgb][fgs]overlay=(W-w)/2:(H-h)/2,setsar=1{out_label}",
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
        f"scale={TARGET_W}:{TARGET_H},setsar=1{out_label}"
    ]


def crop_command_file(plan: CropPlan) -> str:
    """Return the ``sendcmd`` command file text for a crop plan.

    One line per plan sample: ``<t> crop x <x>;``. Times are seconds with a
    decimal point; x is the even source-pixel window left edge.
    """
    lines = [f"{sample.t:.3f} crop x {sample.x};" for sample in plan.samples]
    return "\n".join(lines) + "\n"
