# Search (SPEC §9) against the seeded fixture, through the real FTS5 index.
#
# The interesting cases are the ones a LIKE query gets wrong: stemming, prefix
# matching, a query that is pure punctuation, a search term that lives only in a
# join (author names) or in a note, and the SPEC §3 rule that borrowed and gone
# books stay out of results unless the toggle asks for them.
from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from app.search import build_match


def spine_count(html: str) -> int:
    return html.count("data-book-id=")


# ---------------------------------------------------------------- query builder


def test_the_last_token_gets_a_prefix_match() -> None:
    assert build_match("dun") == '"dun"*'
    assert build_match("dune mess") == '"dune" AND "mess"*'


def test_punctuation_is_stripped_not_interpreted() -> None:
    """FTS5 has its own query language; `"`, `-`, `*` and parentheses must not
    reach it as operators."""
    assert build_match('dune "messiah"') == '"dune" AND "messiah"*'
    assert build_match("a AND b") == '"a" AND "AND" AND "b"*'
    assert build_match("(dune) -messiah") == '"dune" AND "messiah"*'


def test_an_unusable_query_returns_none() -> None:
    assert build_match("") is None
    assert build_match("!!! ??? ...") is None


def test_token_count_is_capped() -> None:
    assert len(build_match(" ".join(f"w{i}" for i in range(40))).split(" AND ")) == 8


# ---------------------------------------------------------------- the index


def test_the_index_holds_one_row_per_book(seeded_db: tuple[TestClient, Engine]) -> None:
    _client, engine = seeded_db
    with engine.connect() as conn:
        books = conn.execute(text("SELECT count(*) FROM books")).scalar_one()
        indexed = conn.execute(text("SELECT count(*) FROM books_fts")).scalar_one()
    assert indexed == books


def test_a_book_is_found_by_its_author_name(seeded_db: tuple[TestClient, Engine]) -> None:
    """Author names are not on the books row; they reach search through the index."""
    client, engine = seeded_db
    with engine.connect() as conn:
        author = conn.execute(text("SELECT name FROM authors ORDER BY name LIMIT 1")).scalar_one()

    response = client.get("/", params={"q": author.split()[0]})
    assert response.status_code == 200
    assert spine_count(response.text) >= 1


def test_a_note_written_after_the_fact_still_becomes_searchable(
    seeded_db: tuple[TestClient, Engine],
) -> None:
    """The fixture carries no notes, so this writes one: it proves both that the
    free-text fields are indexed and that a later UPDATE on `books` re-indexes the
    row through the trigger rather than leaving a stale document behind."""
    client, engine = seeded_db
    with engine.begin() as conn:
        row = conn.execute(text("SELECT id, title FROM books ORDER BY id LIMIT 1")).first()
        conn.execute(
            text("UPDATE books SET notes = :note WHERE id = :id"),
            {"note": "bought at the Novi Sad book fair, second hand", "id": row[0]},
        )

    assert client.get("/", params={"q": "novi sad fair"}).text.count(row[1]) >= 1

    with engine.begin() as conn:
        conn.execute(text("UPDATE books SET notes = NULL WHERE id = :id"), {"id": row[0]})
    assert row[1] not in client.get("/", params={"q": "novi sad fair"}).text


def test_a_book_is_found_by_its_year(seeded_db: tuple[TestClient, Engine]) -> None:
    client, engine = seeded_db
    with engine.connect() as conn:
        year = conn.execute(
            text(
                "SELECT publication_year FROM books "
                "WHERE publication_year BETWEEN 1900 AND 2000 LIMIT 1"
            )
        ).scalar_one()

    response = client.get("/", params={"q": year})
    assert response.status_code == 200
    assert spine_count(response.text) >= 1


def test_prefix_search_finds_a_word_still_being_typed(seeded_db: tuple[TestClient, Engine]) -> None:
    client, engine = seeded_db
    with engine.connect() as conn:
        title = conn.execute(
            text("SELECT title FROM books WHERE title LIKE 'The %' LIMIT 1")
        ).scalar_one()
    # A fragment of the second word: 'the' alone would match half the library.
    word = title.split()[1][:4]

    response = client.get("/", params={"q": f"the {word}"})
    assert title in response.text


def test_a_punctuation_query_returns_nothing_instead_of_everything(
    seeded_db: tuple[TestClient, Engine],
) -> None:
    client, _engine = seeded_db

    response = client.get("/?q=%22%22+%21%21%21")

    assert response.status_code == 200
    assert spine_count(response.text) == 0


def test_a_blank_search_does_not_claim_to_have_searched(
    seeded_db: tuple[TestClient, Engine],
) -> None:
    client, _engine = seeded_db

    response = client.get("/")

    assert "Type a title" not in response.text
    assert spine_count(response.text) >= 1


# ---------------------------------------------------------------- scope (SPEC §3)


def test_search_does_not_cover_the_wishlist_by_default(
    seeded_db: tuple[TestClient, Engine],
) -> None:
    client, engine = seeded_db
    with engine.connect() as conn:
        wanted = conn.execute(
            text("SELECT title FROM books WHERE ownership_status = 'wanted' ORDER BY id LIMIT 1")
        ).scalar_one()

    response = client.get("/", params={"q": wanted[:20]})
    # The wanted book is not owned, so it must not appear as a spine on the library shelf.
    assert spine_count(response.text) == 0


def test_search_excludes_borrowed_and_gone_by_default(seeded_db: tuple[TestClient, Engine]) -> None:
    client, engine = seeded_db
    with engine.connect() as conn:
        gone = conn.execute(
            text("SELECT title FROM books WHERE ownership_status = 'gifted' ORDER BY id LIMIT 1")
        ).scalar_one()

    assert gone not in client.get("/", params={"q": gone[:20]}).text


def test_the_explicit_toggle_pulls_borrowed_and_gone_in(
    seeded_db: tuple[TestClient, Engine],
) -> None:
    client, engine = seeded_db
    with engine.connect() as conn:
        gone = conn.execute(
            text("SELECT title FROM books WHERE ownership_status = 'gifted' ORDER BY id LIMIT 1")
        ).scalar_one()

    response = client.get("/", params={"q": gone[:20], "status": "gone"})
    assert gone in response.text


def test_a_word_searched_on_the_library_shelf_also_filters_it(
    seeded_db: tuple[TestClient, Engine],
) -> None:
    """The filter bar is the same control on every shelf (SPEC §9), and on the
    library page a `q` narrows that page rather than sending you elsewhere."""
    client, engine = seeded_db
    with engine.connect() as conn:
        title = conn.execute(
            text("SELECT title FROM books WHERE ownership_status = 'owned' ORDER BY id LIMIT 1")
        ).scalar_one()

    response = client.get("/", params={"q": title[:20]})
    assert title in response.text
    assert spine_count(response.text) < spine_count(client.get("/").text)
