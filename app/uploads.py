# Photo ingest: validate, crop, strip metadata, store.
#
# Three things this module is responsible for, in order of how much they hurt to
# get wrong. (1) Nothing user-controlled becomes a path: files are named by us,
# under settings.upload_dir, never by the browser's filename. (2) EXIF goes away
# — a phone photo carries GPS and a device serial, and SPEC §8 says uploads are
# stripped. Re-encoding through Pillow without passing `exif` is what drops it,
# with exif_transpose() first so the photo keeps the orientation that metadata
# was describing. (3) Only real images get in: the format is decided by
# sniffing the bytes, not by trusting Content-Type or a .jpg suffix.
from __future__ import annotations

import io
import uuid
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

# Reject decompression bombs: an image whose pixel count exceeds this limit
# raises Image.DecompressionBombError on open, instead of allocating memory for
# a huge decoded raster inside a small file (CWE-400). 25M pixels (~100 MB) is
# well above any sane book photo while staying safe.
Image.MAX_IMAGE_PIXELS = 25_000_000

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_DIMENSION = 1600
MIN_DIMENSION = 40
ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP"}
# Slot is part of the stored name and reaches the filesystem, so it is an enum.
SLOTS = ("front", "back", "spine")


@dataclass(frozen=True)
class SavedUpload:
    """What landed on disk, ready for the photos row."""

    filename: str
    width: int
    height: int
    format: str


class UploadError(ValueError):
    """Anything the caller can turn into a form message. Never a stack trace."""


def save_upload(
    data: bytes,
    *,
    upload_dir: str | Path,
    book_id: int,
    slot: str,
    crop: tuple[int, int, int, int] | None = None,
) -> SavedUpload:
    """Turn raw upload bytes into a stored image, returning its filename."""
    if slot not in SLOTS:
        raise UploadError(f"Unknown photo slot: {slot!r}")
    if not data:
        raise UploadError("The uploaded file is empty.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise UploadError(
            f"That file is {len(data) // 1024} KB; the limit is {MAX_UPLOAD_BYTES // 1024} KB."
        )

    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        # Not a decodable image at all (or absurdly large): same answer as a
        # disallowed type, because the user's next step is the same.
        raise UploadError(
            "That file is not a readable image. Upload a JPEG, PNG or WEBP."
        ) from None

    with image:
        source_format = image.format
        if source_format not in ALLOWED_FORMATS:
            raise UploadError(
                f"Unsupported image type {source_format or 'unknown'}; upload JPEG, PNG or WEBP."
            )
        image = ImageOps.exif_transpose(image)
        region = _crop_box(image, crop)
        if region is not None:
            image = image.crop(region)
        image.thumbnail((MAX_DIMENSION, MAX_DIMENSION), Image.LANCZOS)
        if image.width < MIN_DIMENSION or image.height < MIN_DIMENSION:
            raise UploadError(
                f"That crop is {image.width}x{image.height}; keep at least"
                f" {MIN_DIMENSION}x{MIN_DIMENSION}."
            )

        # JPEG has no alpha channel: a transparent PNG must be flattened or the
        # save fails deep inside Pillow with an unhelpful message.
        target_format = "JPEG" if source_format == "JPEG" else source_format
        if target_format == "JPEG" and image.mode not in ("RGB", "L"):
            image = image.convert("RGB")
        elif target_format == "PNG" and image.mode not in ("RGB", "RGBA", "L"):
            image = image.convert("RGBA")

        directory = Path(upload_dir)
        directory.mkdir(parents=True, exist_ok=True)
        suffix = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp"}[target_format]
        filename = f"book{int(book_id)}-{slot}-{uuid.uuid4().hex}{suffix}"
        destination = directory / filename

        # Write to a sibling then rename, so a crash mid-write cannot leave a
        # half file that the photos row points at.
        temporary = destination.with_suffix(destination.suffix + ".part")
        image.save(temporary, format=target_format, optimize=True)
        temporary.replace(destination)

        return SavedUpload(
            filename=filename, width=image.width, height=image.height, format=target_format
        )


def delete_upload(upload_dir: str | Path, filename: str) -> bool:
    """Remove a stored file by name. Foreign paths are refused, not resolved."""
    if not filename or "/" in filename or "\\" in filename or filename.startswith("."):
        return False
    target = Path(upload_dir) / filename
    if not target.is_file():
        return False
    target.unlink()
    return True


def read_upload(upload_dir: str | Path, filename: str) -> bytes | None:
    """Bytes of a stored file, or None. Used to serve uploads without exposing a tree."""
    if not filename or "/" in filename or "\\" in filename or filename.startswith("."):
        return None
    target = Path(upload_dir) / filename
    return target.read_bytes() if target.is_file() else None


def _crop_box(image: Image.Image, crop: tuple[int, int, int, int] | None):
    """Validate a requested crop against the real image size."""
    if crop is None:
        return None
    left, top, right, bottom = (int(value) for value in crop)
    if left < 0 or top < 0 or right <= left or bottom <= top:
        raise UploadError("That crop box is not a usable rectangle.")
    if right > image.width or bottom > image.height:
        raise UploadError(f"That crop runs outside the image ({image.width}x{image.height}).")
    return (left, top, right, bottom)
