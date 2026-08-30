# Tags: the many-to-many custom labels (D-spec, mirrors authors). A book carries
# any number of tags, a tag is offered as a pick-or-type-new suggestion in the
# form, and the Advanced search can narrow to one tag.
from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import Engine, text


def _last_book_id() -> int:
    from app.db import SessionLocal

    with SessionLocal() as session:
        return int(
            session.execute(text("SELECT id FROM books ORDER BY id DESC LIMIT 1")).scalar_one()
        )


def _tag_names(engine: Engine, book_id: int) -> list[str]:
    with engine.connect() as conn:
        return list(
            conn.execute(
                text(
                    "SELECT t.name FROM book_tags bt JOIN tags t ON t.id = bt.tag_id"
                    " WHERE bt.book_id = :b ORDER BY t.name"
                ),
                {"b": book_id},
            ).scalars()
        )


def _owner(engine: Engine, book_id: int) -> int:
    with engine.connect() as conn:
        return int(
            conn.execute(
                text("SELECT owner_id FROM books WHERE id = :i"), {"i": book_id}
            ).scalar_one()
        )


def test_creating_a_book_stores_tags(seeded_db: tuple[TestClient, Engine]) -> None:
    client, engine = seeded_db
    resp = client.post(
        "/book/new",
        data={"title": "Tagged", "tags": "book club, reference, book club"},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    book_id = _last_book_id()
    assert _tag_names(engine, book_id) == ["book club", "reference"]  # deduped
    owner = _owner(engine, book_id)

    with engine.connect() as conn:
        names = [
            r[0]
            for r in conn.execute(
                text("SELECT name FROM tags WHERE owner_id = :o ORDER BY name"), {"o": owner}
            )
        ]
    assert names == ["book club", "reference"]


def test_book_detail_shows_tags(seeded_db: tuple[TestClient, Engine]) -> None:
    client, _engine = seeded_db
    client.post("/book/new", data={"title": "Tagged", "tags": "book club, reference"})
    book_id = _last_book_id()

    body = client.get(f"/book/{book_id}").text
    assert "Tags" in body
    assert "book club" in body
    assert "reference" in body


def test_form_offers_existing_tags_as_suggestions(seeded_db: tuple[TestClient, Engine]) -> None:
    client, _engine = seeded_db
    client.post("/book/new", data={"title": "Tagged", "tags": "book club"})

    body = client.get("/book/new").text
    assert 'list="tag-options"' in body
    assert 'id="tag-options"' in body
    assert "book club" in body


def test_filtering_by_tag_narrows_the_shelf(seeded_db: tuple[TestClient, Engine]) -> None:
    client, engine = seeded_db
    client.post("/book/new", data={"title": "Tagged", "tags": "book club"})
    book_id = _last_book_id()

    with engine.connect() as conn:
        tag_id = int(
            conn.execute(
                text(
                    "SELECT t.id FROM tags t JOIN book_tags bt ON bt.tag_id = t.id"
                    " WHERE bt.book_id = :b"
                ),
                {"b": book_id},
            ).scalar_one()
        )

    body = client.get("/", params={"tag": str(tag_id)}).text
    assert body.count("data-book-id=") == 1
    assert "Tagged" in body


def test_editing_a_book_replaces_its_tags(seeded_db: tuple[TestClient, Engine]) -> None:
    client, engine = seeded_db
    client.post("/book/new", data={"title": "Tagged", "tags": "book club, reference"})
    book_id = _last_book_id()

    resp = client.post(
        f"/book/{book_id}/edit",
        data={"title": "Tagged", "tags": "circulated"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert _tag_names(engine, book_id) == ["circulated"]
