# Schema tests. Every test runs the real Alembic migration against its
# own throwaway SQLite file (the dev database is never touched) and asserts the
# rules the locked decisions impose: owner_id everywhere it belongs, decimal
# series positions that sort correctly, one primary genre per book, the curated
# genres actually seeded, and CHECK constraints that refuse nonsense.
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import IntegrityError

from app.formats import FORMAT_KEYS
from tests.alembic_runner import engine_for, run_alembic

EXPECTED_TABLES = {
    "users",
    "books",
    "authors",
    "series",
    "genres",
    "rooms",
    "loans",
    "photos",
    "settings",
    "book_authors",
    "book_genres",
}

# Every table whose rows belong to a user must carry owner_id (D10). users and
# the curated genres are the two documented exceptions.
ROW_OWNING_TABLES = {
    "books",
    "book_authors",
    "book_genres",
    "authors",
    "series",
    "rooms",
    "loans",
    "photos",
    "settings",  # owner_id NULL is the deliberate global row, the column still exists
}


@pytest.fixture(scope="module")
def db(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Engine]:
    db_path = tmp_path_factory.mktemp("m1_schema") / "schema.sqlite3"
    run_alembic(db_path, "head")
    engine = engine_for(db_path)
    yield engine
    engine.dispose()


@pytest.fixture()
def conn(db: Engine) -> Iterator[Connection]:
    """One transaction per test, rolled back so tests cannot contaminate each other."""
    connection = db.connect()
    transaction = connection.begin()
    yield connection
    transaction.rollback()
    connection.close()


def _insert_user(connection: Connection, username: str) -> int:
    result = connection.execute(
        text("INSERT INTO users (username, password_hash) VALUES (:u, 'not-a-real-hash')"),
        {"u": username},
    )
    return int(result.lastrowid)


def _insert_book(connection: Connection, owner_id: int, **columns: object) -> int:
    cols = {"owner_id": owner_id, "title": "Test Book", **columns}
    names = ", ".join(cols)
    binds = ", ".join(f":{name}" for name in cols)
    result = connection.execute(text(f"INSERT INTO books ({names}) VALUES ({binds})"), cols)
    return int(result.lastrowid)


def table_names(connection: Connection) -> set[str]:
    rows = connection.execute(
        text("SELECT name FROM sqlite_master WHERE type = 'table'")
    ).fetchall()
    return {row[0] for row in rows}


def column_names(connection: Connection, table: str) -> set[str]:
    rows = connection.execute(text(f"PRAGMA table_info({table})")).fetchall()
    return {row[1] for row in rows}


def test_upgrade_creates_every_expected_table(conn: Connection) -> None:
    assert EXPECTED_TABLES.issubset(table_names(conn))


def test_books_has_every_m1_column(conn: Connection) -> None:
    expected = {
        "id",
        "owner_id",
        "title",
        "subtitle",
        "isbn",
        "publisher",
        "publication_year",
        "edition",
        "format",
        "page_count",
        "language",
        "room_id",
        "series_id",
        "series_position",
        "primary_genre_notation",
        "genre_free_text",
        "price",
        "where_bought",
        "ownership_status",
        "reading_state",
        "gifted_to",
        "gift_note",
        "is_sprayed_edge",
        "is_signed",
        "inscription",
        "notes",
        "height_mm",
        "width_mm",
        "thickness_mm",
        "same_edition_as_id",
        "created_at",
    }
    assert expected.issubset(column_names(conn, "books"))


def test_owner_id_present_on_every_row_owning_table(conn: Connection) -> None:
    for table in sorted(ROW_OWNING_TABLES):
        assert "owner_id" in column_names(conn, table), f"{table} is missing owner_id (D10)"


def test_series_position_decimals_sort_between_integers_nulls_last(conn: Connection) -> None:
    owner_id = _insert_user(conn, "sorter")
    series_id = conn.execute(
        text("INSERT INTO series (owner_id, name) VALUES (:o, 'Horus Heresy') RETURNING id"),
        {"o": owner_id},
    ).scalar_one()
    for position in (2, 1, 1.5, None):
        _insert_book(conn, owner_id, series_id=series_id, series_position=position)

    # Exactly the D6 expression: nulls last, then position ascending.
    rows = conn.execute(
        text(
            "SELECT series_position FROM books WHERE series_id = :s "
            "ORDER BY series_position IS NULL, series_position"
        ),
        {"s": series_id},
    ).fetchall()
    positions = [row[0] for row in rows]
    assert positions == [1.0, 1.5, 2.0, None]


def test_primary_genre_is_a_single_column_with_a_real_genre_fk(conn: Connection) -> None:
    owner_id = _insert_user(conn, "shelf-owner")
    # The primary genre is ONE column on books — two primaries cannot exist.
    assert "primary_genre_notation" in column_names(conn, "books")
    # Unknown genre notation is refused; known ones are accepted.
    _insert_book(conn, owner_id, primary_genre_notation="FIC009000")
    with pytest.raises(IntegrityError):
        _insert_book(conn, owner_id, primary_genre_notation="FIC999999")


