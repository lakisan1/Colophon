# Shelves 2, 3 and 4 plus the server-side filters (SPEC §3, §9; D6, D7).
#
# Two things these tests exist to catch:
#   * a shelf leaking another shelf's books (the scope is a WHERE clause, so the
#     mistake is silent when it breaks), and
#   * a filter that looks like it works because the page shrank, while actually
#     ignoring the parameter. Hence every filter assertion compares against a
#     count taken straight from the database.
from __future__ import annotations

import html

from fastapi.testclient import TestClient
from sqlalchemy import Engine, text


def spine_count(markup: str) -> int:
    return markup.count("data-book-id=")


def text_of(response) -> str:
    """Titles and room names carry apostrophes; Jinja escapes them, so comparing
    raw titles against `response.text` fails on "Kids' room" for no real reason."""
    return html.unescape(response.text)


def titles(engine: Engine, status: str) -> list[str]:
    with engine.connect() as conn:
        return [
            row[0]
            for row in conn.execute(
                text("SELECT title FROM books WHERE ownership_status = :s ORDER BY id"),
                {"s": status},
            )
        ]


# ---------------------------------------------------------------- shelf 1


def test_the_library_shelf_shows_only_owned_books(seeded_db: tuple[TestClient, Engine]) -> None:
    client, engine = seeded_db

    response = client.get("/")

    assert spine_count(response.text) == len(titles(engine, "owned"))


def test_the_wishlist_shelf_shows_only_wanted_books(seeded_db: tuple[TestClient, Engine]) -> None:
    client, engine = seeded_db

    response = client.get("/wishlist")

    wanted = titles(engine, "wanted")
    assert spine_count(response.text) == len(wanted)
    assert wanted[0] in text_of(response)
    # A book that is on the shelf must not also appear as something to buy.
    owned_only = next(title for title in titles(engine, "owned") if title not in wanted)
    assert owned_only not in text_of(response)


# ---------------------------------------------------------------- shelf 2


def test_the_borrowed_shelf_shows_who_has_each_book(seeded_db: tuple[TestClient, Engine]) -> None:
    client, engine = seeded_db
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT b.title, l.borrowed_to, l.expected_return_date
                FROM books b JOIN loans l ON l.book_id = b.id AND l.returned_at IS NULL
                ORDER BY b.id
                """
            )
        ).fetchall()

    response = client.get("/borrowed")

    assert response.status_code == 200
    body = text_of(response)
    for title, borrower, due in rows:
        assert title in body
        assert borrower in body
        if due:
            assert due in body


def test_returning_a_book_moves_it_off_the_borrowed_shelf(
    seeded_db: tuple[TestClient, Engine],
) -> None:
    """The status axis is what puts a book on a shelf (SPEC §3), so recording the
    return must move it back to the library rather than leave it listed twice."""
    client, engine = seeded_db
    with engine.begin() as conn:
        row = conn.execute(
            text(
                "SELECT id, title FROM books "
                "WHERE ownership_status = 'borrowed' ORDER BY id LIMIT 1"
            )
        ).first()
        conn.execute(
            text(
                "UPDATE books SET ownership_status = 'owned', "
                "room_id = (SELECT id FROM rooms ORDER BY id LIMIT 1)"
                " WHERE id = :id"
            ),
            {"id": row[0]},
        )

    borrowed = text_of(client.get("/borrowed"))
    assert row[1] not in borrowed
    assert row[1] in text_of(client.get("/"))


def test_loan_history_is_carried_alongside_the_current_loan(
    seeded_db: tuple[TestClient, Engine],
) -> None:
    """SPEC §3 keeps history; the page says how many times a book has been out."""
    client, engine = seeded_db
    with engine.connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT b.title, count(*) AS c FROM books b JOIN loans l ON l.book_id = b.id
                WHERE b.ownership_status = 'borrowed' GROUP BY b.id ORDER BY c DESC LIMIT 1
                """
            )
        ).first()

    response = client.get("/borrowed")
    assert row[0] in response.text
    assert "Loans" in response.text


# ---------------------------------------------------------------- shelf 3


def test_the_gone_shelf_holds_sold_and_gifted_together(
    seeded_db: tuple[TestClient, Engine],
) -> None:
    client, engine = seeded_db

    response = client.get("/gone")
    body = text_of(response)

    assert spine_count(response.text) == 0  # it is a table, not spines
    for title in titles(engine, "sold") + titles(engine, "gifted"):
        assert title in body


