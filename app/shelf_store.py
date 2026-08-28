# The one query the main shelf needs. Written as plain SQL on purpose (SPEC
# §15): the joins — first-listed author, series, curated genre, cached cover —
# are the thing a reader has to trust here, so they should be readable rather
# than assembled from ORM fragments.
#
# "My library" is copies in possession only (SPEC §12); the other three shelves
# arrive in M6 and reuse the same row shape.
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

SHELF_SELECT = text(
    """
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
    WHERE b.owner_id = :owner_id
      AND b.ownership_status = 'owned'
    ORDER BY genre_sort_order, b.title
    """
)


def shelf_rows(session: Session, owner_id: int) -> list[dict]:
    """Rows for the owner's own books, in the shape `app.shelf.build_shelf` wants."""
    rows = session.execute(SHELF_SELECT, {"owner_id": owner_id})
    return [dict(row._mapping) for row in rows]
