# Add / edit / photo endpoints (M4). Uploads go through a real multipart POST
# with a Pillow-generated image, and the provider is stubbed, so these exercise
# the whole path without a network or a browser.
from __future__ import annotations

import io
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app import lookup as lookup_module
from app.settings import settings


@pytest.fixture()
def uploads_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Keep test files out of the project's real uploads/ directory."""
    target = tmp_path / "uploads"
    monkeypatch.setattr(settings, "upload_dir", str(target))
    return target


def _jpeg(width: int = 500, height: int = 700) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (200, 30, 30)).save(buffer, format="JPEG")
    return buffer.getvalue()


def _book_count(client: TestClient) -> int:
    from app.db import SessionLocal

    with SessionLocal() as session:
        return int(session.execute(text("SELECT count(*) FROM books")).scalar_one())


def _owner_of(client: TestClient, book_id: int) -> int:
    from app.db import SessionLocal

    with SessionLocal() as session:
        return int(
            session.execute(
                text("SELECT owner_id FROM books WHERE id = :i"), {"i": book_id}
            ).scalar_one()
        )


def test_new_book_form_renders(seeded_client: TestClient) -> None:
    response = seeded_client.get("/book/new")

    assert response.status_code == 200
    assert 'name="title"' in response.text
    assert "Add a book" in response.text


def test_creating_a_book_applies_the_defaults(seeded_client: TestClient) -> None:
    before = _book_count(seeded_client)
    response = seeded_client.post(
        "/book/new", data={"title": "Artemis", "authors": "Andy Weir"}, follow_redirects=False
    )

    assert response.status_code == 303
    assert response.headers["location"].endswith("saved=1")
    assert _book_count(seeded_client) == before + 1

    new_id = int(response.headers["location"].split("/")[2])
    from app.db import SessionLocal

    with SessionLocal() as session:
        row = session.execute(
            text("SELECT ownership_status, reading_state, room_id FROM books WHERE id = :i"),
            {"i": new_id},
        ).first()
    assert row[0] == "owned"
    assert row[1] == "unread"


def test_authors_are_stored_in_order(seeded_client: TestClient) -> None:
    response = seeded_client.post(
        "/book/new",
        data={
            "title": "Design Patterns: Test Edition",
            "authors": "Erich Gamma, Richard Helm, John Vlissides",
        },
        follow_redirects=False,
    )
    new_id = int(response.headers["location"].split("/")[2])

    from app.db import SessionLocal

    with SessionLocal() as session:
        names = list(
            session.execute(
                text(
                    "SELECT a.name FROM book_authors ba JOIN authors a ON a.id = ba.author_id"
                    " WHERE ba.book_id = :i ORDER BY ba.position"
                ),
                {"i": new_id},
            ).scalars()
        )
    assert names == ["Erich Gamma", "Richard Helm", "John Vlissides"]


def test_a_bad_isbn_is_rejected_without_saving(seeded_client: TestClient) -> None:
    before = _book_count(seeded_client)
    response = seeded_client.post("/book/new", data={"title": "Fake", "isbn": "978-000-000-000-7"})

    assert response.status_code == 400
    assert "not valid" in response.text
    assert _book_count(seeded_client) == before


def test_a_missing_title_is_rejected(seeded_client: TestClient) -> None:
    response = seeded_client.post("/book/new", data={"title": "   "})

    assert response.status_code == 400
    assert "needs a title" in response.text


def test_a_series_number_without_a_series_is_rejected(seeded_client: TestClient) -> None:
    response = seeded_client.post("/book/new", data={"title": "Orphan", "series_position": "3.5"})

    assert response.status_code == 400
    assert "series name" in response.text


def test_a_bad_dimension_is_rejected(seeded_client: TestClient) -> None:
    response = seeded_client.post("/book/new", data={"title": "Flat", "height_mm": "-20"})

    assert response.status_code == 400
    assert "greater than zero" in response.text


def test_a_duplicate_saves_anyway_and_says_so(seeded_client: TestClient) -> None:
    """D3 is warn-only: the copy is real, the warning is a message."""
    unique = "The Ledger of Nine Coppers"  # not in the M2 fixture
    first = seeded_client.post("/book/new", data={"title": unique}, follow_redirects=False)
    assert "duplicate" not in first.headers["location"]

    second = seeded_client.post("/book/new", data={"title": unique}, follow_redirects=False)

    assert second.status_code == 303
    assert "saved=1" in second.headers["location"]
    assert "duplicate=1" in second.headers["location"]


