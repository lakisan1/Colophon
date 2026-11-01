# Photo ingest tests. The fixture images are generated with Pillow in
# tmp_path — including a JPEG that really carries EXIF and GPS — because "EXIF
# was stripped" has to be proven against a file that had it, not asserted.
from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image

from app.uploads import (
    MAX_DIMENSION,
    UploadError,
    delete_upload,
    read_upload,
    save_upload,
)


def _jpeg(width: int = 400, height: int = 600, *, with_exif: bool = False) -> bytes:
    image = Image.new("RGB", (width, height), (120, 60, 40))
    buffer = io.BytesIO()
    if with_exif:
        exif = Image.Exif()
        exif[0x010F] = "CameraMaker"  # Make
        exif[0x0110] = "Model X"  # Model
        exif[0x8825] = {1: "N", 2: (44, 0, 0), 3: "E", 4: (20, 0, 0)}  # GPSInfo
        image.save(buffer, format="JPEG", exif=exif.tobytes(), quality=95)
    else:
        image.save(buffer, format="JPEG", quality=95)
    return buffer.getvalue()


def _png(*, rgba: bool = True) -> bytes:
    mode = "RGBA" if rgba else "RGB"
    image = Image.new(mode, (300, 300), (10, 200, 30, 255) if rgba else (10, 200, 30))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def test_upload_is_written_and_named_by_us(tmp_path: Path) -> None:
    saved = save_upload(_jpeg(), upload_dir=tmp_path, book_id=7, slot="front")

    assert saved.filename.startswith("book7-front-")
    assert saved.filename.endswith(".jpg")
    assert "/" not in saved.filename
    assert (tmp_path / saved.filename).is_file()
    assert (saved.width, saved.height) == (400, 600)


def test_exif_and_gps_are_stripped(tmp_path: Path) -> None:
    """SPEC §8: uploads lose metadata. The fixture really had Make and GPS."""
    raw = _jpeg(with_exif=True)

    # Sanity: the input does carry the metadata we claim to remove.
    source = Image.open(io.BytesIO(raw))
    assert source.getexif()[0x010F] == "CameraMaker"
    assert 0x8825 in source.getexif()

    saved = save_upload(raw, upload_dir=tmp_path, book_id=1, slot="front")
    result = Image.open(tmp_path / saved.filename)

    assert result.getexif().get(0x010F) is None
    assert 0x8825 not in result.getexif()


def test_crop_is_applied(tmp_path: Path) -> None:
    saved = save_upload(
        _jpeg(400, 600), upload_dir=tmp_path, book_id=1, slot="spine", crop=(10, 20, 110, 320)
    )

    assert (saved.width, saved.height) == (100, 300)


def test_crop_outside_the_image_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(UploadError, match="outside the image"):
        save_upload(
            _jpeg(400, 600), upload_dir=tmp_path, book_id=1, slot="front", crop=(0, 0, 500, 100)
        )


def test_inverted_crop_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(UploadError, match="rectangle"):
        save_upload(
            _jpeg(), upload_dir=tmp_path, book_id=1, slot="front", crop=(200, 200, 100, 100)
        )


def test_crop_smaller_than_the_minimum_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(UploadError, match="keep at least"):
        save_upload(_jpeg(), upload_dir=tmp_path, book_id=1, slot="front", crop=(0, 0, 10, 10))


def test_giant_images_are_scaled_down(tmp_path: Path) -> None:
    saved = save_upload(_jpeg(3000, 2400), upload_dir=tmp_path, book_id=1, slot="front")

    assert saved.width <= MAX_DIMENSION
    assert saved.height <= MAX_DIMENSION
    assert saved.width == pytest.approx(MAX_DIMENSION, rel=0.01)


def test_transparent_png_survives_and_flattens_for_jpeg(tmp_path: Path) -> None:
    saved = save_upload(_png(), upload_dir=tmp_path, book_id=1, slot="front")

    assert saved.format == "PNG"
    assert Image.open(tmp_path / saved.filename).mode in ("RGBA", "RGB", "P", "LA")


def test_not_an_image_is_a_message_not_a_crash(tmp_path: Path) -> None:
    with pytest.raises(UploadError, match="not a readable image"):
        save_upload(
            b"this is definitely not a picture", upload_dir=tmp_path, book_id=1, slot="front"
        )


def test_empty_upload_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(UploadError, match="empty"):
        save_upload(b"", upload_dir=tmp_path, book_id=1, slot="front")


def test_disallowed_image_type_is_rejected(tmp_path: Path) -> None:
    buffer = io.BytesIO()
    Image.new("RGB", (200, 200), (1, 2, 3)).save(buffer, format="BMP")

    with pytest.raises(UploadError, match="Unsupported image type"):
        save_upload(buffer.getvalue(), upload_dir=tmp_path, book_id=1, slot="front")


def test_oversized_file_is_rejected(tmp_path: Path) -> None:
    from app.uploads import MAX_UPLOAD_BYTES

    with pytest.raises(UploadError, match="the limit is"):
        save_upload(b"0" * (MAX_UPLOAD_BYTES + 1), upload_dir=tmp_path, book_id=1, slot="front")


@pytest.mark.parametrize("slot", ["front", "back", "spine"])
def test_known_slots_are_accepted(tmp_path: Path, slot: str) -> None:
    assert save_upload(_jpeg(), upload_dir=tmp_path, book_id=2, slot=slot).filename.startswith(
        f"book2-{slot}-"
    )


def test_unknown_slot_never_reaches_the_filesystem(tmp_path: Path) -> None:
    with pytest.raises(UploadError, match="Unknown photo slot"):
        save_upload(_jpeg(), upload_dir=tmp_path, book_id=1, slot="../../etc/passwd")


def test_read_and_delete_refuse_any_path_but_a_bare_name(tmp_path: Path) -> None:
    saved = save_upload(_jpeg(), upload_dir=tmp_path, book_id=1, slot="front")

    assert read_upload(tmp_path, saved.filename) is not None
    assert read_upload(tmp_path, "../colophon.sqlite3") is None
    assert read_upload(tmp_path, "/etc/passwd") is None
    assert read_upload(tmp_path, "") is None

    assert delete_upload(tmp_path, "../nope.jpg") is False
    assert delete_upload(tmp_path, saved.filename) is True
    assert delete_upload(tmp_path, saved.filename) is False


def test_partial_file_is_never_left_behind(tmp_path: Path) -> None:
    """The write goes to a .part sibling and is renamed, so nothing dangles."""
    saved = save_upload(_jpeg(), upload_dir=tmp_path, book_id=1, slot="front")

    assert list(tmp_path.glob("*.part")) == []
    assert (tmp_path / saved.filename).is_file()
