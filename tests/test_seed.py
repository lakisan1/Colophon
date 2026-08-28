# M2 seed fixture tests. PLAN §M2 asks for row-count and series-ordering
# assertions plus idempotency; the rest of these guard the properties the later
# milestones quietly depend on (every enum value reachable, dimensions
# plausible, loans consistent with ownership, no invented ISBNs).
from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import Engine, text

from app.formats import FORMAT_BY_KEY
from app.seed import SeedSummary, dimensions, seed, sort_key
from app.seed_data import BOOKS, DEMO_USERNAME, GIFTS, LOANS, PHOTO_PROVIDERS, ROOMS
from tests.alembic_runner import engine_for, run_alembic


@pytest.fixture()
def seeded(tmp_path: Path) -> tuple[Engine, SeedSummary]:
    """A migrated throwaway database with the fixture loaded once."""
    db_path = tmp_path / "seed.db"
    run_alembic(db_path, "head")
    engine = engine_for(db_path)
    return engine, seed(engine)


def _count(engine: Engine, sql: str) -> int:
    with engine.connect() as conn:
        return int(conn.execute(text(sql)).scalar_one())


def test_summary_counts_match_the_database(seeded: tuple[Engine, SeedSummary]) -> None:
    engine, summary = seeded
    owner = summary.user_id

    with engine.connect() as conn:
        actual = {
            "rooms": conn.execute(
                text("SELECT count(*) FROM rooms WHERE owner_id = :o"), {"o": owner}
            ).scalar_one(),
            "series": conn.execute(
                text("SELECT count(*) FROM series WHERE owner_id = :o"), {"o": owner}
            ).scalar_one(),
            "authors": conn.execute(
                text("SELECT count(*) FROM authors WHERE owner_id = :o"), {"o": owner}
            ).scalar_one(),
            "books": conn.execute(
                text("SELECT count(*) FROM books WHERE owner_id = :o"), {"o": owner}
            ).scalar_one(),
            "book_authors": conn.execute(
                text("SELECT count(*) FROM book_authors WHERE owner_id = :o"), {"o": owner}
            ).scalar_one(),
            "book_genres": conn.execute(
                text("SELECT count(*) FROM book_genres WHERE owner_id = :o"), {"o": owner}
            ).scalar_one(),
            "loans": conn.execute(
                text("SELECT count(*) FROM loans WHERE owner_id = :o"), {"o": owner}
            ).scalar_one(),
            "photos": conn.execute(
                text("SELECT count(*) FROM photos WHERE owner_id = :o"), {"o": owner}
            ).scalar_one(),
        }
    assert actual == summary.as_dict()
    assert actual["books"] == len(BOOKS)


def test_seeding_twice_leaves_identical_counts(seeded: tuple[Engine, SeedSummary]) -> None:
    engine, first = seeded
    second = seed(engine)

    assert second.as_dict() == first.as_dict()
    # The demo user is reused, not duplicated, and its password hash survives.
    assert _count(engine, "SELECT count(*) FROM users") == 1
    assert second.user_id == first.user_id


def test_demo_user_exists_with_the_named_username(seeded: tuple[Engine, SeedSummary]) -> None:
    engine, _summary = seeded
    with engine.connect() as conn:
        username = conn.execute(text("SELECT username FROM users")).scalar_one()
    assert username == DEMO_USERNAME


def test_every_ownership_status_appears(seeded: tuple[Engine, SeedSummary]) -> None:
    engine, _ = seeded
    with engine.connect() as conn:
        found = {
            row[0] for row in conn.execute(text("SELECT DISTINCT ownership_status FROM books"))
        }
    assert found == {"owned", "wanted", "borrowed", "sold", "gifted"}


def test_every_reading_state_appears(seeded: tuple[Engine, SeedSummary]) -> None:
    engine, _ = seeded
    with engine.connect() as conn:
        found = {row[0] for row in conn.execute(text("SELECT DISTINCT reading_state FROM books"))}
    assert found == {"unread", "reading", "finished", "dnf"}


def test_every_physical_format_appears(seeded: tuple[Engine, SeedSummary]) -> None:
    engine, _ = seeded
    with engine.connect() as conn:
        found = {row[0] for row in conn.execute(text("SELECT DISTINCT format FROM books"))}
    assert found == set(FORMAT_BY_KEY)