def test_a_book_that_is_not_here_loses_its_room(seeded_client: TestClient) -> None:
    """D2: a room is where a copy stands; sold/wanted/gifted/borrowed stand nowhere."""
    from app.db import SessionLocal

    with SessionLocal() as session:
        room_id = int(session.execute(text("SELECT id FROM rooms LIMIT 1")).scalar_one())

    response = seeded_client.post(
        "/book/new",
        data={"title": "Sold Copy", "ownership_status": "sold", "room_id": str(room_id)},
        follow_redirects=False,
    )
    new_id = int(response.headers["location"].split("/")[2])

    with SessionLocal() as session:
        stored_room = session.execute(
            text("SELECT room_id FROM books WHERE id = :i"), {"i": new_id}
        ).scalar_one()
    assert stored_room is None


def test_edit_form_is_prefilled_and_updates(seeded_client: TestClient) -> None:
    created = seeded_client.post(
        "/book/new", data={"title": "Old Title", "page_count": "300"}, follow_redirects=False
    )
    new_id = int(created.headers["location"].split("/")[2])

    form = seeded_client.get(f"/book/{new_id}/edit")
    assert 'value="Old Title"' in form.text

    updated = seeded_client.post(
        f"/book/{new_id}/edit",
        data={"title": "New Title", "page_count": "320"},
        follow_redirects=False,
    )
    assert updated.status_code == 303

    from app.db import SessionLocal

    with SessionLocal() as session:
        row = session.execute(
            text("SELECT title, page_count FROM books WHERE id = :i"), {"i": new_id}
        ).first()
    assert row[0] == "New Title"
    assert row[1] == 320


def test_another_users_book_answers_404(seeded_client: TestClient) -> None:
    """Not 403: an id must not leak the existence of someone else's book."""
    from app.db import SessionLocal

    with SessionLocal() as session:
        session.execute(
            text("INSERT INTO users (username, password_hash) VALUES ('intruder', 'x')")
        )
        other_id = int(session.execute(text("SELECT last_insert_rowid()")).scalar_one())
        session.execute(
            text("INSERT INTO books (owner_id, title) VALUES (:o, 'Someone else')"), {"o": other_id}
        )
        book_id = int(session.execute(text("SELECT last_insert_rowid()")).scalar_one())
        session.commit()

    assert seeded_client.get(f"/book/{book_id}/edit").status_code == 404
    assert (
        seeded_client.post(f"/book/{book_id}/edit", data={"title": "hijacked"}).status_code == 404
    )
    assert seeded_client.get(f"/book/{book_id}/photos/front").status_code == 404
    assert _owner_of(seeded_client, book_id) == other_id


def test_photo_upload_stores_serves_and_replaces(
    seeded_client: TestClient, uploads_dir: Path
) -> None:
    created = seeded_client.post("/book/new", data={"title": "Photo Book"}, follow_redirects=False)
    new_id = int(created.headers["location"].split("/")[2])

    upload = seeded_client.post(
        f"/book/{new_id}/photos",
        data={"slot": "front"},
        files={"file": ("cover.jpg", _jpeg(), "image/jpeg")},
        follow_redirects=False,
    )
    assert upload.status_code == 303
    assert "photo_saved=1" in upload.headers["location"]

    served = seeded_client.get(f"/book/{new_id}/photos/front")
    assert served.status_code == 200
    assert served.headers["content-type"] == "image/jpeg"
    assert served.content[:2] == b"\xff\xd8"

    # Second upload replaces the first file rather than orphaning it.
    seeded_client.post(
        f"/book/{new_id}/photos",
        data={
            "slot": "front",
            "crop_left": "0",
            "crop_top": "0",
            "crop_right": "100",
            "crop_bottom": "200",
        },
        files={"file": ("cover2.jpg", _jpeg(500, 700), "image/jpeg")},
        follow_redirects=False,
    )
    from app.db import SessionLocal

    with SessionLocal() as session:
        filename = session.execute(
            text("SELECT upload_filename FROM photos WHERE book_id = :i AND slot = 'front'"),
            {"i": new_id},
        ).scalar_one()
    assert filename is not None
    assert (uploads_dir / filename).is_file()
    assert len(list(uploads_dir.glob("*-front-*"))) == 1


