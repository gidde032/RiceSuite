"""Pure framing policy for the subject-focused 9:16 crop (ADR-001).

This module turns a face track into a :class:`CropPlan`: a per-sample window x
plus a crop-or-blur-pad decision. It is pure. It reads no files and calls no
cv2 or ffmpeg. Detection and scene cuts belong to ``render.subject`` (F3); the
ffmpeg statements belong to ``render.geometry`` (F2).

Policy, per sample, in order: snap after a cut or face jump, snap on a face
returning after a loss, hold inside the outer lock zone, else move toward the
inner settle boundary under a pan cap. Geometry interpolates ordinary moves.
See ``docs/design/subject-crop-spec.md`` (Framing policy).
"""

from __future__ import annotations

from typing import Literal

from app.models import Content, CropPlan, CropReason, CropSample, TrackSample
from render.ass import CAPTION_ZONE_PX, StyleConfig

# Sampling and motion policy (source_w-relative unless noted).
SAMPLE_FPS = 5
DEAD_ZONE = 0.20
SETTLE_ZONE = 0.10
INTERPOLATION_FPS = 30
PAN_CAP = 0.50
JUMP_CUT = 0.15
LOSS_S = 1.0
SAFE_FRACTION = 0.70
SCENE_MIN_SPEECH = 0.3
SCENE_MIN_MUSIC = 0.2
FACE_RATE_MIN = 0.80
SAFE_RATE_MIN = 0.95
# The "face near header" zone is the drawn header's own top-to-bottom span
# (RiceSuite #65). At ingest no header is known yet, so the plan's stored
# warning uses the default header's span: from ``header_margin_v`` down to the
# bottom of a 2-line default header with its plate (160 px covers it). The
# editor re-checks the zone for the clip's header through ``header_warning``.
HEADER_BLOCK_MAX_PX = 160
HEADER_ZONE_PX = StyleConfig().header_margin_v + HEADER_BLOCK_MAX_PX
DEFAULT_HEADER_SPAN = (StyleConfig().header_margin_v, HEADER_ZONE_PX)
WARN_FRACTION = 0.20

# Output height the header/caption zones are defined against.
_OUTPUT_W = 1080
_OUTPUT_H = 1920
# Central-window margin on each side, from SAFE_FRACTION.
_SAFE_MARGIN = (1.0 - SAFE_FRACTION) / 2.0


def _even_down(value: int) -> int:
    """Round an int down to the nearest even number."""
    return value if value % 2 == 0 else value - 1


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def window_size(source_w: int, source_h: int) -> tuple[int, int]:
    """Return the (window_w, window_h) of the 9:16 crop window.

    window_h is the full source height; window_w is ``source_h * 9 / 16``
    rounded to an even int. The caller decides whether the window fits.
    """
    window_h = source_h
    window_w = _even_down(round(source_h * 9 / 16))
    return window_w, window_h


def _croppable(source_w: int, source_h: int, window_w: int) -> bool:
    """True when a 9:16 window fits a landscape source.

    A source that is not wider than tall, or narrower than the window, cannot
    be cropped; the caller falls back to blur-pad with ``no_samples``.
    """
    return source_w > source_h and 0 < window_w < source_w