def test_decimal_series_positions_sort_between_integers(
    seeded: tuple[Engine, SeedSummary],
) -> None:
    """D9: 1.5 must land between 1 and 2, which is why the column is REAL."""
    engine, _ = seeded
    with engine.connect() as conn:
        positions = [
            row[0]
            for row in conn.execute(
                text(
                    "SELECT b.series_position FROM books b"
                    " JOIN series s ON s.id = b.series_id"
                    " WHERE s.name = 'Horus Heresy' AND b.series_position IS NOT NULL"
                    " ORDER BY b.series_position"
                )
            )
        ]
    assert positions == sorted(positions)
    assert 1.5 in positions and 2.5 in positions
    assert positions.index(1.5) == positions.index(1.0) + 1
    assert positions.index(2.5) == positions.index(2.0) + 1


def test_a_fractional_prequel_sorts_before_volume_one(seeded: tuple[Engine, SeedSummary]) -> None:
    engine, _ = seeded
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT b.title, b.series_position FROM books b"
                " JOIN series s ON s.id = b.series_id"
                " WHERE s.name = 'The Wheel of Time' ORDER BY b.series_position"
            )
        ).all()
    assert rows[0][1] == 0.5
    assert rows[0][0] == "New Spring"


def test_only_the_planned_titles_are_duplicated(seeded: tuple[Engine, SeedSummary]) -> None:
    """Two titles exist in two formats on purpose (ownership-level dedup, D3)."""
    engine, _ = seeded
    with engine.connect() as conn:
        dupes = {
            row[0]
            for row in conn.execute(
                text("SELECT title FROM books GROUP BY title HAVING count(*) > 1")
            )
        }
    assert dupes == {"The Count of Monte Cristo", "The Very Hungry Caterpillar"}


def test_duplicate_title_rows_differ_by_format(seeded: tuple[Engine, SeedSummary]) -> None:
    engine, _ = seeded
    with engine.connect() as conn:
        formats = [
            row[0]
            for row in conn.execute(
                text("SELECT format FROM books WHERE title = 'The Count of Monte Cristo'")
            )
        ]
    assert len(set(formats)) == len(formats)
    # Only the gift edition carries sprayed edges.
    with engine.connect() as conn:
        sprayed = conn.execute(
            text(
                "SELECT count(*) FROM books WHERE title = 'The Count of Monte Cristo'"
                " AND is_sprayed_edge"
            )
        ).scalar_one()
    assert sprayed == 1


def test_no_isbn_is_invented_by_the_fixture(seeded: tuple[Engine, SeedSummary]) -> None:
    """M6 supplies ISBNs; fake ones would resolve to the wrong book upstream."""
    engine, _ = seeded
    assert _count(engine, "SELECT count(*) FROM books WHERE isbn IS NOT NULL") == 0


def test_borrowed_books_have_exactly_one_loan_each(seeded: tuple[Engine, SeedSummary]) -> None:
    engine, _ = seeded
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT b.title, count(l.id) FROM books b"
                " LEFT JOIN loans l ON l.book_id = b.id"
                " WHERE b.ownership_status = 'borrowed' GROUP BY b.id"
            )
        ).all()
    assert len(rows) == len(LOANS)
    assert {row[0] for row in rows} == {loan[0] for loan in LOANS}
    assert all(row[1] == 1 for row in rows)


def test_only_borrowed_books_are_on_loan(seeded: tuple[Engine, SeedSummary]) -> None:
    engine, _ = seeded
    assert (
        _count(
            engine,
            "SELECT count(*) FROM loans l JOIN books b ON b.id = l.book_id"
            " WHERE b.ownership_status <> 'borrowed'",
        )
        == 0
    )


def test_loan_dates_are_recorded_and_some_are_returned(seeded: tuple[Engine, SeedSummary]) -> None:
    engine, _ = seeded
    assert _count(engine, "SELECT count(*) FROM loans WHERE loaned_at IS NULL") == 0
    returned = _count(engine, "SELECT count(*) FROM loans WHERE returned_at IS NOT NULL")
    assert 0 < returned < len(LOANS)


def test_gifted_books_name_a_recipient_and_own_no_room(seeded: tuple[Engine, SeedSummary]) -> None:
    engine, _ = seeded
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT title, gifted_to, room_id FROM books WHERE ownership_status = 'gifted'")
        ).all()
    assert {row[0] for row in rows} == set(GIFTS)
    assert all(row[1] for row in rows)
    assert all(row[2] is None for row in rows)


def test_items_that_are_not_on_the_shelf_have_no_room(seeded: tuple[Engine, SeedSummary]) -> None:
    """Wanted / sold / gifted / borrowed books are not physically present (D2)."""
    engine, _ = seeded
    assert (
        _count(
            engine,
            "SELECT count(*) FROM books WHERE ownership_status <> 'owned' AND room_id IS NOT NULL",
        )
        == 0
    )