def test_photo_upload_rejects_a_non_image(seeded_client: TestClient, uploads_dir: Path) -> None:
    created = seeded_client.post("/book/new", data={"title": "No Photo"}, follow_redirects=False)
    new_id = int(created.headers["location"].split("/")[2])

    response = seeded_client.post(
        f"/book/{new_id}/photos",
        data={"slot": "front"},
        files={"file": ("notes.txt", b"just text", "image/jpeg")},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "photo_error=1" in response.headers["location"]
    from app.db import SessionLocal

    with SessionLocal() as session:
        count = session.execute(
            text("SELECT count(*) FROM photos WHERE book_id = :i"), {"i": new_id}
        ).scalar_one()
    assert count == 0


def test_photo_delete_removes_the_row_and_the_file(
    seeded_client: TestClient, uploads_dir: Path
) -> None:
    created = seeded_client.post(
        "/book/new", data={"title": "Temporary Art"}, follow_redirects=False
    )
    new_id = int(created.headers["location"].split("/")[2])
    seeded_client.post(
        f"/book/{new_id}/photos",
        data={"slot": "spine"},
        files={"file": ("spine.jpg", _jpeg(), "image/jpeg")},
        follow_redirects=False,
    )

    seeded_client.post(f"/book/{new_id}/photos/spine/delete", follow_redirects=False)

    from app.db import SessionLocal

    with SessionLocal() as session:
        remaining = session.execute(
            text("SELECT count(*) FROM photos WHERE book_id = :i AND slot = 'spine'"), {"i": new_id}
        ).scalar_one()
    assert remaining == 0
    assert list(uploads_dir.glob("*-spine-*")) == []
    assert seeded_client.get(f"/book/{new_id}/photos/spine").status_code == 404


def test_an_unknown_slot_is_not_stored(seeded_client: TestClient, uploads_dir: Path) -> None:
    created = seeded_client.post("/book/new", data={"title": "Slotless"}, follow_redirects=False)
    new_id = int(created.headers["location"].split("/")[2])

    response = seeded_client.post(
        f"/book/{new_id}/photos",
        data={"slot": "../../evil"},
        files={"file": ("x.jpg", _jpeg(), "image/jpeg")},
        follow_redirects=False,
    )

    assert "photo_error=1" in response.headers["location"]


def test_provider_cover_is_cached_when_a_candidate_is_adopted(
    seeded_client: TestClient, uploads_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T6: provider bytes are stored under our name, separate from uploads."""
    monkeypatch.setattr(
        lookup_module,
        "CLIENT_FACTORY",
        lambda **kwargs: httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, content=_jpeg(200, 300))
            ),
            **kwargs,
        ),
    )

    created = seeded_client.post(
        "/book/new",
        data={"title": "Adopted Cover", "cover_url": "https://covers.openlibrary.org/b/id/9-L.jpg"},
        follow_redirects=False,
    )
    new_id = int(created.headers["location"].split("/")[2])

    from app.db import SessionLocal

    with SessionLocal() as session:
        row = session.execute(
            text(
                "SELECT provider_filename, upload_filename FROM photos"
                " WHERE book_id = :i AND slot = 'front'"
            ),
            {"i": new_id},
        ).first()
    assert row is not None
    assert row[0] is not None and row[1] is None
    assert (uploads_dir / row[0]).is_file()


def test_a_failing_provider_does_not_break_the_save(
    seeded_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        lookup_module,
        "CLIENT_FACTORY",
        lambda **kwargs: httpx.Client(transport=httpx.MockTransport(_refuse), **kwargs),
    )

    created = seeded_client.post(
        "/book/new",
        data={
            "title": "No Art Available",
            "cover_url": "https://covers.openlibrary.org/b/id/1-L.jpg",
        },
        follow_redirects=False,
    )

    assert created.status_code == 303
    new_id = int(created.headers["location"].split("/")[2])
    from app.db import SessionLocal

    with SessionLocal() as session:
        assert (
            session.execute(
                text("SELECT count(*) FROM books WHERE id = :i"), {"i": new_id}
            ).scalar_one()
            == 1
        )


def _refuse(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("provider down")


def test_lookup_candidates_are_offered_in_the_form(
    seeded_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = {
        "docs": [
            {
                "key": "/works/OL1A",
                "title": "Leviathan Wakes",
                "author_name": ["James S.A. Corey"],
                "publisher": ["Orbit"],
                "publish_year": [2011],
                "isbn": ["9780441018369"],
                "cover_i": 8033155,
            }
        ]
    }
    monkeypatch.setattr(
        lookup_module,
        "CLIENT_FACTORY",
        lambda **kwargs: httpx.Client(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload)),
            **kwargs,
        ),
    )

    response = seeded_client.get("/book/new?q=Leviathan+Wakes")

    assert response.status_code == 200
    assert "Leviathan Wakes" in response.text
    assert "Use this" in response.text
    assert "9780441018369" in response.text


def test_a_failed_lookup_shows_the_reason(
    seeded_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        lookup_module,
        "CLIENT_FACTORY",
        lambda **kwargs: httpx.Client(transport=httpx.MockTransport(_refuse), **kwargs),
    )

    response = seeded_client.get("/book/new?q=anything")

    assert response.status_code == 200
    assert "Open Library is unreachable" in response.text


def test_prefill_query_parameters_fill_the_blank_form(seeded_client: TestClient) -> None:
    response = seeded_client.get(
        "/book/new?title=Prefilled&authors=Some%20Author&isbn=9780441018369"
    )

    assert 'value="Prefilled"' in response.text
    assert 'value="Some Author"' in response.text
    assert "9780441018369" in response.text


def test_shelf_links_to_add_a_book(seeded_client: TestClient) -> None:
    assert 'href="/book/new"' in seeded_client.get("/").text
