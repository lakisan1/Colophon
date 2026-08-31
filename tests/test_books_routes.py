# Add / edit / photo endpoints (M4). Uploads go through a real multipart POST
# with a Pillow-generated image, and the provider is stubbed, so these exercise
# the whole path without a network or a browser.
from __future__ import annotations

import io
import re
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


def _last_book_id() -> int:
    """The id of the book created most recently on this test's database.

    The add-book redirect now goes to the home page (?saved=1) and no longer
    carries the new book's id in the path, so tests read it back from the
    database instead of parsing the Location header.
    """
    from app.db import SessionLocal

    with SessionLocal() as session:
        return int(
            session.execute(text("SELECT id FROM books ORDER BY id DESC LIMIT 1")).scalar_one()
        )


def test_new_book_form_renders(seeded_client: TestClient) -> None:
    response = seeded_client.get("/book/new")

    assert response.status_code == 200
    assert 'name="title"' in response.text
    assert "Add a book" in response.text


def _stub_isbn_lookup(monkeypatch: pytest.MonkeyPatch, payload: dict) -> None:
    """Point lookup.CLIENT_FACTORY at a local MockTransport returning payload."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    monkeypatch.setattr(
        lookup_module,
        "CLIENT_FACTORY",
        lambda **kwargs: httpx.Client(transport=httpx.MockTransport(handler), **kwargs),
    )


def test_book_lookup_returns_json_for_an_isbn(
    seeded_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_isbn_lookup(
        monkeypatch,
        {
            "ISBN:9780140328721": {
                "title": "Fantastic Mr. Fox",
                "authors": [{"name": "Roald Dahl"}],
                "publishers": [{"name": "Puffin"}],
                "publish_date": "October 1, 1988",
                "number_of_pages": 96,
                "languages": [{"key": "/languages/eng"}],
            }
        },
    )

    response = seeded_client.get("/book/lookup", params={"isbn": "978-0140328721"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    data = response.json()
    assert data["found"] is True
    assert data["title"] == "Fantastic Mr. Fox"
    assert data["authors"] == ["Roald Dahl"]
    assert data["publication_year"] == 1988
    assert data["page_count"] == 96
    assert data["language"] == "English"


def test_book_lookup_reports_not_found_for_an_unknown_isbn(
    seeded_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_isbn_lookup(monkeypatch, {})

    response = seeded_client.get("/book/lookup", params={"isbn": "9780000000000"})

    assert response.status_code == 200
    assert response.json() == {"found": False}


def _google_payload() -> dict:
    return {
        "items": [
            {
                "id": "gb1",
                "volumeInfo": {
                    "title": "Tales of the Google",
                    "authors": ["G. B. Author"],
                    "publisher": "GBCo",
                    "publishedDate": "2001-01-01",
                    "pageCount": 200,
                    "language": "en",
                    "industryIdentifiers": [{"type": "ISBN_10", "identifier": "0441478123"}],
                    "imageLinks": {"thumbnail": "https://books.example/thumb.jpg"},
                },
            }
        ]
    }


def _mixed_lookup_handler(request: httpx.Request) -> httpx.Response:
    # OL for the default search host, Google for its own API host.
    if "googleapis" in request.url.host:
        return httpx.Response(200, json=_google_payload())
    return httpx.Response(
        200,
        json={
            "docs": [
                {
                    "key": "/works/OL1",
                    "title": "Tales of the Shelf",
                    "author_name": ["A. Author"],
                    "publish_year": [2001],
                    "num_pages": [210],
                }
            ]
        },
    )


def _install_mixed_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        lookup_module,
        "CLIENT_FACTORY",
        lambda **kw: httpx.Client(transport=httpx.MockTransport(_mixed_lookup_handler), **kw),
    )


def _demo_owner(engine) -> int:
    with engine.connect() as conn:
        return int(conn.execute(text("SELECT id FROM users WHERE username = 'demo'")).scalar_one())


def test_google_books_shows_in_search_when_enabled(
    seeded_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, engine = seeded_db
    owner = _demo_owner(engine)
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT id FROM settings WHERE owner_id = :o"), {"o": owner}
        ).first()
        if row:
            conn.execute(
                text("UPDATE settings SET search_providers = :p WHERE owner_id = :o"),
                {"p": "openlibrary,googlebooks", "o": owner},
            )
        else:
            conn.execute(
                text("INSERT INTO settings (owner_id, search_providers) VALUES (:o, :p)"),
                {"p": "openlibrary,googlebooks", "o": owner},
            )
    monkeypatch.setattr(settings, "google_books_key", "test-key")
    _install_mixed_lookup(monkeypatch)

    html = client.get("/book/new", params={"q": "tales"}).text

    assert "Tales of the Shelf" in html  # Open Library result
    assert "Tales of the Google" in html  # Google Books result, opted in


def test_google_books_is_absent_by_default(seeded_db, monkeypatch: pytest.MonkeyPatch) -> None:
    # search_providers stays at its 'openlibrary' default even though a key is
    # configured: the key makes Google Books possible, the preference makes it asked.
    client, _engine = seeded_db
    monkeypatch.setattr(settings, "google_books_key", "test-key")
    _install_mixed_lookup(monkeypatch)

    html = client.get("/book/new", params={"q": "tales"}).text

    assert "Tales of the Shelf" in html
    assert "Tales of the Google" not in html  # not opted in, so only Open Library


def test_book_lookup_returns_not_found_for_an_empty_isbn(seeded_client: TestClient) -> None:
    response = seeded_client.get("/book/lookup")

    assert response.status_code == 200
    assert response.json() == {"found": False}


def test_creating_a_book_applies_the_defaults(seeded_client: TestClient) -> None:
    before = _book_count(seeded_client)
    response = seeded_client.post(
        "/book/new", data={"title": "Artemis", "authors": "Andy Weir"}, follow_redirects=False
    )

    assert response.status_code == 303
    assert response.headers["location"].endswith("saved=1")
    assert _book_count(seeded_client) == before + 1

    new_id = _last_book_id()
    from app.db import SessionLocal

    with SessionLocal() as session:
        row = session.execute(
            text("SELECT ownership_status, reading_state, room_id FROM books WHERE id = :i"),
            {"i": new_id},
        ).first()
    assert row[0] == "owned"
    assert row[1] == "unread"


def test_authors_are_stored_in_order(seeded_client: TestClient) -> None:
    seeded_client.post(
        "/book/new",
        data={
            "title": "Design Patterns: Test Edition",
            "authors": "Erich Gamma, Richard Helm, John Vlissides",
        },
        follow_redirects=False,
    )
    new_id = _last_book_id()

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

    seeded_client.post(
        "/book/new",
        data={"title": "Sold Copy", "ownership_status": "sold", "room_id": str(room_id)},
        follow_redirects=False,
    )
    new_id = _last_book_id()

    with SessionLocal() as session:
        stored_room = session.execute(
            text("SELECT room_id FROM books WHERE id = :i"), {"i": new_id}
        ).scalar_one()
    assert stored_room is None


def test_edit_form_is_prefilled_and_updates(seeded_client: TestClient) -> None:
    created = seeded_client.post(
        "/book/new", data={"title": "Old Title", "page_count": "300"}, follow_redirects=False
    )
    assert created.status_code == 303
    new_id = _last_book_id()

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
    assert created.status_code == 303
    new_id = _last_book_id()

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
    assert created.status_code == 303
    new_id = _last_book_id()

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
    assert created.status_code == 303
    new_id = _last_book_id()
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
    assert created.status_code == 303
    new_id = _last_book_id()

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
    assert created.status_code == 303
    new_id = _last_book_id()

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
    assert created.status_code == 303
    new_id = _last_book_id()
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


def _created_book_id(client: TestClient, title: str = "Deletable Book") -> int:
    """Create a book and return its id."""
    response = client.post(
        "/book/new",
        data={"title": title, "page_count": "200"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    return _last_book_id()


def _csrf(client: TestClient, page: str) -> str:
    """Read the CSRF token out of an edit page's delete form."""
    html = client.get(page).text
    match = re.search(r'name="csrf_token" value="([^"]*)"', html)
    assert match, "expected a csrf_token input on the edit page"
    return match.group(1)