def test_every_owned_book_has_a_known_room(seeded: tuple[Engine, SeedSummary]) -> None:
    engine, _ = seeded
    with engine.connect() as conn:
        names = {
            row[0]
            for row in conn.execute(
                text("SELECT DISTINCT r.name FROM books b JOIN rooms r ON r.id = b.room_id")
            )
        }
    assert names <= set(ROOMS)
    assert (
        _count(
            engine,
            "SELECT count(*) FROM books WHERE ownership_status = 'owned' AND room_id IS NULL",
        )
        == 0
    )


def test_dimensions_are_present_and_plausible(seeded: tuple[Engine, SeedSummary]) -> None:
    engine, _ = seeded
    assert (
        _count(
            engine,
            "SELECT count(*) FROM books WHERE height_mm IS NULL OR width_mm IS NULL"
            " OR thickness_mm IS NULL",
        )
        == 0
    )
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT height_mm, width_mm, thickness_mm FROM books")).all()
    for height, width, thickness in rows:
        assert 90 <= height <= 310, height
        assert 100 <= width <= 430, width
        assert 5 <= thickness <= 200, thickness


def test_thickness_tracks_page_count_within_a_format(seeded: tuple[Engine, SeedSummary]) -> None:
    """The long Malazan paperbacks must be thicker than the short ones."""
    engine, _ = seeded
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT page_count, thickness_mm FROM books"
                " WHERE format = 'mass_market_paperback' AND page_count IS NOT NULL"
            )
        ).all()
    thinnest_short = min(t for pages, t in rows if pages < 300)
    thickest_long = max(t for pages, t in rows if pages > 700)
    assert thinnest_short < thickest_long


def test_multi_author_books_are_linked_in_order(seeded: tuple[Engine, SeedSummary]) -> None:
    engine, _ = seeded
    with engine.connect() as conn:
        names = [
            row[0]
            for row in conn.execute(
                text(
                    "SELECT a.name FROM book_authors ba"
                    " JOIN authors a ON a.id = ba.author_id"
                    " JOIN books b ON b.id = ba.book_id"
                    " WHERE b.title = 'Design Patterns'"
                    " ORDER BY ba.position"
                )
            )
        ]
    assert len(names) == 4
    assert names[0] == "Erich Gamma"
    assert _count(engine, "SELECT count(*) FROM book_authors WHERE position < 1") == 0


def test_primary_genre_notations_all_resolve_to_curated_rows(
    seeded: tuple[Engine, SeedSummary],
) -> None:
    engine, _ = seeded
    assert (
        _count(
            engine,
            "SELECT count(*) FROM books b LEFT JOIN genres g"
            " ON g.notation = b.primary_genre_notation"
            " WHERE b.primary_genre_notation IS NOT NULL AND g.notation IS NULL",
        )
        == 0
    )


def test_the_local_young_adult_notation_is_used(seeded: tuple[Engine, SeedSummary]) -> None:
    """D4's one non-BISAC entry has to be exercised or it is dead weight."""
    engine, _ = seeded
    assert (
        _count(engine, "SELECT count(*) FROM books WHERE primary_genre_notation = 'LOCAL/YA'") > 0
    )


def test_provider_photo_rows_match_the_fixture(seeded: tuple[Engine, SeedSummary]) -> None:
    """M7 writes the image bytes; these rows are its cache keys."""
    engine, _ = seeded
    expected_slots = sum(len(slots) for slots in PHOTO_PROVIDERS.values())
    assert _count(engine, "SELECT count(*) FROM photos") == expected_slots
    assert _count(engine, "SELECT count(*) FROM photos WHERE provider_filename IS NULL") == 0
    assert _count(engine, "SELECT count(*) FROM photos WHERE upload_filename IS NOT NULL") == 0
    with engine.connect() as conn:
        slots = {row[0] for row in conn.execute(text("SELECT DISTINCT slot FROM photos"))}
    assert slots <= {"front", "back", "spine"}


def test_sort_key_puts_the_surname_first() -> None:
    assert sort_key("Dan Abnett") == "abnett, dan"
    assert sort_key("Aaron Dembski-Bowden") == "dembski-bowden, aaron"
    assert sort_key("Anonymous") == "anonymous"


def test_dimensions_are_deterministic_and_format_shaped() -> None:
    assert dimensions("hardcover", "Dune", 688) == dimensions("hardcover", "Dune", 688)
    folio_h, folio_w, folio_t = dimensions("oversized_folio", "Anything", 400)
    assert (folio_h, folio_w, folio_t) == (300, 420, 40)
    # A boxed set is thicker than a pocket A6 of the same page count.
    assert dimensions("boxed_set", "X", 100)[2] > dimensions("pocket_a6", "X", 100)[2]
