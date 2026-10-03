"""Still-photo ingest (Issue #54, SPEC.md D17).

A photo job holds one still image that renders as a blur-padded clip of a
chosen length. Upload normalizes the image once with Pillow: it applies the
EXIF rotation, flattens transparency onto black, bounds the size, and writes a
PNG. The render path then reads that PNG as a looped ffmpeg input, so ffmpeg's
own EXIF handling never matters.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from PIL import Image, ImageOps

from app.probe import MediaInfo, _even

PHOTO_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp"})
PHOTO_FORMATS = frozenset({"PNG", "JPEG", "WEBP"})
PHOTO_SOURCE_NAME = "source.png"
PHOTO_TYPES = frozenset({"image/png", "image/jpeg", "image/webp"})
# The blur-pad foreground is at most 1920 px tall, so a 2160 px long edge keeps
# every frame sharp, and ffmpeg decodes a small PNG for each looped frame.
MAX_EDGE = 2160
# Refuse larger images before decoding: the decode runs under the job lock.
MAX_PIXELS = 60_000_000
# Upload stores this length; the render request carries the chosen length.
DEFAULT_SECONDS = 10.0


class PhotoError(ValueError):
    """The upload is not a readable still photo. ``detail`` is safe to show."""

    detail = "could not read image"


class UnsupportedPhotoError(PhotoError):
    """The upload is a readable image in a format Clipper does not accept."""

    detail = "unsupported image type: use PNG, JPEG, or WebP"


class OversizePhotoError(PhotoError):
    """The image has more pixels than Clipper decodes."""

    detail = "image is too large: use one under 60 megapixels"


def classify(
    filename: str | None, content_type: str | None
) -> Literal["video", "photo", "unsupported"]:
    """Route an upload by name and declared type.

    PNG, JPEG, and WebP are photos, by name or by declared type; ``normalize``
    then checks the bytes. Any other ``image/*`` upload is refused, so a GIF or
    HEIC never reaches the video probe by accident.
    """
    suffix = Path(filename or "").suffix.lower()
    declared = (content_type or "").lower()
    if suffix in PHOTO_SUFFIXES or declared in PHOTO_TYPES:
        return "photo"
    if declared.startswith("image/"):
        return "unsupported"
    return "video"


def _opaque(image: Image.Image) -> Image.Image:
    """Return ``image`` as 8-bit RGB, with any transparency flattened onto black."""
    if image.mode in {"I", "I;16", "I;16B", "I;16L"}:
        # 16-bit grayscale: scale to 8 bits; a plain convert clips to white.
        image = image.convert("I").point(lambda v: v / 256).convert("L")
    if image.has_transparency_data:
        rgba = image.convert("RGBA")
        flat = Image.new("RGB", rgba.size, (0, 0, 0))
        flat.paste(rgba, mask=rgba.getchannel("A"))
        return flat
    return image.convert("RGB")


def normalize(raw: Path, dest: Path) -> MediaInfo:
    """Write ``raw`` as an upright, opaque PNG at ``dest`` and describe it."""
    try:
        with Image.open(raw) as opened:
            if opened.format not in PHOTO_FORMATS:
                raise UnsupportedPhotoError(f"image format {opened.format}")
            if opened.width * opened.height > MAX_PIXELS:
                raise OversizePhotoError(f"{opened.width}x{opened.height}")
            if opened.format == "JPEG":
                # Decode a JPEG at a reduced scale when it is far larger than
                # needed; this bounds memory and time under the job lock.
                opened.draft("RGB", (MAX_EDGE, MAX_EDGE))
            image = _opaque(ImageOps.exif_transpose(opened))
            image.thumbnail((MAX_EDGE, MAX_EDGE))
            image.save(dest, format="PNG")
            width, height = image.size
    except PhotoError:
        raise
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError) as exc:
        raise PhotoError("could not read image") from exc
    return MediaInfo(
        width=_even(width),
        height=_even(height),
        duration=DEFAULT_SECONDS,
        has_audio=False,
        coded_width=width,
        coded_height=height,
        still=True,
    )
