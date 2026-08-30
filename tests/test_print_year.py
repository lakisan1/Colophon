# Print year (M8): the year this physical copy came off the press.
#
# Publication year answers "when was this edition published"; print year
# answers "when was this copy printed", which differs for later printings.
# These tests pin the same promises as the other bounded fields: a bad year
# is an error naming the range, never a silent clamp; unknown is NULL and the
# page omits the row rather than showing a dash that looks like data.
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError

from tests.test_book_page import text_of


def _created_id(title: str) -> int:
    """Read back a created book's id from the database (the add-book redirect now
    goes to the home page and no longer carries the id in the path)."""
    from sqlalchemy import text as _t

    from app.db import SessionLocal

    with SessionLocal() as session:
        return int(
            session.execute(
                _t("SELECT id FROM books WHERE title = :t ORDER BY id DESC LIMIT 1"),
                {"t": title},
            ).scalar_one()
        )


def _add(client: TestClient, print_year: str | None) -> int:
    data = {"title": "Print Probe"}
    if print_year is not None:
        data["print_year"] = print_year
    response = client.post("/book/new", data=data, follow_redirects=False)
    assert response.status_code == 303
    return _created_id("Print Probe")


def _stored(engine: Engine, book_id: int) -> int | None:
    with engine.connect() as conn:
        return conn.execute(
            text("SELECT print_year FROM books WHERE id = :i"), {"i": book_id}
        ).scalar()


def test_the_form_renders_a_print_year_field(seeded_client: TestClient) -> None:
    page = text_of(seeded_client.get("/book/new"))

    assert 'name="print_year"' in page
    assert "Print year" in page


def test_a_print_year_is_stored_and_shown_on_the_page(seeded_db) -> None:
    client, engine = seeded_db
    book_id = _add(client, "1972")

    assert _stored(engine, book_id) == 1972
    page = text_of(client.get(f"/book/{book_id}"))
    assert "Print year" in page
    assert "1972" in page


def test_unknown_print_year_is_null_and_not_shown(seeded_db) -> None:
    client, engine = seeded_db
    book_id = _add(client, None)

    assert _stored(engine, book_id) is None
    page = text_of(client.get(f"/book/{book_id}"))
    assert "Print year" not in page


def test_editing_a_book_updates_the_print_year(seeded_db) -> None:
    client, engine = seeded_db
    book_id = _add(client, "1960")

    updated = client.post(
        f"/book/{book_id}/edit",
        data={"title": "Print Probe", "print_year": "1972"},
        follow_redirects=False,
    )
    assert updated.status_code == 303
    assert _stored(engine, book_id) == 1972


def test_a_print_year_outside_the_range_is_rejected_without_saving(seeded_db) -> None:
    client, engine = seeded_db
    before = _add(client, "1960")

    response = client.post(
        "/book/new", data={"title": "Bogus", "print_year": "10000"}, follow_redirects=False
    )

    assert response.status_code == 400
    assert "1 and 9999" in response.text
    assert _stored(engine, before) == 1960


def test_a_non_numeric_print_year_is_rejected(seeded_client: TestClient) -> None:
    response = seeded_client.post(
        "/book/new", data={"title": "Words", "print_year": "sixty"}, follow_redirects=False
    )

    assert response.status_code == 400
    assert "must be a number" in response.text


def test_the_database_check_rejects_a_print_year_of_ten_thousand(seeded_db) -> None:
    _client, engine = seeded_db
    with engine.connect() as conn, pytest.raises(IntegrityError):
        conn.execute(
            text("INSERT INTO books (owner_id, title, print_year) VALUES (1, 'Too new', 10000)")
        )


def test_the_export_carries_the_print_year_column(seeded_db) -> None:
    client, engine = seeded_db
    book_id = _add(client, "1972")

    lines = client.get("/export.csv").text.splitlines()
    header = lines[0].split(",")
    assert "print_year" in header
    col = header.index("print_year")
    probe_row = next(line for line in lines[1:] if "Print Probe" in line)
    assert probe_row.split(",")[col] == "1972"
    assert _stored(engine, book_id) == 1972