def test_delete_book_requires_the_account_password(seeded_client: TestClient) -> None:
    book_id = _created_book_id(seeded_client)
    before = _book_count(seeded_client)

    # A wrong password must not delete anything.
    wrong = seeded_client.post(
        f"/book/{book_id}/delete",
        data={
            "csrf_token": _csrf(seeded_client, f"/book/{book_id}/edit"),
            "password": "not-the-demo-password",
            "confirm_delete": "1",
        },
        follow_redirects=False,
    )
    assert wrong.status_code == 400
    assert "Wrong password" in wrong.text
    assert _book_count(seeded_client) == before


def test_delete_book_requires_the_confirmation_checkbox(seeded_client: TestClient) -> None:
    book_id = _created_book_id(seeded_client)
    before = _book_count(seeded_client)

    # Without the "Are you sure?" checkbox the book must not be deleted.
    refused = seeded_client.post(
        f"/book/{book_id}/delete",
        data={
            "csrf_token": _csrf(seeded_client, f"/book/{book_id}/edit"),
            "password": "demo",
            # no confirm_delete
        },
        follow_redirects=False,
    )
    assert refused.status_code == 400
    assert "really want to delete" in refused.text
    assert _book_count(seeded_client) == before


def test_delete_book_with_the_account_password(seeded_client: TestClient) -> None:
    book_id = _created_book_id(seeded_client)
    before = _book_count(seeded_client)

    # The demo account's password is "demo" (see seed data).
    deleted = seeded_client.post(
        f"/book/{book_id}/delete",
        data={
            "csrf_token": _csrf(seeded_client, f"/book/{book_id}/edit"),
            "password": "demo",
            "confirm_delete": "1",
        },
        follow_redirects=False,
    )
    assert deleted.status_code == 303
    assert deleted.headers["location"] == "/"
    assert _book_count(seeded_client) == before - 1