def test_the_kind_filter_separates_sold_from_gifted(seeded_db: tuple[TestClient, Engine]) -> None:
    client, engine = seeded_db

    sold = text_of(client.get("/gone", params={"kind": "sold"}))
    gifted = text_of(client.get("/gone", params={"kind": "gifted"}))

    for title in titles(engine, "sold"):
        assert title in sold
        assert title not in gifted
    for title in titles(engine, "gifted"):
        assert title in gifted
        assert title not in sold


def test_the_gone_shelf_shows_the_price_with_the_settings_symbol(
    seeded_db: tuple[TestClient, Engine],
) -> None:
    client, engine = seeded_db
    with engine.connect() as conn:
        price = conn.execute(
            text(
                "SELECT price FROM books "
                "WHERE ownership_status = 'sold' AND price IS NOT NULL LIMIT 1"
            )
        ).scalar_one()
    assert price is not None

    response = client.get("/gone")
    # The symbol comes from Settings, not from the row (SPEC §10).
    assert f"€{price}" in text_of(response)


# ---------------------------------------------------------------- filters (SPEC §9)


def test_the_author_filter_finds_books_across_genre_sections(
    seeded_db: tuple[TestClient, Engine],
) -> None:
    """SPEC §9: an author's books across all sections. The fixture has co-authored
    and multi-genre authors, so this checks the real shape rather than one row."""
    client, engine = seeded_db
    with engine.connect() as conn:
        author = conn.execute(
            text(
                """
                SELECT a.id, a.name, count(*) AS books,
                    count(DISTINCT b.primary_genre_notation) AS genres
                FROM authors a
                JOIN book_authors ba ON ba.author_id = a.id
                JOIN books b ON b.id = ba.book_id AND b.ownership_status = 'owned'
                GROUP BY a.id
                ORDER BY books DESC, genres DESC
                LIMIT 1
                """
            )
        ).first()

    response = client.get("/", params={"author": str(author[0])})

    assert spine_count(response.text) == author[2]
    # The whole result set belongs to that author.
    assert author[1].split()[-1] in response.text


def test_the_year_filter_is_exact(seeded_db: tuple[TestClient, Engine]) -> None:
    client, engine = seeded_db
    year = 1995
    with engine.connect() as conn:
        expected = conn.execute(
            text(
                "SELECT count(*) FROM books "
                "WHERE ownership_status = 'owned' AND publication_year = :y"
            ),
            {"y": year},
        ).scalar_one()

    assert spine_count(client.get("/", params={"year": str(year)}).text) == expected


def test_the_reading_state_filter_works_on_a_shelf(seeded_db: tuple[TestClient, Engine]) -> None:
    client, engine = seeded_db
    with engine.connect() as conn:
        expected = conn.execute(
            text(
                "SELECT count(*) FROM books "
                "WHERE ownership_status = 'owned' AND reading_state = 'reading'"
            )
        ).scalar_one()

    assert spine_count(client.get("/", params={"reading": "reading"}).text) == expected


def test_the_room_filter_narrows_the_shelf(seeded_db: tuple[TestClient, Engine]) -> None:
    """D7: rooms are filters, not pages."""
    client, engine = seeded_db
    with engine.connect() as conn:
        room = conn.execute(
            text(
                """
                SELECT r.id, count(b.id) FROM rooms r
                JOIN books b ON b.room_id = r.id AND b.ownership_status = 'owned'
                GROUP BY r.id ORDER BY count(b.id) DESC LIMIT 1
                """
            )
        ).first()

    assert spine_count(client.get("/", params={"room": str(room[0])}).text) == room[1]


def test_the_no_room_filter_matches_books_without_a_room(
    seeded_db: tuple[TestClient, Engine],
) -> None:
    """D2 leaves non-owned books with room_id NULL, so the wishlist is where this
    filter has something to match; on the library shelf it must match nothing."""
    client, engine = seeded_db
    with engine.connect() as conn:
        wishlist_expected = conn.execute(
            text("SELECT count(*) FROM books WHERE ownership_status = 'wanted' AND room_id IS NULL")
        ).scalar_one()
        library_expected = conn.execute(
            text("SELECT count(*) FROM books WHERE ownership_status = 'owned' AND room_id IS NULL")
        ).scalar_one()

    assert wishlist_expected > 0
    assert spine_count(client.get("/wishlist", params={"room": "none"}).text) == wishlist_expected
    assert spine_count(client.get("/", params={"room": "none"}).text) == library_expected


