# The queries behind the four shelves (M6). Written as plain SQL on purpose
# (SPEC §15): the joins — first-listed author, series, curated genre, cached cover —
# are the thing a reader has to trust here, so they are written out rather than
# assembled from ORM fragments.
#
# `Filters` (app/filters.py) supplies the WHERE fragments; this module owns only the
# SELECT lists and ordering. The main shelf orders by genre in SQL and leaves the D6
# ordering within a section to `app.shelf.order_spines`, which is the pure, tested
# version of that rule.
#
# Every `{where}` is filled from `Filters.clauses()`, whose values are always bound
# parameters — nothing user-supplied is ever interpolated into the SQL text.
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.filters import Filters

_SHELF_SQL = """
    SELECT
        b.id                                AS book_id,
        b.title                             AS title,
        b.series_position                   AS series_position,
        b.height_mm                         AS height_mm,
        b.thickness_mm                      AS thickness_mm,
        s.name                              AS series_name,
        g.notation                          AS genre_notation,
        COALESCE(g.label, 'Uncategorised')  AS genre_label,
        COALESCE(g.sort_order, 9999)        AS genre_sort_order,
        COALESCE(a.name, 'Unknown author')  AS author_label,
        COALESCE(a.sort_key, 'unknown')     AS author_sort_key,
        EXISTS (
            SELECT 1 FROM photos p
            WHERE p.book_id = b.id AND p.slot = 'front'
              AND p.provider_filename IS NOT NULL
        )                                   AS has_provider_cover
    FROM books b
    LEFT JOIN series s ON s.id = b.series_id
    LEFT JOIN genres g ON g.notation = b.primary_genre_notation
    -- position 1 is the first-listed author, which is the one the shelf sorts by (D6).
    LEFT JOIN book_authors ba ON ba.book_id = b.id AND ba.position = 1
    LEFT JOIN authors a ON a.id = ba.author_id
    WHERE {where}
    ORDER BY genre_sort_order, author_sort_key, b.title
"""

# The Gone shelf needs the columns that explain the going: price and where for a
# sale, recipient and note for a gift (SPEC §3). Sold and gifted share the shelf;
# the `kind` filter inside it is handled by Filters.
_GONE_SQL = """
    SELECT
        b.id                   AS book_id,
        b.title                AS title,
        b.ownership_status     AS status,
        b.price                AS price,
        b.where_bought         AS where_bought,
        b.gifted_to            AS gifted_to,
        b.gift_note            AS gift_note,
        b.publication_year     AS publication_year,
        b.created_at           AS created_at,
        COALESCE(a.name, 'Unknown author') AS author_label,
        COALESCE(g.label, 'Uncategorised') AS genre_label
    FROM books b
    LEFT JOIN book_authors ba ON ba.book_id = b.id AND ba.position = 1
    LEFT JOIN authors a ON a.id = ba.author_id
    LEFT JOIN genres g ON g.notation = b.primary_genre_notation
    WHERE {where}
    ORDER BY CASE WHEN b.ownership_status = 'sold' THEN 0 ELSE 1 END, b.title
"""

# The Borrowed shelf is a book plus the loan currently out (SPEC §3: to whom, date,
# note, expected return). Returned loans stay in `loans` as history and are counted
# here; SPEC §12 says the due date is displayed and that is the whole feature.
_BORROWED_SQL = """
    SELECT
        b.id                   AS book_id,
        b.title                AS title,
        b.publication_year     AS publication_year,
        COALESCE(a.name, 'Unknown author') AS author_label,
        COALESCE(g.label, 'Uncategorised') AS genre_label,
        l.borrowed_to          AS borrowed_to,
        l.loaned_at            AS loaned_at,
        l.note                 AS note,
        l.expected_return_date AS expected_return_date,
        (SELECT count(*) FROM loans h WHERE h.book_id = b.id)             AS loan_count,
        (SELECT max(h.returned_at) FROM loans h
          WHERE h.book_id = b.id AND h.returned_at IS NOT NULL)           AS last_returned_at
    FROM books b
    LEFT JOIN book_authors ba ON ba.book_id = b.id AND ba.position = 1
    LEFT JOIN authors a ON a.id = ba.author_id
    LEFT JOIN genres g ON g.notation = b.primary_genre_notation
    LEFT JOIN loans l ON l.book_id = b.id AND l.returned_at IS NULL
    WHERE {where}
    ORDER BY COALESCE(l.loaned_at, b.created_at), b.title
"""