def test_extra_genres_are_link_rows_and_duplicates_are_refused(conn: Connection) -> None:
    owner_id = _insert_user(conn, "multi-genre")
    book_id = _insert_book(conn, owner_id)
    conn.execute(
        text(
            "INSERT INTO book_genres (book_id, genre_notation, owner_id) "
            "VALUES (:b, 'SCI000000', :o)"
        ),
        {"b": book_id, "o": owner_id},
    )
    with pytest.raises(IntegrityError):
        conn.execute(
            text(
                "INSERT INTO book_genres (book_id, genre_notation, owner_id) "
                "VALUES (:b, 'SCI000000', :o)"
            ),
            {"b": book_id, "o": owner_id},
        )
    with pytest.raises(IntegrityError):
        conn.execute(
            text(
                "INSERT INTO book_genres (book_id, genre_notation, owner_id) "
                "VALUES (:b, 'NOPE000000', :o)"
            ),
            {"b": book_id, "o": owner_id},
        )


def test_isbn_column_accepts_only_normalised_13_digit_values(conn: Connection) -> None:
    owner_id = _insert_user(conn, "isbn-owner")
    _insert_book(conn, owner_id, isbn="9780306406157")
    with pytest.raises(IntegrityError):
        _insert_book(conn, owner_id, isbn="0306406152")  # app must normalise first


def test_status_axes_are_two_independent_check_constraints(conn: Connection) -> None:
    owner_id = _insert_user(conn, "status-owner")
    _insert_book(conn, owner_id, ownership_status="gifted", reading_state="dnf")
    _insert_book(conn, owner_id, ownership_status="wanted", reading_state="reading")
    with pytest.raises(IntegrityError):
        _insert_book(conn, owner_id, ownership_status="lended")
    with pytest.raises(IntegrityError):
        _insert_book(conn, owner_id, reading_state="abandoned")


def test_format_check_constraint_matches_app_formats_exactly(conn: Connection) -> None:
    owner_id = _insert_user(conn, "format-owner")
    for key in FORMAT_KEYS:  # every formats.py key must pass the DB CHECK
        _insert_book(conn, owner_id, format=key)
    with pytest.raises(IntegrityError):
        _insert_book(conn, owner_id, format="audiobook")  # D1: not book-shaped, not allowed


def test_photos_keep_provider_and_upload_in_separate_columns(conn: Connection) -> None:
    owner_id = _insert_user(conn, "photo-owner")
    book_id = _insert_book(conn, owner_id)
    conn.execute(
        text(
            "INSERT INTO photos (owner_id, book_id, slot, provider_filename) "
            "VALUES (:o, :b, 'front', 'p1.webp')"
        ),
        {"o": owner_id, "b": book_id},
    )
    conn.execute(
        text(
            "UPDATE photos SET upload_filename = 'user1.webp' WHERE book_id = :b AND slot = 'front'"
        ),
        {"b": book_id},
    )
    # A re-lookup touches only provider_filename (T6): the user's photo survives.
    conn.execute(
        text(
            "UPDATE photos SET provider_filename = 'p2.webp' WHERE book_id = :b AND slot = 'front'"
        ),
        {
            "b": book_id,
        },
    )
    row = conn.execute(
        text(
            "SELECT provider_filename, upload_filename FROM photos "
            "WHERE book_id = :b AND slot = 'front'"
        ),
        {"b": book_id},
    ).fetchone()
    assert (row[0], row[1]) == ("p2.webp", "user1.webp")

    # One row per book per slot; slots outside front/back/spine are refused.
    with pytest.raises(IntegrityError):
        conn.execute(
            text(
                "INSERT INTO photos (owner_id, book_id, slot, provider_filename) "
                "VALUES (:o, :b, 'front', 'p3.webp')"
            ),
            {"o": owner_id, "b": book_id},
        )
    with pytest.raises(IntegrityError):
        conn.execute(
            text(
                "INSERT INTO photos (owner_id, book_id, slot, provider_filename) "
                "VALUES (:o, :b, 'dust-jacket', 'p4.webp')"
            ),
            {"o": owner_id, "b": book_id},
        )
    with pytest.raises(IntegrityError):
        conn.execute(
            text("INSERT INTO photos (owner_id, book_id, slot) VALUES (:o, :b, 'back')"),
            {"o": owner_id, "b": book_id},
        )


