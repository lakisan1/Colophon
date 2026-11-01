"""Search: a real FTS5 index over the fields SPEC §9 says to search.

The index is its own content table (not external content) with `rowid = books.id`,
because the text a search has to hit is a *join*: title, subtitle, ISBN, publisher,
year, both status axes, every author name, series, the curated genre plus any extra
genres, and the free-text fields. External-content FTS can only index columns that
live in one table.

Kept in sync by triggers rather than application code, so a row written by the seed
script, by a route, or by hand in a SQLite shell is indexed identically — the kind
of divergence that only shows up months later as "search misses that book".

`tokenize = 'porter unicode61 remove_diacritics 2'` stems ("running" finds "run")
and folds accents, which matters for a shelf that holds Serbian, French and Japanese
titles side by side.

Downgrade drops the virtual table; SQLite drops its shadow tables with it.
"""

from __future__ import annotations

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


# The document a search matches against. Built with || and ifnull rather than
# concat_ws so it also runs on the older SQLite that ships with some base images
# (concat_ws needs 3.44; FTS5 itself has been around far longer).
SEARCH_TEXT = """
    lower(
           ifnull(b.title, '')                  || ' '
        || ifnull(b.subtitle, '')               || ' '
        || ifnull(b.isbn, '')                   || ' '
        || ifnull(b.publisher, '')              || ' '
        || ifnull(CAST(b.publication_year AS TEXT), '') || ' '
        || ifnull(b.ownership_status, '')       || ' '
        || ifnull(b.reading_state, '')          || ' '
        || ifnull(b.genre_free_text, '')        || ' '
        || ifnull(b.notes, '')                  || ' '
        || ifnull((SELECT group_concat(a.name, ' ')
                     FROM book_authors ba
                     JOIN authors a ON a.id = ba.author_id
                    WHERE ba.book_id = b.id), '') || ' '
        || ifnull((SELECT s.name FROM series s WHERE s.id = b.series_id), '') || ' '
        || ifnull((SELECT g.label FROM genres g WHERE g.notation = b.primary_genre_notation), '')
        || ' '
        || ifnull((SELECT group_concat(g2.label, ' ')
                     FROM book_genres bg
                     JOIN genres g2 ON g2.notation = bg.genre_notation
                    WHERE bg.book_id = b.id), '')
    )
"""


# Re-indexing a set of books is always the same two steps: drop their rows from
# the index, then rebuild them from the current data. `books_fts` stores its own
# content (it is not an external-content table), so ordinary DELETE works and the
# fts5 'delete'/'replace' admin commands — which exist for external-content tables
# — are neither needed nor supported here.
#
# `match` selects the books to rebuild, using the alias `b` for `books`.
def _reindex(match: str) -> str:
    return f"""
        DELETE FROM books_fts WHERE rowid IN (SELECT id FROM books b WHERE {match});
        INSERT INTO books_fts (rowid, owner_id, search_text)
        SELECT b.id, b.owner_id, {SEARCH_TEXT} FROM books b WHERE {match};
    """


def _trigger(name: str, event: str, body: str) -> str:
    return f"CREATE TRIGGER {name} {event} BEGIN {body} END"


_TRIGGERS: tuple[str, ...] = (
    _trigger("books_fts_after_insert", "AFTER INSERT ON books", _reindex("b.id = NEW.id")),
    _trigger("books_fts_after_update", "AFTER UPDATE ON books", _reindex("b.id = NEW.id")),
    _trigger(
        "books_fts_after_delete",
        "AFTER DELETE ON books",
        "DELETE FROM books_fts WHERE rowid = OLD.id;",
    ),
    # Author links change the document without touching the books row at all.
    _trigger(
        "book_authors_fts_after_insert",
        "AFTER INSERT ON book_authors",
        _reindex("b.id = NEW.book_id"),
    ),
    _trigger(
        "book_authors_fts_after_delete",
        "AFTER DELETE ON book_authors",
        _reindex("b.id = OLD.book_id"),
    ),
    # Renaming an author reindexes exactly the books that carry that author, and a
    # renamed series reaches every book in it.
    _trigger(
        "authors_fts_after_update",
        "AFTER UPDATE OF name ON authors",
        _reindex("b.id IN (SELECT ba.book_id FROM book_authors ba WHERE ba.author_id = NEW.id)"),
    ),
    _trigger(
        "series_fts_after_update",
        "AFTER UPDATE OF name ON series",
        _reindex("b.series_id = NEW.id"),
    ),
    _trigger(
        "book_genres_fts_after_insert",
        "AFTER INSERT ON book_genres",
        _reindex("b.id = NEW.book_id"),
    ),
    _trigger(
        "book_genres_fts_after_delete",
        "AFTER DELETE ON book_genres",
        _reindex("b.id = OLD.book_id"),
    ),
    # A genre label is shared by many books, so reindex everything with that notation.
    _trigger(
        "genres_fts_after_update",
        "AFTER UPDATE OF label ON genres",
        _reindex("b.primary_genre_notation = NEW.notation"),
    ),
)

_TRIGGER_NAMES = (
    "genres_fts_after_update",
    "book_genres_fts_after_delete",
    "book_genres_fts_after_insert",
    "series_fts_after_update",
    "authors_fts_after_update",
    "book_authors_fts_after_delete",
    "book_authors_fts_after_insert",
    "books_fts_after_delete",
    "books_fts_after_update",
    "books_fts_after_insert",
)


def upgrade() -> None:
    op.execute(
        """
        CREATE VIRTUAL TABLE books_fts USING fts5(
            search_text,
            owner_id UNINDEXED,
            tokenize = 'porter unicode61 remove_diacritics 2'
        )
        """
    )
    for statement in _TRIGGERS:
        op.execute(statement.strip() + ";")
    # Backfill everything that is already in the database.
    op.execute(
        f"""
        INSERT INTO books_fts (rowid, owner_id, search_text)
        SELECT b.id, b.owner_id, {SEARCH_TEXT} FROM books b
        """
    )


def downgrade() -> None:
    for name in _TRIGGER_NAMES:
        op.execute(f"DROP TRIGGER IF EXISTS {name}")
    op.execute("DROP TABLE IF EXISTS books_fts")