def where_clause(owner_id: int, filters: Filters | None) -> tuple[str, dict]:
    """The owner clause plus whatever the filters narrow, always bound."""
    fragments, params = (filters or Filters()).clauses()
    params["owner_id"] = owner_id
    return " AND ".join(["b.owner_id = :owner_id", *fragments]), params


def shelf_rows(session: Session, owner_id: int, filters: Filters | None = None) -> list[dict]:
    """Rows in the shape `app.shelf.build_shelf` wants, narrowed by `filters`."""
    where, params = where_clause(owner_id, filters)
    result = session.execute(text(_SHELF_SQL.format(where=where)), params)
    return [dict(row._mapping) for row in result]


def gone_rows(session: Session, owner_id: int, filters: Filters | None = None) -> list[dict]:
    where, params = where_clause(owner_id, filters)
    result = session.execute(text(_GONE_SQL.format(where=where)), params)
    return [dict(row._mapping) for row in result]


def borrowed_rows(session: Session, owner_id: int, filters: Filters | None = None) -> list[dict]:
    where, params = where_clause(owner_id, filters)
    result = session.execute(text(_BORROWED_SQL.format(where=where)), params)
    return [dict(row._mapping) for row in result]


def count_books(session: Session, owner_id: int, filters: Filters | None = None) -> int:
    """How many books a filter matches, without building the shelf."""
    where, params = where_clause(owner_id, filters)
    # The shape is fixed here; `where` comes from Filters, never from a string.
    statement = text(f"SELECT count(*) FROM books b WHERE {where}")
    return int(session.execute(statement, params).scalar_one())


# ---------------------------------------------------------------- filter options
# The filter bar lists only values that exist for this owner: no empty rooms, no
# author with nothing on the shelf.


def room_options(session: Session, owner_id: int) -> list[dict]:
    rows = session.execute(
        text(
            """
            SELECT r.id AS id, r.name AS name, count(b.id) AS book_count
            FROM rooms r
            LEFT JOIN books b ON b.room_id = r.id AND b.owner_id = r.owner_id
            WHERE r.owner_id = :owner_id
            GROUP BY r.id
            ORDER BY r.name
            """
        ),
        {"owner_id": owner_id},
    )
    return [dict(row._mapping) for row in rows]


def author_options(session: Session, owner_id: int, limit: int = 500) -> list[dict]:
    rows = session.execute(
        text(
            """
            SELECT a.id AS id, a.name AS name, a.sort_key AS sort_key,
                   count(ba.book_id) AS book_count
            FROM authors a
            JOIN book_authors ba ON ba.author_id = a.id
            JOIN books b ON b.id = ba.book_id AND b.owner_id = a.owner_id
            WHERE a.owner_id = :owner_id
            GROUP BY a.id
            ORDER BY a.sort_key
            LIMIT :limit
            """
        ),
        {"owner_id": owner_id, "limit": limit},
    )
    return [dict(row._mapping) for row in rows]


def genre_options(session: Session, owner_id: int) -> list[dict]:
    rows = session.execute(
        text(
            """
            SELECT g.notation AS notation, g.label AS label, g.sort_order AS sort_order,
                   count(b.id) AS book_count
            FROM genres g
            JOIN books b ON b.primary_genre_notation = g.notation AND b.owner_id = :owner_id
            GROUP BY g.notation
            ORDER BY g.sort_order
            """
        ),
        {"owner_id": owner_id},
    )
    return [dict(row._mapping) for row in rows]