def test_authors_are_ordered_per_book_and_sorted_by_sort_key(conn: Connection) -> None:
    owner_id = _insert_user(conn, "author-owner")
    banks = conn.execute(
        text(
            "INSERT INTO authors (owner_id, name, sort_key) "
            "VALUES (:o, 'Iain M. Banks', 'Banks, Iain M.') RETURNING id"
        ),
        {"o": owner_id},
    ).scalar_one()
    reynolds = conn.execute(
        text(
            "INSERT INTO authors (owner_id, name, sort_key) "
            "VALUES (:o, 'Alastair Reynolds', 'Reynolds, Alastair') RETURNING id"
        ),
        {"o": owner_id},
    ).scalar_one()
    book_id = _insert_book(conn, owner_id, title="The Hydrogen Sonata")
    conn.execute(
        text(
            "INSERT INTO book_authors (book_id, author_id, owner_id, position) "
            "VALUES (:b, :a, :o, 1)"
        ),
        {"b": book_id, "a": banks, "o": owner_id},
    )
    conn.execute(
        text(
            "INSERT INTO book_authors (book_id, author_id, owner_id, position) "
            "VALUES (:b, :a, :o, 2)"
        ),
        {"b": book_id, "a": reynolds, "o": owner_id},
    )
    with pytest.raises(IntegrityError):  # two first authors is not a thing
        conn.execute(
            text(
                "INSERT INTO book_authors (book_id, author_id, owner_id, position) "
                "VALUES (:b, :a, :o, 1)"
            ),
            {"b": book_id, "a": reynolds, "o": owner_id},
        )
    # The D6 author sort: first author's sort_key.
    first_author = conn.execute(
        text(
            "SELECT authors.sort_key FROM book_authors "
            "JOIN authors ON authors.id = book_authors.author_id "
            "WHERE book_authors.book_id = :b AND book_authors.position = 1"
        ),
        {"b": book_id},
    ).scalar_one()
    assert first_author == "Banks, Iain M."


def test_loans_keep_full_history_with_no_reminder_fields(conn: Connection) -> None:
    owner_id = _insert_user(conn, "lender")
    book_id = _insert_book(conn, owner_id, ownership_status="borrowed")
    conn.execute(
        text(
            "INSERT INTO loans (owner_id, book_id, borrowed_to, loaned_at, note, "
            "expected_return_date, returned_at) "
            "VALUES (:o, :b, 'Nikola', '2027-06-01', "
            "'will return after the summer', '2027-09-01', NULL)"
        ),
        {"o": owner_id, "b": book_id},
    )
    row = conn.execute(
        text(
            "SELECT borrowed_to, loaned_at, note, expected_return_date, returned_at "
            "FROM loans WHERE book_id = :b"
        ),
        {"b": book_id},
    ).fetchone()
    assert row == ("Nikola", "2027-06-01", "will return after the summer", "2027-09-01", None)
    # D12: no reminder machinery anywhere.
    loan_columns = column_names(conn, "loans")
    assert not any("remind" in column.lower() for column in loan_columns)


def test_settings_global_row_seeded_and_second_one_refused(conn: Connection) -> None:
    global_row = conn.execute(
        text(
            "SELECT currency_symbol, date_format, time_format, theme, zoom_preset "
            "FROM settings WHERE owner_id IS NULL"
        )
    ).fetchall()
    assert global_row == [("\u20ac", "dd.mm.yyyy", "24h", "light", "M")]
    with pytest.raises(
        IntegrityError
    ):  # exactly one global row (ifnull unique index; NULLs alone never collide)
        conn.execute(text("INSERT INTO settings (owner_id, theme) VALUES (NULL, 'dark')"))
    # Per-user overrides are the other half of the design (A2: zoom per user).
    owner_id = _insert_user(conn, "settings-owner")
    conn.execute(
        text("INSERT INTO settings (owner_id, zoom_preset) VALUES (:o, 'L')"), {"o": owner_id}
    )


def test_sensible_checks_refuse_impossible_numbers(conn: Connection) -> None:
    owner_id = _insert_user(conn, "numbers-owner")
    with pytest.raises(IntegrityError):
        _insert_book(conn, owner_id, height_mm=0)
    with pytest.raises(IntegrityError):
        _insert_book(conn, owner_id, page_count=-3)
    with pytest.raises(IntegrityError):
        _insert_book(conn, owner_id, price=-1.0)
    with pytest.raises(IntegrityError):
        _insert_book(conn, owner_id, publication_year=0)
    with pytest.raises(IntegrityError):  # a position needs a series
        _insert_book(conn, owner_id, series_position=3.5)


def test_migration_round_trip_downgrade_then_upgrade_again(tmp_path: Path) -> None:
    db_path = tmp_path / "roundtrip.sqlite3"
    run_alembic(db_path, "head")
    run_alembic(db_path, "base")
    engine = engine_for(db_path)
    try:
        with engine.connect() as connection:
            leftovers = {
                row[0]
                for row in connection.execute(
                    text("SELECT name FROM sqlite_master WHERE type = 'table'")
                )
                if not row[0].startswith("alembic_")
            }
        assert leftovers == set()
    finally:
        engine.dispose()
    run_alembic(db_path, "head")  # the same upgrade must apply to the emptied file
    engine = engine_for(db_path)
    try:
        with engine.connect() as connection:
            assert EXPECTED_TABLES.issubset(table_names(connection))
            seeded = connection.execute(text("SELECT count(*) FROM genres")).scalar_one()
        assert seeded == 45
    finally:
        engine.dispose()
