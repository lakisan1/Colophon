# Condition rating (M8): a 1-5 mark on the physical copy.
#
# Condition is the one attribute a collector compares across the library, and
# free text ("goodish", "VG-") sorts and filters badly, so it is a bounded
# number with the words rendered in the UI. These tests pin three promises:
# (a) the scale is 1..5 and anything else is an error naming the scale, never a
# silent clamp — a wrong rating is worse than no rating; (b) unrated is NULL,
# which is most of the library and must never read as a 1; (c) the database
# CHECK backs the form even if some future writer forgets it.
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


def _add(client: TestClient, rating: str | None) -> int:
    data = {"title": "Condition Probe"}
    if rating is not None:
        data["condition_rating"] = rating
    response = client.post("/book/new", data=data, follow_redirects=False)
    assert response.status_code == 303
    return _created_id("Condition Probe")


def _stored(engine: Engine, book_id: int) -> int | None:
    with engine.connect() as conn:
        return conn.execute(
            text("SELECT condition_rating FROM books WHERE id = :i"), {"i": book_id}
        ).scalar()


def test_the_form_renders_the_whole_scale(seeded_client: TestClient) -> None:
    page = text_of(seeded_client.get("/book/new"))

    # The scale is a single <select> with unrated + 1..5 options.
    start = page.index('name="condition_rating"')
    end = page.index("</select>", start)
    select_block = page[start:end]
    assert select_block.count("<option") == 6  # unrated + 1..5
    for word in ("Not rated", "Poor", "Fair", "Good", "Very good", "As new"):
        assert word in select_block


def test_a_rating_is_stored_and_shown_on_the_page(seeded_db) -> None:
    client, engine = seeded_db
    book_id = _add(client, "4")

    assert _stored(engine, book_id) == 4
    page = text_of(client.get(f"/book/{book_id}"))
    assert "Condition" in page
    assert "4 · Very good" in page


def test_unrated_is_null_and_not_shown(seeded_db) -> None:
    client, engine = seeded_db
    book_id = _add(client, None)

    assert _stored(engine, book_id) is None
    page = text_of(client.get(f"/book/{book_id}"))
    assert "th>Condition" not in page  # the detail row is omitted when unrated


def test_editing_a_book_updates_the_rating(seeded_db) -> None:
    client, engine = seeded_db
    book_id = _add(client, "2")

    updated = client.post(
        f"/book/{book_id}/edit",
        data={"title": "Condition Probe", "condition_rating": "5"},
        follow_redirects=False,
    )
    assert updated.status_code == 303
    assert _stored(engine, book_id) == 5


def test_a_rating_outside_the_scale_is_rejected_without_saving(seeded_db) -> None:
    client, engine = seeded_db
    before = _add(client, "3")

    response = client.post(
        "/book/new", data={"title": "Bogus", "condition_rating": "6"}, follow_redirects=False
    )

    assert response.status_code == 400
    assert "1 (Poor) to 5 (As new)" in response.text
    # Nothing was written: the bogus copy has no id, and the probe copy is intact.
    assert _stored(engine, before) == 3


def test_a_non_numeric_rating_is_rejected(seeded_client: TestClient) -> None:
    response = seeded_client.post(
        "/book/new", data={"title": "Words", "condition_rating": "good"}, follow_redirects=False
    )

    assert response.status_code == 400
    assert "rating from 1 to 5" in response.text


def test_the_database_check_rejects_a_rating_of_seven(seeded_db) -> None:
    _client, engine = seeded_db
    with engine.connect() as conn, pytest.raises(IntegrityError):
        conn.execute(
            text("INSERT INTO books (owner_id, title, condition_rating) VALUES (1, 'Too worn', 7)")
        )


def test_the_export_carries_the_rating_column(seeded_db) -> None:
    client, engine = seeded_db
    book_id = _add(client, "1")

    lines = client.get("/export.csv").text.splitlines()
    header = lines[0].split(",")
    assert "condition_rating" in header
    rating_col = header.index("condition_rating")
    probe_row = next(line for line in lines[1:] if "Condition Probe" in line)
    assert probe_row.split(",")[rating_col] == "1"
    assert _stored(engine, book_id) == 1
