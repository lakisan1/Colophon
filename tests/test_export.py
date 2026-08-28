# CSV and JSON export (SPEC §11; PLAN M6 verify: "export round-trips through a parser").
#
# The export is the only door data leaves Colophon — there is no import (D13), so
# the file a user downloads today is the backup they rebuild from in five years.
# A broken quote or mangled UTF-8 title is then data loss, not a cosmetic glitch,
# and substring matching cannot prove otherwise: the only honest test is to read
# the downloaded document back with the very parser (csv.reader, json.loads) a
# future reader would use, and compare values rather than bytes of a document.
from __future__ import annotations

import csv
import io
import json
import re
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from app.export import EXPORT_FIELDS

# A title carrying everything a CSV writer can get wrong: the separator, a quote,
# a line break inside a field, and Serbian letters that must survive UTF-8.
NASTY_TITLE = 'Kiš, "Gramatika", jedan\ndrugi — ćuć'

EXPECTED_HEADERS = [header for _key, header in EXPORT_FIELDS]


def owner_id(engine: Engine) -> int:
    """The seeded demo user's id — read from the fixture, never hardcoded."""
    with engine.connect() as conn:
        return int(conn.execute(text("SELECT id FROM users WHERE username = 'demo'")).scalar_one())


def parsed_csv(response) -> list[dict[str, str]]:
    """The body again, as data — parsed by the same module that wrote it."""
    return list(csv.DictReader(io.StringIO(response.text)))


def parsed_json(response) -> list[dict[str, Any]]:
    return json.loads(response.text)


def insert_book(
    engine: Engine,
    *,
    title: str = NASTY_TITLE,
    price: float | None = 1234.56,
    isbn: str | None = None,
) -> int:
    """One row written straight through SQL — its title being the worst case a
    text export can meet.

    SQLAlchemy 2.0 refuses `result.inserted_primary_key` on a `text()` statement,
    so the id comes back from SQLite itself, on the same connection — which is
    exactly what makes `last_insert_rowid()` refer to this insert."""
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO books (owner_id, title, ownership_status, reading_state, price, isbn)"
                " VALUES (:o, :title, 'owned', 'finished', :price, :isbn)"
            ),
            {"o": owner_id(engine), "title": title, "price": price, "isbn": isbn},
        )
        return int(conn.execute(text("SELECT last_insert_rowid()")).scalar_one())


# ------------------------------------------------------------------ the shape


def test_the_csv_header_is_exactly_the_declared_fields(
    seeded_db: tuple[TestClient, Engine],
) -> None:
    """The header is the contract a spreadsheet reads; a renamed or reordered
    column breaks someone's old export silently."""
    client, _engine = seeded_db

    response = client.get("/export.csv")

    assert response.status_code == 200
    header = next(csv.reader(io.StringIO(response.text)))
    assert header == EXPECTED_HEADERS


def test_an_unfiltered_export_holds_every_book_the_owner_has(
    seeded_db: tuple[TestClient, Engine],
) -> None:
    """SPEC §11 exports the library, not the shelf you happen to be looking at:
    the export scope spans all five ownership statuses, so the count must match
    the books table and not merely the owned rows."""
    client, engine = seeded_db

    rows = parsed_csv(client.get("/export.csv"))

    with engine.connect() as conn:
        total = conn.execute(
            text("SELECT count(*) FROM books WHERE owner_id = :o"), {"o": owner_id(engine)}
        ).scalar_one()
        owned = conn.execute(
            text("SELECT count(*) FROM books WHERE owner_id = :o AND ownership_status = 'owned'"),
            {"o": owner_id(engine)},
        ).scalar_one()

    assert len(rows) == total
    assert owned < total  # otherwise the assertion above proves nothing about scope


def test_the_json_export_holds_the_same_rows_as_the_csv(
    seeded_db: tuple[TestClient, Engine],
) -> None:
    """Two serialisations of one query: if they ever disagree, one of them is
    lying about what the library contains."""
    client, engine = seeded_db

    csv_rows = parsed_csv(client.get("/export.csv"))
    json_rows = parsed_json(client.get("/export.json"))

    with engine.connect() as conn:
        total = conn.execute(
            text("SELECT count(*) FROM books WHERE owner_id = :o"), {"o": owner_id(engine)}
        ).scalar_one()

    assert len(json_rows) == len(csv_rows) == total
    # Same books in the same order. The cast is the point: CSV is all text, JSON
    # is typed, and a reader that joins the two files keys on this column.
    assert [row["id"] for row in json_rows] == [int(row["id"]) for row in csv_rows]


# ------------------------------------------------------------- the round trip


def test_a_hostile_title_survives_the_csv_round_trip(
    seeded_db: tuple[TestClient, Engine],
) -> None:
    """Comma, double quote, embedded newline, ć ć š ž đ: the four ways a naive
    writer produces a file that reads back wrong. Parsed with DictReader, the
    title must be byte-identical — one cell shift and every column after it lies.
    """
    client, engine = seeded_db
    book_id = insert_book(engine)

    response = client.get("/export.csv")

    rows = parsed_csv(response)
    row = next(row for row in rows if row["id"] == str(book_id))
    assert row["title"] == NASTY_TITLE
    # The row must not have been split by the comma or the newline.
    assert row["ownership_status"] == "owned"
    assert row["reading_state"] == "finished"
    assert row["price"] == "1234.56"
    assert None not in row  # a short row would hand DictReader trailing Nones


