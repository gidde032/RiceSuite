"""Geometry normalisation for the render (SPEC.md §4 step 2, D12).

Exactly-9:16 (1080x1920) input passes through untouched. Anything else is
**blur-padded** to 1080x1920 — the same frame is scaled to fill and blurred as a
background, with the untouched frame scaled to fit and centred on top. Content is
never cropped.
"""

from __future__ import annotations

TARGET_W = 1080
TARGET_H = 1920


def is_target(width: int, height: int) -> bool:
    return width == TARGET_W and height == TARGET_H


def blur_pad_statements(input_label: str = "[0:v]", out_label: str = "[base]") -> list[str]:
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
