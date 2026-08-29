# The first real book typed into this app was 700 mm wide — a centimetre
# entered as millimetres. SPEC §6 draws each spine at its true relative size,
# so a wrong dimension quietly distorts the whole shelf and deserves a stop
# and a question. Not a rejection: the row stays valid, it is simply not
# written until the same submit returns with confirm_oversize=1. These tests
# pin both halves of that handshake on create and update, and the boundary,
# so the next refactor cannot turn the warning into an error (or silently
# drop the confirmation).
from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import text

LARGER_THAN_A_BOOK = "larger than a book usually is"


def _book_count() -> int:
    # Imported inside the helpers: the fixture rebinds db_module.SessionLocal,
    # and a module-level `from app.db import SessionLocal` would pin the
    # pre-patch factory and read the wrong database.
    from app.db import SessionLocal

    with SessionLocal() as session:
        return int(session.execute(text("SELECT count(*) FROM books")).scalar_one())


def _stored(book_id: int, column: str) -> int | None:
    """The column straight off the row, so a test asserts what was written,
    not what was rendered."""
    from app.db import SessionLocal

    with SessionLocal() as session:
        return session.execute(
            text(f"SELECT {column} FROM books WHERE id = :i"),
            {"i": book_id},
        ).scalar_one()


def _create(seeded_client: TestClient, **fields: str) -> int:
    response = seeded_client.post("/book/new", data=fields, follow_redirects=False)
    assert response.status_code == 303
    return int(response.headers["location"].split("/")[2])


def test_a_700_mm_width_saves_nothing_and_asks_the_question(
    seeded_client: TestClient,
) -> None:
    before = _book_count()

    response = seeded_client.post("/book/new", data={"title": "Fat Paperback", "width_mm": "700"})

    # A warning, not an error: valid values, just not written yet.
    assert response.status_code == 200
    assert "Width 700 mm" in response.text
    assert LARGER_THAN_A_BOOK in response.text
    assert _book_count() == before
    # The re-rendered form carries the answer, so pressing the same Save
    # button a second time is itself the confirmation.
    assert 'name="confirm_oversize" value="1"' in response.text
    # And it shows what was typed, not a blank or the old row.
    assert 'value="700"' in response.text


def test_confirming_a_700_mm_width_saves_it(seeded_client: TestClient) -> None:
    before = _book_count()

    response = seeded_client.post(
        "/book/new",
        data={"title": "Fat Paperback", "width_mm": "700", "confirm_oversize": "1"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert _book_count() == before + 1
    new_id = int(response.headers["location"].split("/")[2])
    assert _stored(new_id, "width_mm") == 700


def test_a_900_mm_height_on_update_asks_before_touching_the_row(
    seeded_client: TestClient,
) -> None:
    book_id = _create(seeded_client, title="Steady Book", height_mm="200")

    response = seeded_client.post(
        f"/book/{book_id}/edit", data={"title": "Steady Book", "height_mm": "900"}
    )

    assert response.status_code == 200
    assert "Height 900 mm" in response.text
    assert LARGER_THAN_A_BOOK in response.text
    assert _stored(book_id, "height_mm") == 200
    assert 'name="confirm_oversize" value="1"' in response.text
    assert 'value="900"' in response.text


def test_confirming_a_900_mm_height_on_update_saves_it(seeded_client: TestClient) -> None:
    book_id = _create(seeded_client, title="Steady Book", height_mm="200")

    response = seeded_client.post(
        f"/book/{book_id}/edit",
        data={"title": "Steady Book", "height_mm": "900", "confirm_oversize": "1"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert _stored(book_id, "height_mm") == 900


def test_thickness_is_covered_by_the_same_rule(seeded_client: TestClient) -> None:
    before = _book_count()

    warning = seeded_client.post("/book/new", data={"title": "Slab", "thickness_mm": "600"})
    assert warning.status_code == 200
    assert "Thickness 600 mm" in warning.text
    assert _book_count() == before

    saved = seeded_client.post(
        "/book/new",
        data={"title": "Slab", "thickness_mm": "600", "confirm_oversize": "1"},
        follow_redirects=False,
    )
    assert saved.status_code == 303
    assert _stored(int(saved.headers["location"].split("/")[2]), "thickness_mm") == 600


def test_an_ordinary_book_needs_no_confirmation(seeded_client: TestClient) -> None:
    before = _book_count()

    response = seeded_client.post(
        "/book/new",
        data={"title": "Normal Book", "width_mm": "130", "height_mm": "200", "thickness_mm": "35"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert _book_count() == before + 1
    page = seeded_client.get(response.headers["location"])
    assert LARGER_THAN_A_BOOK not in page.text
    # No half-asked question lurking as a hidden field either.
    assert 'name="confirm_oversize"' not in page.text


def test_exactly_the_limit_passes_and_one_more_millimetre_asks(
    seeded_client: TestClient,
) -> None:
    """The rule is strictly greater than MAX_PLAUSIBLE_MM, so the boundary
    belongs to the plausible side."""
    before = _book_count()

    at_limit = seeded_client.post(
        "/book/new", data={"title": "Exactly Fifty", "height_mm": "500"}, follow_redirects=False
    )
    assert at_limit.status_code == 303
    assert _book_count() == before + 1

    one_over = seeded_client.post("/book/new", data={"title": "One Over", "height_mm": "501"})
    assert one_over.status_code == 200
    assert "501" in one_over.text
    assert _book_count() == before + 1


def test_negative_and_non_numeric_dimensions_keep_the_existing_rejection(
    seeded_client: TestClient,
) -> None:
    """These are errors, not warnings: 400 with the message the app already
    used, and confirm_oversize waives nothing for them."""
    before = _book_count()

    negative = seeded_client.post("/book/new", data={"title": "Inside Out", "width_mm": "-70"})
    assert negative.status_code == 400
    assert "greater than zero" in negative.text
    assert LARGER_THAN_A_BOOK not in negative.text

    words = seeded_client.post("/book/new", data={"title": "Handwritten", "width_mm": "fat"})
    assert words.status_code == 400
    assert "whole number" in words.text

    confirmed = seeded_client.post(
        "/book/new",
        data={"title": "Ignoring", "width_mm": "-70", "confirm_oversize": "1"},
        follow_redirects=False,
    )
    assert confirmed.status_code == 400
    assert _book_count() == before