def failed_plan(
    reason: CropReason,
    source_w: int = 0,
    source_h: int = 0,
    profile: Content = "speech",
) -> CropPlan:
    """Build a blur-pad plan carrying a failure ``reason`` and zero rates."""
    window_w = window_h = 0
    samples: list[CropSample] = []
    if source_w > 0 and source_h > 0:
        ww, wh = window_size(source_w, source_h)
        if _croppable(source_w, source_h, ww):
            window_w, window_h = ww, wh
            centered = _even_down((source_w - window_w) // 2)
            samples = [CropSample(t=0.0, x=centered)]
    return CropPlan(
        decision="blur_pad",
        reason=reason,
        face_rate=0.0,
        safe_rate=0.0,
        window_w=window_w,
        window_h=window_h,
        samples=samples,
        profile=profile,
        interpolation_fps=INTERPOLATION_FPS,
    )


def plan_crop(
    track: list[TrackSample | None],
    cuts: list[float] | list[tuple[float, float]],
    source_w: int,
    source_h: int,
    *,
    sample_times: list[float] | None = None,
    profile: Content = "speech",
    motion_response: float = 1.0,
    dead_zone: float = DEAD_ZONE,
    settle_zone: float = SETTLE_ZONE,
    interpolation_fps: int = INTERPOLATION_FPS,
) -> CropPlan:
    """Resolve a track into a :class:`CropPlan`.

    ``track`` is one entry per sample (a face box or ``None``) at ``SAMPLE_FPS``.
    ``cuts`` are scene-cut times or ``(t, score)`` pairs. When scores are present
    the profile threshold filters which cuts snap. ``motion_response`` controls
    how much of an ordinary target delta is applied per sample; cut and
    loss-return snaps remain immediate. The default of 1.0 preserves the
    ratified production behavior. ``dead_zone`` is the outer hold boundary;
    ``settle_zone`` leaves the subject inside an inner boundary after an
    ordinary correction instead of forcing it back to exact center.
    """
    if not 0.0 < motion_response <= 1.0:
        raise ValueError("motion_response must be greater than 0 and at most 1")
    if not 0.0 <= settle_zone <= dead_zone < 0.5:
        raise ValueError(
            "settle_zone and dead_zone must satisfy 0 <= settle_zone <= dead_zone < 0.5"
        )
    if not 0 <= interpolation_fps <= 120:
        raise ValueError("interpolation_fps must be between 0 and 120")

    window_w, window_h = window_size(source_w, source_h)
    if not _croppable(source_w, source_h, window_w):
        return failed_plan("no_samples", source_w, source_h, profile)
    if not track:
        return failed_plan("no_samples", source_w, source_h, profile)

    threshold = SCENE_MIN_MUSIC if profile == "music" else SCENE_MIN_SPEECH
    cut_times: list[float] = []
    for entry in cuts:
        if isinstance(entry, (list, tuple)):
            t_val, score = float(entry[0]), float(entry[1])
            if score > threshold:
                cut_times.append(t_val)
        else:
            cut_times.append(float(entry))

    step = 1.0 / SAMPLE_FPS
    max_x = source_w - window_w
    center_x = float((source_w - window_w) // 2)

    if sample_times is not None and len(sample_times) != len(track):
        raise ValueError("sample_times must align one-for-one with track")

    def sample_time(i: int, sample: TrackSample | None) -> float:
        if sample_times is not None:
            return sample_times[i]
        return sample.t if sample is not None else i * step

    def target_for(sample: TrackSample) -> int:
        return int(_clamp(round(sample.cx - window_w / 2), 0, max_x))

    samples_out: list[CropSample] = []
    face_records: list[tuple[TrackSample, int]] = []
    x: float | None = None
    prev_t: float | None = None
    last_face_t: float | None = None
    prev_face_cx: float | None = None
    lost = False
    pending_cut = False

    for i, sample in enumerate(track):
        t_i = sample_time(i, sample)
        has_face = sample is not None
        scene_cut = prev_t is not None and any(prev_t < c <= t_i for c in cut_times)
        pending_cut = pending_cut or scene_cut
        face_jump = (
            has_face
            and prev_face_cx is not None
            and abs(sample.cx - prev_face_cx) > JUMP_CUT * source_w
        )
        after_cut = pending_cut or face_jump
        return_after_loss = has_face and (
            # First face ever acquired: snap straight to it — the window was only
            # holding at center because no subject had been seen yet, so there is
            # no real resting position to pan smoothly away from (B3).
            last_face_t is None or (t_i - last_face_t) > LOSS_S
        )
        snap = False

        if x is None:
            x = float(target_for(sample)) if has_face else center_x
        elif has_face:
            target = target_for(sample)
            if after_cut:
                x = float(target)
                snap = True
                lost = False
                pending_cut = False
            elif lost or return_after_loss:
                x = float(target)
                snap = True
                lost = False
            elif abs(target - x) <= dead_zone * window_w:
                pass  # dead zone: hold
            else:
                dt = t_i - prev_t
                error = target - x
                settle_offset = settle_zone * window_w
                settle_target = target - (
                    settle_offset if error > 0 else -settle_offset
                )
                move = motion_response * (settle_target - x)
                cap = PAN_CAP * source_w * dt
                if abs(move) > cap:
                    move = cap if move > 0 else -cap
                x += move
        else:
            if last_face_t is not None and (t_i - last_face_t) > LOSS_S:
                lost = True

        x = _clamp(x, 0.0, float(max_x))
        even_x = _even_down(max(0, min(_even_down(round(x)), max_x)))
        samples_out.append(CropSample(t=t_i, x=even_x, snap=snap))

        if has_face:
            face_records.append((sample, even_x))
            last_face_t = t_i
            prev_face_cx = sample.cx
        prev_t = t_i

    n_all = len(track)
    n_face = len(face_records)
    if n_face == 0:
        if profile == "music":
            centered = _even_down((source_w - window_w) // 2)
            return CropPlan(
                decision="crop",
                reason="hold_static",
                face_rate=0.0,
                safe_rate=0.0,
                window_w=window_w,
                window_h=window_h,
                samples=[CropSample(t=0.0, x=centered)],
                profile=profile,
                interpolation_fps=interpolation_fps,
            )
        return failed_plan("no_samples", source_w, source_h, profile)

    face_rate = n_face / n_all
    safe = 0
    for sample, xr in face_records:
        lo = xr + _SAFE_MARGIN * window_w
        hi = xr + (1.0 - _SAFE_MARGIN) * window_w
        if lo <= sample.cx <= hi:
            safe += 1
    safe_rate = safe / n_face

    if profile == "music":
        decision, reason = "crop", "ok"
    elif face_rate >= FACE_RATE_MIN and safe_rate >= SAFE_RATE_MIN:
        decision, reason = "crop", "ok"
    elif face_rate < FACE_RATE_MIN:
        decision, reason = "blur_pad", "low_face_rate"
    else:
        decision, reason = "blur_pad", "low_safe_rate"

    face_spans = [
        (round(f.cy - f.h / 2, 1), round(f.cy + f.h / 2, 1)) for f, _ in face_records
    ]
    warning = zone_warning(face_spans, source_h, DEFAULT_HEADER_SPAN)

    return CropPlan(
        decision=decision,
        reason=reason,
        face_rate=face_rate,
        safe_rate=safe_rate,
        window_w=window_w,
        window_h=window_h,
        samples=samples_out,
        warning=warning,
        profile=profile,
        interpolation_fps=interpolation_fps,
        face_spans=face_spans,
    )


def zone_warning(
    face_spans: list[tuple[float, float]],
    source_h: int,
    header_span: tuple[float, float] | None,
) -> str | None:
    """Return the larger over-threshold zone hit, or None.

    ``face_spans`` are (top, bottom) face boxes in source px. ``header_span``
    is the header's (top, bottom) in output px, or None when the clip has no
    header. Zones are defined in output pixels; scale maps them back to source
    pixels.
    """
    if not face_spans or source_h <= 0:
        return None
    n_face = len(face_spans)
    scale = _OUTPUT_H / source_h
    caption_limit = source_h - CAPTION_ZONE_PX / scale
    header_hits = 0
    if header_span is not None:
        top, bottom = header_span[0] / scale, header_span[1] / scale
        header_hits = sum(1 for t, b in face_spans if t < bottom and b > top)
    caption_hits = sum(1 for _t, b in face_spans if b > caption_limit)

    candidates: list[tuple[str, int]] = []
    if header_hits / n_face > WARN_FRACTION:
        candidates.append(("header_zone", header_hits))
    if caption_hits / n_face > WARN_FRACTION:
        candidates.append(("caption_zone", caption_hits))
    if not candidates:
        return None
    return max(candidates, key=lambda c: c[1])[0]


def header_warning(
    plan: CropPlan | None,
    header_span: tuple[float, float] | None,
    *,
    source_w: int | None = None,
    source_h: int | None = None,
    resolved_geometry: Literal["pass", "blur_pad", "crop"] | None = None,
) -> str | None:
    """The plan's warning, re-checked against the clip's drawn header.

    A plan saved before RiceSuite #65 has no face spans; its ingest warning
    stands. When the preview supplies the source dimensions and resolved render
    geometry, face spans are projected onto the output canvas first. Without
    them, retain the original full-height crop mapping for existing callers.
    """
    if plan is None:
        return None
    if not plan.face_spans:
        return plan.warning

    if (
        source_w is None
        or source_h is None
        or source_w <= 0
        or source_h <= 0
        or resolved_geometry is None
    ):
        return zone_warning(plan.face_spans, plan.window_h, header_span)

    if resolved_geometry in {"pass", "crop"}:
        scale = _OUTPUT_H / source_h
        offset_y = 0.0
    else:
        # blur_pad keeps the whole foreground and fits it inside the output;
        # the background fills the rest. Mirror scale=decrease and the centred
        # overlay used by render.geometry.blur_pad_statements.
        scale = min(_OUTPUT_W / source_w, _OUTPUT_H / source_h)
        offset_y = (_OUTPUT_H - source_h * scale) / 2.0

    output_spans = [
        (offset_y + top * scale, offset_y + bottom * scale)
        for top, bottom in plan.face_spans
    ]
    return zone_warning(output_spans, _OUTPUT_H, header_span)
