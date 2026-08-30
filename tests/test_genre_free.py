# The free-text genre ("Genre, in your own words") is stored and shown on the
# detail page, and the form offers the reader's existing free-text genres as a
# pick-or-type-new suggestion (same autofill pattern as authors/series/tags).
from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import Engine, text


def _last_book_id() -> int:
    from app.db import SessionLocal

    with SessionLocal() as session:
        return int(
            session.execute(text("SELECT id FROM books ORDER BY id DESC LIMIT 1")).scalar_one()
        )


def test_book_detail_shows_the_free_text_genre(seeded_db: tuple[TestClient, Engine]) -> None:
    client, _engine = seeded_db
    client.post("/book/new", data={"title": "Cozy", "genre_free_text": "cozy mystery"})
    book_id = _last_book_id()

    body = client.get(f"/book/{book_id}").text
    assert "Genre, in your own words" in body
    assert "cozy mystery" in body


def test_form_offers_existing_free_text_genres_as_suggestions(
    seeded_db: tuple[TestClient, Engine],
) -> None:
    client, _engine = seeded_db
    client.post("/book/new", data={"title": "Cozy", "genre_free_text": "cozy mystery"})

    body = client.get("/book/new").text
    assert 'list="genre-free-options"' in body
    assert 'id="genre-free-options"' in body
    assert "cozy mystery" in body