def test_a_hostile_title_survives_the_json_round_trip(
    seeded_db: tuple[TestClient, Engine],
) -> None:
    """The same row through the other serialiser: `ensure_ascii=False` means the
    Serbian letters ride out literally, so the parse has to give them back."""
    client, engine = seeded_db
    book_id = insert_book(engine)

    response = client.get("/export.json")

    row = next(row for row in parsed_json(response) if row["id"] == book_id)
    assert row["title"] == NASTY_TITLE
    # Both exports must agree, or the two files are two different libraries.
    csv_row = next(r for r in parsed_csv(client.get("/export.csv")) if r["id"] == str(book_id))
    assert csv_row["title"] == row["title"]


def test_json_keeps_machine_readable_types(seeded_db: tuple[TestClient, Engine]) -> None:
    """A reader will do arithmetic on price and compare ISBNs as text: a number
    that arrives as a string, or an ISBN serialised as a number that lost its
    leading zero, is silent corruption of the download.

    The demo fixture prices its books but carries no ISBNs at all, so this writes
    the one row the assertion needs instead of trusting data it cannot see."""
    client, engine = seeded_db
    book_id = insert_book(engine, title="Gramatika srpskog jezika", isbn="9788679460739")

    rows = parsed_json(client.get("/export.json"))

    prices = [row["price"] for row in rows if row["price"] is not None]
    assert prices, "the fixture should contain priced books"
    assert all(isinstance(price, float) for price in prices)

    written = next(row for row in rows if row["id"] == book_id)
    assert written["isbn"] == "9788679460739"
    assert written["price"] == 1234.56
    # Everywhere else: a string or a null — never a number, never an empty string.
    assert all(row["isbn"] is None or isinstance(row["isbn"], str) for row in rows)


# ------------------------------------------------------------------- filters


def test_the_author_filter_narrows_the_export(seeded_db: tuple[TestClient, Engine]) -> None:
    """Export what I am looking at: the URL filter must reach the export query.
    Checked against a count from the database, because a filter that is quietly
    ignored still looks like it worked when the file is merely smaller."""
    client, engine = seeded_db
    with engine.connect() as conn:
        author = conn.execute(
            text(
                """
                SELECT a.id, a.name, count(*) AS c
                FROM authors a
                JOIN book_authors ba ON ba.author_id = a.id
                JOIN books b ON b.id = ba.book_id AND b.owner_id = :o
                GROUP BY a.id ORDER BY c DESC LIMIT 1
                """
            ),
            {"o": owner_id(engine)},
        ).first()
        expected = conn.execute(
            text(
                "SELECT count(*) FROM books b WHERE b.owner_id = :o"
                " AND b.id IN (SELECT book_id FROM book_authors WHERE author_id = :a)"
            ),
            {"o": owner_id(engine), "a": author[0]},
        ).scalar_one()

    rows = parsed_csv(client.get("/export.csv", params={"author": str(author[0])}))

    assert len(rows) == expected >= 1
    # Every row belongs to that author — compared as a listed name, not as a
    # substring, so "Dan Abnett" cannot be matched by somebody else's "Abnett".
    for row in rows:
        assert author[1] in row["authors"].split("; ")


def test_the_no_room_filter_exports_books_without_a_room(
    seeded_db: tuple[TestClient, Engine],
) -> None:
    """D7: rooms are filters. `room=none` is the one value that is not an id, and
    an empty CSV cell is how a NULL room is written out."""
    client, engine = seeded_db
    with engine.connect() as conn:
        expected = conn.execute(
            text("SELECT count(*) FROM books WHERE owner_id = :o AND room_id IS NULL"),
            {"o": owner_id(engine)},
        ).scalar_one()

    rows = parsed_csv(client.get("/export.csv", params={"room": "none"}))

    assert len(rows) == expected >= 1
    assert all(row["room"] == "" for row in rows)


def test_an_empty_result_is_still_a_valid_file(seeded_db: tuple[TestClient, Engine]) -> None:
    """A filter matching nothing must not produce an empty file: a spreadsheet
    needs the header to know what it opened, and a JSON reader needs an array."""
    client, engine = seeded_db

    csv_response = client.get("/export.csv", params={"year": "1066"})
    json_response = client.get("/export.json", params={"year": "1066"})

    assert parsed_csv(csv_response) == []
    assert list(csv.reader(io.StringIO(csv_response.text))) == [EXPECTED_HEADERS]
    assert parsed_json(json_response) == []

    with engine.connect() as conn:
        assert (
            conn.execute(
                text("SELECT count(*) FROM books WHERE owner_id = :o AND publication_year = 1066"),
                {"o": owner_id(engine)},
            ).scalar_one()
            == 0
        )


# ------------------------------------------------------------------ transport


def test_downloads_are_attachments_with_a_dated_name(
    seeded_db: tuple[TestClient, Engine],
) -> None:
    """SPEC §11 is a download, not a page: without `attachment` the browser would
    render 210 rows of CSV inline instead of saving them."""
    client, _engine = seeded_db

    for path, extension, expected_type in (
        ("/export.csv", "csv", "text/csv"),
        ("/export.json", "json", "application/json"),
    ):
        response = client.get(path)
        assert response.headers["content-type"].startswith(expected_type), path
        disposition = response.headers["content-disposition"]
        assert disposition.startswith("attachment;"), path
        assert re.search(
            rf'filename="colophon-\d{{4}}-\d{{2}}-\d{{2}}\.{extension}"', disposition
        ), path


def test_export_requires_a_signed_in_user(client: TestClient) -> None:
    """SPEC §13: the login screen is the only public surface, and an export is
    the most extractable thing in the app. `client` is the bypass-off app."""
    for path in ("/export.csv", "/export.json"):
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 303, path
        assert response.headers["location"] == "/login", path
