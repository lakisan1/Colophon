# File-handling hardening: photos are reader bytes the server stores and serves
# back, export rows land in spreadsheets, and every filename crosses the
# DB/disk boundary. These tests pin the containment and validation contracts a
# public deployment depends on.
from __future__ import annotations

import io
import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from app import db as db_module
from app.export import _csv_cell
from app.main import create_app
from app.routers.books import _media_type
from app.settings import settings
from app.uploads import UploadError, delete_upload, read_upload, save_upload
from tests.alembic_runner import engine_for, run_alembic


@pytest.fixture()
def auth_engine(monkeypatch: pytest.MonkeyPatch, tmp_path) -> Engine:
    from sqlalchemy.orm import sessionmaker

    db_path = tmp_path / "files.db"
    run_alembic(db_path, "head")
    engine = engine_for(db_path)
    monkeypatch.setattr(
        db_module, "SessionLocal", sessionmaker(bind=engine, expire_on_commit=False)
    )
    monkeypatch.setattr(settings, "dev_auth_bypass", False)
    yield engine
    engine.dispose()


@pytest.fixture()
def auth_client(auth_engine: Engine) -> TestClient:
    return TestClient(create_app())


def _csrf_from(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]*)"', html)
    assert match, "no CSRF token in the page"
    return match.group(1)


