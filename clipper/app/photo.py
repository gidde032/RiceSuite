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
# Blur-pad needs at most a 1080x1920 cover, so a 4K long edge keeps every
# frame sharp while a camera-size original does not slow each looped frame.
MAX_EDGE = 3840
# Upload stores this length; the render request carries the chosen length.
DEFAULT_SECONDS = 10.0


class PhotoError(ValueError):
    """The upload is not a readable still photo."""


class UnsupportedPhotoError(PhotoError):
    """The upload is a readable image in a format Clipper does not accept."""


def classify(
    filename: str | None, content_type: str | None
) -> Literal["video", "photo", "unsupported"]:
    """Route an upload by name and declared type.

    PNG, JPEG, and WebP are photos. Any other ``image/*`` upload is refused, so a
    GIF or HEIC never reaches the video probe by accident.
    """
    suffix = Path(filename or "").suffix.lower()
    if suffix in PHOTO_SUFFIXES:
        return "photo"
    if (content_type or "").lower().startswith("image/"):
        return "unsupported"
    return "video"


def normalize(raw: Path, dest: Path) -> MediaInfo:
    """Write ``raw`` as an upright, opaque PNG at ``dest`` and describe it."""
    try:
        with Image.open(raw) as opened:
            if opened.format not in PHOTO_FORMATS:
                raise UnsupportedPhotoError(f"image format {opened.format}")
            image = ImageOps.exif_transpose(opened)
            if image.mode in {"RGBA", "LA"} or "transparency" in image.info:
                rgba = image.convert("RGBA")
                image = Image.new("RGB", rgba.size, (0, 0, 0))
                image.paste(rgba, mask=rgba.getchannel("A"))
            else:
                image = image.convert("RGB")
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
