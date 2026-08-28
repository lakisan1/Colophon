# CSV and JSON export (SPEC §11). There is no import in v1 (D13) — the library is
# entered by hand — so this file only has to be honest on the way out.
#
# The CSV is written with `csv`, which quotes and escapes; the round-trip test feeds
# a title containing a comma, a double quote, a newline and Serbian characters
# through it and reads the result back with the same module.
from __future__ import annotations

import csv
import io
import json
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.filters import Filters
from app.shelf_store import where_clause  # the same WHERE builder the shelves use

# (row key, CSV header). Order is deliberate: what you would write on an index
# card first — identity, then where it lives, then the statuses, then the details.
EXPORT_FIELDS: tuple[tuple[str, str], ...] = (
    ("id", "id"),
    ("title", "title"),
    ("subtitle", "subtitle"),
    ("authors", "authors"),
    ("series_name", "series"),
    ("series_position", "series_position"),
    ("genre", "genre"),
    ("extra_genres", "extra_genres"),
    ("format", "format"),
    ("publication_year", "year"),
    ("page_count", "pages"),
    ("language", "language"),
    ("publisher", "publisher"),
    ("isbn", "isbn"),
    ("room", "room"),
    ("ownership_status", "ownership_status"),
    ("reading_state", "reading_state"),
    ("borrowed_to", "borrowed_to"),
    ("loan_due", "loan_due"),
    ("price", "price"),
    ("where_bought", "where_bought"),
    ("gifted_to", "gifted_to"),
    ("gift_note", "gift_note"),
    ("height_mm", "height_mm"),
    ("width_mm", "width_mm"),
    ("thickness_mm", "thickness_mm"),
    ("is_signed", "is_signed"),
    ("is_sprayed_edge", "is_sprayed_edge"),
    ("inscription", "inscription"),
    ("notes", "notes"),
    ("created_at", "created_at"),
)

_EXPORT_SQL = """
    SELECT
        b.id                   AS id,
        b.title                AS title,
        b.subtitle             AS subtitle,
        (SELECT group_concat(a.name, '; ')
           FROM book_authors ba JOIN authors a ON a.id = ba.author_id
          WHERE ba.book_id = b.id ORDER BY ba.position) AS authors,
        s.name                 AS series_name,
        b.series_position      AS series_position,
        g.label                AS genre,
        (SELECT group_concat(g2.label, '; ')
           FROM book_genres bg JOIN genres g2 ON g2.notation = bg.genre_notation
          WHERE bg.book_id = b.id) AS extra_genres,
        b.format               AS format,
        b.publication_year     AS publication_year,
        b.page_count           AS page_count,
        b.language             AS language,
        b.publisher            AS publisher,
        b.isbn                 AS isbn,
        r.name                 AS room,
        b.ownership_status     AS ownership_status,
        b.reading_state        AS reading_state,
        l.borrowed_to          AS borrowed_to,
        l.expected_return_date AS loan_due,
        b.price                AS price,
        b.where_bought         AS where_bought,
        b.gifted_to            AS gifted_to,
        b.gift_note            AS gift_note,
        b.height_mm            AS height_mm,
        b.width_mm             AS width_mm,
        b.thickness_mm         AS thickness_mm,
        b.is_signed            AS is_signed,
        b.is_sprayed_edge      AS is_sprayed_edge,
        b.inscription          AS inscription,
        b.notes                AS notes,
        b.created_at           AS created_at
    FROM books b
    LEFT JOIN series s ON s.id = b.series_id
    LEFT JOIN genres g ON g.notation = b.primary_genre_notation
    LEFT JOIN rooms r ON r.id = b.room_id
    LEFT JOIN loans l ON l.book_id = b.id AND l.returned_at IS NULL
    WHERE {where}
    ORDER BY b.id
"""


def export_rows(
    session: Session, owner_id: int, filters: Filters | None = None
) -> list[dict[str, Any]]:
    """One dict per book, with the same filters the shelves use, so what you see
    is what you export."""
    where, params = where_clause(owner_id, filters)
    rows = session.execute(text(_EXPORT_SQL.format(where=where)), params)
    return [_normalise_row(dict(row._mapping)) for row in rows]


def to_csv(rows: list[dict[str, Any]]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow([header for _key, header in EXPORT_FIELDS])
    for row in rows:
        writer.writerow([_csv_cell(row.get(key)) for key, _header in EXPORT_FIELDS])
    return buffer.getvalue()


def to_json(rows: list[dict[str, Any]]) -> str:
    """JSON export keeps the raw keys and nulls; a machine reading it should not
    have to guess what an empty string meant. `default=str` is only a net for the
    text timestamps this query selects, never for money or flags — those are
    normalised before they get here."""
    return json.dumps(
        [{key: row.get(key) for key, _header in EXPORT_FIELDS} for row in rows],
        ensure_ascii=False,
        indent=2,
        default=str,
    )


# `is_signed` and `is_sprayed_edge` are 0/1 INTEGERs in SQLite (D9 keeps no booleans
# in the schema beyond these flags), so a raw SQL row would export "1"/"0" — and JSON
# would carry the number where a reader expects true/false. Coercing at the row
# boundary keeps both formats honest without pretending SQLite gave us Python bools.
BOOLEAN_COLUMNS = frozenset(
    {
        "is_signed",
        "is_sprayed_edge",
    }
)


def _normalise_row(row: dict[str, Any]) -> dict[str, Any]:
    """One place where a raw SQL row becomes a value both formats can render
    honestly: flags become real booleans and money becomes a float."""
    return {
        key: bool(value)
        if key in BOOLEAN_COLUMNS and value is not None
        else float(value)
        if isinstance(value, Decimal)
        else value
        for key, value in row.items()
    }


def _csv_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    # No Decimal branch: money is normalised to float by _normalise_row above, so a
    # cell formatter that silently stringified a Decimal would be dead code here.
    return str(value)