def sign_in(client: TestClient, username: str = "admin", password: str = "admin") -> None:
    response = client.post(
        "/login",
        data={
            "username": username,
            "password": password,
            "csrf_token": _csrf_from(client.get("/login").text),
        },
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text


def _png_bytes(width: int = 100, height: int = 100, mode: str = "RGB") -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new(mode, (width, height), color=1 if mode == "1" else (120, 40, 40)).save(
        buffer, format="PNG"
    )
    return buffer.getvalue()


# ---------------------------------------------------------------- containment


def test_upload_filenames_cannot_leave_the_upload_dir(tmp_path) -> None:
    """Filenames come from the database, and a database can be written by
    anything that once had a bug: read_upload and delete_upload must treat a
    path-looking name as junk, not as a location."""
    upload_dir = tmp_path / "photos"
    upload_dir.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("unrelated")
    inside = upload_dir / "real.jpg"
    inside.write_bytes(b"jpeg-bytes")

    assert read_upload(upload_dir, "../secret.txt") is None
    assert read_upload(upload_dir, "..\\secret.txt") is None
    assert read_upload(upload_dir, str(outside)) is None
    assert read_upload(upload_dir, "sub/real.jpg") is None

    for evil in ("../secret.txt", "..\\secret.txt", str(outside), "sub/../real.jpg"):
        assert delete_upload(upload_dir, evil) is False, evil
        assert outside.exists()
        assert inside.exists()

    assert read_upload(upload_dir, "real.jpg") == b"jpeg-bytes"
    assert delete_upload(upload_dir, "real.jpg") is True
    assert not inside.exists()


# ---------------------------------------------------------------- ingest


def test_save_upload_refuses_what_is_not_an_image() -> None:
    for payload in (b"", b"hello world, definitely not a jpeg"):
        with pytest.raises(UploadError):
            save_upload(payload, upload_dir="/tmp", book_id=1, slot="front")


def test_save_upload_refuses_a_decompression_bomb(tmp_path) -> None:
    """A tiny PNG claiming 81M pixels (past the 2x bomb threshold on the 25M
    cap) must come back as an UploadError the form can show, not an exception
    that 500s the route or fills the worker's RAM."""
    with pytest.raises(UploadError):
        save_upload(
            _png_bytes(width=9000, height=9000, mode="1"),
            upload_dir=tmp_path,
            book_id=1,
            slot="front",
        )


def test_save_upload_bounds_the_crop_box(tmp_path) -> None:
    data = _png_bytes(width=100, height=100)
    for crop in ((-10, 0, 50, 50), (60, 0, 40, 50), (0, 0, 500, 500)):
        with pytest.raises(UploadError):
            save_upload(data, upload_dir=tmp_path, book_id=1, slot="front", crop=crop)


def test_saved_photos_carry_no_exif(tmp_path) -> None:
    """A phone photo's GPS and device serial must not survive the ingest — the
    SPEC promises stripped metadata and a public install stores strangers' books."""
    from PIL import Image

    source = io.BytesIO()
    image = Image.new("RGB", (80, 80), color=(10, 10, 10))
    exif = Image.Exif()
    exif[0x010F] = "CamCo"  # the maker tag a phone always sets
    image.save(source, format="JPEG", exif=exif.tobytes())

    saved = save_upload(source.getvalue(), upload_dir=tmp_path, book_id=1, slot="front")
    stored = Image.open(tmp_path / saved.filename)
    assert len(stored.getexif()) == 0


# ---------------------------------------------------------------- serving


def test_a_manipulated_photo_row_cannot_serve_scriptable_content(
    auth_client: TestClient, auth_engine: Engine, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """_media_type decides the Content-Type from the stored filename. Whatever
    a tampered row says, the answer must never be text/html or image/svg+xml —
    bytes the browser would execute."""
    upload_dir = tmp_path / "photos"
    upload_dir.mkdir()
    monkeypatch.setattr(settings, "upload_dir", str(upload_dir))
    (upload_dir / "evil.svg").write_bytes(b"<svg onload=alert(1)>")

    sign_in(auth_client)
    auth_client.post(
        "/book/new",
        data={
            "csrf_token": _csrf_from(auth_client.get("/book/new").text),
            "title": "Probe",
            "ownership_status": "owned",
            "reading_state": "unread",
        },
        follow_redirects=False,
    )
    book_id = auth_engine.connect().execute(text("SELECT max(id) FROM books")).scalar_one()
    from sqlalchemy.orm import sessionmaker

    with sessionmaker(bind=auth_engine)() as session:
        session.execute(
            text(
                "INSERT INTO photos (owner_id, book_id, slot, provider_filename) "
                "VALUES (1, :book, 'front', 'evil.svg')"
            ),
            {"book": book_id},
        )
        session.commit()

    assert _media_type("evil.svg") == "application/octet-stream"
    assert _media_type("x.jpg") == "image/jpeg"

    response = auth_client.get(f"/book/{book_id}/photos/front")
    assert response.status_code == 200
    assert "text/html" not in response.headers["content-type"]
    assert "svg" not in response.headers["content-type"]


# ---------------------------------------------------------------- export


def test_csv_export_neutralises_spreadsheet_formulas() -> None:
    """CWE-1236: a cell starting with =, +, - or @ (also after tab/CR/NL
    whitespace) is stored as a formula by Excel/LibreOffice. The quote-prefix
    keeps it text; the whole cell is prefixed, not just the dangerous head."""
    for value in (
        '=HYPERLINK("http://evil","x")',
        "+1; cmd",
        "-1; cmd",
        "@SUM(1)",
        "\t=cmd",
        "\r=cmd",
        "  =cmd",
    ):
        assert _csv_cell(value).startswith("'"), value
    assert _csv_cell("Plain title") == "Plain title"
    assert _csv_cell(None) == ""
    assert _csv_cell(True) == "true"


def test_the_export_filename_is_static_and_header_safe(
    auth_client: TestClient, auth_engine: Engine
) -> None:
    """A book titled with a carriage return must not be able to add headers:
    the Content-Disposition filename is fixed by the server."""
    sign_in(auth_client)
    auth_client.post(
        "/book/new",
        data={
            "csrf_token": _csrf_from(auth_client.get("/book/new").text),
            "title": "Evil\r\nX-Injected: 1",
            "ownership_status": "owned",
            "reading_state": "unread",
        },
        follow_redirects=False,
    )
    response = auth_client.get("/export.csv")
    assert response.status_code == 200
    disposition = response.headers["content-disposition"]
    assert "\r" not in disposition and "\n" not in disposition
    assert re.fullmatch(r'attachment; filename="colophon-\d{4}-\d{2}-\d{2}\.csv"', disposition)