def test_the_genre_filter_includes_extra_filter_only_genres(
    seeded_db: tuple[TestClient, Engine],
) -> None:
    """D5 gives a book one primary genre; `book_genres` holds the extras. Filtering
    by a genre has to find both, or the extra tags are decoration.

    The fixture tags each book with its primary genre only, so this writes the
    second tag the code path needs and counts from the database afterwards."""
    client, engine = seeded_db
    with engine.begin() as conn:
        book = conn.execute(
            text(
                "SELECT id, primary_genre_notation FROM books WHERE ownership_status = 'owned'"
                " ORDER BY id LIMIT 1"
            )
        ).first()
        extra = conn.execute(
            text(
                "SELECT notation FROM genres WHERE notation != :n AND notation NOT IN"
                " (SELECT genre_notation FROM book_genres WHERE book_id = :id) LIMIT 1"
            ),
            {"n": book[1], "id": book[0]},
        ).scalar_one()
        conn.execute(
            text(
                "INSERT INTO book_genres (book_id, genre_notation, owner_id) VALUES (:id, :g, :o)"
            ),
            {"id": book[0], "g": extra, "o": 2},
        )
        expected = conn.execute(
            text(
                """
                SELECT count(*) FROM books b
                WHERE b.ownership_status = 'owned'
                  AND (b.primary_genre_notation = :n
                       OR b.id IN (SELECT book_id FROM book_genres WHERE genre_notation = :n))
                """
            ),
            {"n": extra},
        ).scalar_one()

    assert expected >= 1
    assert spine_count(client.get("/", params={"genre": extra}).text) == expected


def test_filters_combine(seeded_db: tuple[TestClient, Engine]) -> None:
    client, engine = seeded_db
    with engine.connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT ba.author_id, b.reading_state, count(*) AS c
                FROM books b JOIN book_authors ba ON ba.book_id = b.id
                WHERE b.ownership_status = 'owned'
                GROUP BY ba.author_id, b.reading_state
                HAVING c BETWEEN 2 AND 8
                LIMIT 1
                """
            )
        ).first()

    response = client.get("/", params={"author": str(row[0]), "reading": row[1]})
    assert spine_count(response.text) == row[2]


def test_an_impossible_filter_shows_a_clear_empty_state(
    seeded_db: tuple[TestClient, Engine],
) -> None:
    client, _engine = seeded_db

    response = client.get("/", params={"year": "1066"})

    assert "Nothing matches those filters" in text_of(response)
    assert spine_count(response.text) == 0


def test_an_unrecognised_parameter_is_ignored_not_an_error(
    seeded_db: tuple[TestClient, Engine],
) -> None:
    """A stale bookmark should show a wider list, not a 422 page."""
    client, engine = seeded_db

    response = client.get("/", params={"year": "abcd", "room": "attic", "reading": "abandoned"})

    assert response.status_code == 200
    assert spine_count(response.text) == len(titles(engine, "owned"))


def test_the_filter_bar_offers_only_rooms_that_exist(seeded_db: tuple[TestClient, Engine]) -> None:
    client, engine = seeded_db
    with engine.connect() as conn:
        rooms = [row[0] for row in conn.execute(text("SELECT name FROM rooms ORDER BY name"))]

    body = text_of(client.get("/"))
    for name in rooms:
        assert name in body


def test_navigation_links_carry_the_active_filter(seeded_db: tuple[TestClient, Engine]) -> None:
    """Changing shelf must not silently drop the filter you were working with."""
    client, _engine = seeded_db

    html = client.get("/", params={"reading": "reading"}).text

    assert "reading" in html
    assert 'href="/wishlist?reading=reading"' in html
    assert 'href="/export.csv?reading=reading"' in html


def test_every_shelf_requires_a_signed_in_user(client: TestClient) -> None:
    """SPEC §13: the login screen is the only public surface. `client` is the
    bypass-off app from conftest."""
    for path in (
        "/",
        "/borrowed",
        "/gone",
        "/wishlist",
        "/search",
        "/stats",
        "/export.csv",
        "/export.json",
    ):
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 303, path
        assert response.headers["location"] == "/login", path
