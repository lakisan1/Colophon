# Statistics: total value of the collection (M8).
#
# The total is the sum of price over books the owner currently holds
# (owned/wanted/borrowed). Sold and gifted books are no longer part of the
# collection, so their price is not counted. A held book with no price adds
# 0.0, and when the whole collection is unpriced the stats page shows no
# value row rather than a meaningless "€0".
from __future__ import annotations

from sqlalchemy import text

from app import db as dbm
from app.money import format_price
from app.stats import dashboard
from tests.test_book_page import text_of


def _owner(engine) -> int:
    with engine.connect() as conn:
        return conn.execute(text("SELECT id FROM users WHERE username = 'demo'")).scalar_one()


def _add(engine, owner: int, price: float | None, status: str = "owned") -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO books (owner_id, title, price, ownership_status)"
                " VALUES (:o, :t, :p, :s)"
            ),
            {"o": owner, "t": f"value probe {status}", "p": price, "s": status},
        )


def _clear(engine, owner: int) -> None:
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM books WHERE owner_id = :o"), {"o": owner})


def _expected(engine, owner: int) -> float:
    with engine.connect() as conn:
        return conn.execute(
            text(
                "SELECT COALESCE(sum(price), 0) FROM books"
                " WHERE owner_id = :o"
                "   AND ownership_status NOT IN ('sold', 'gifted')"
            ),
            {"o": owner},
        ).scalar_one()


def _dashboard_value(owner: int) -> float:
    with dbm.SessionLocal() as session:
        return dashboard(session, owner).value


def test_the_total_counts_held_books_only(seeded_db) -> None:
    _client, engine = seeded_db
    owner = _owner(engine)
    base = _dashboard_value(owner)

    _add(engine, owner, 99.0, "sold")
    _add(engine, owner, 88.0, "gifted")
    assert _dashboard_value(owner) == base  # gone books never count

    _add(engine, owner, 10.0, "owned")
    _add(engine, owner, 5.0, "wanted")
    _add(engine, owner, 20.0, "borrowed")
    _add(engine, owner, None, "owned")  # unpriced held book adds 0.0
    assert _dashboard_value(owner) == base + 35.0
    assert _dashboard_value(owner) == _expected(engine, owner)


def test_the_stats_page_renders_the_total_value(seeded_db) -> None:
    client, engine = seeded_db
    owner = _owner(engine)
    _clear(engine, owner)
    _add(engine, owner, 12.5, "owned")
    _add(engine, owner, 7.0, "wanted")
    _add(engine, owner, 200.0, "sold")  # excluded from the page total

    page = text_of(client.get("/stats"))

    assert "Total value" in page
    assert format_price(_expected(engine, owner), "€") in page
    assert "€200.00" not in page


def test_the_stats_page_shows_no_value_when_everything_is_unpriced(seeded_db) -> None:
    client, engine = seeded_db
    owner = _owner(engine)
    _clear(engine, owner)
    _add(engine, owner, None, "owned")

    page = text_of(client.get("/stats"))

    assert "Total value" not in page
