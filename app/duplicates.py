# Duplicate detection for the add/edit form (D3: warn, never block).
#
# A person cataloguing a physical shelf ends up with two copies of the same title
# on purpose — different formats, a gifted duplicate, a book bought twice because
# the first was lent out and never came back. So a hit here is a message with a
# link, not an error, and the save proceeds.
from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.orm import Session

# "The Hobbit" and "Hobbit, The" are the same book to a human scanning a shelf.
_LEADING_ARTICLES = re.compile(r"^(the|a|an)\s+", re.IGNORECASE)
_TRAILING_ARTICLES = re.compile(r"[,\s]+(the|a|an)$", re.IGNORECASE)
_PUNCTUATION = re.compile(r"[^\w\s]", re.UNICODE)


@dataclass(frozen=True)
class DuplicateHit:
    book_id: int
    title: str
    author_label: str
    isbn: str | None
    ownership_status: str
    reason: str


def normalise_title(title: str) -> str:
    cleaned = _TRAILING_ARTICLES.sub("", _LEADING_ARTICLES.sub("", title.strip()))
    return _PUNCTUATION.sub("", cleaned).casefold().strip()


def find_duplicates(
    session: Session,
    owner_id: int,
    *,
    isbn: str | None = None,
    title: str | None = None,
    exclude_book_id: int | None = None,
) -> list[DuplicateHit]:
    """Books of this owner that look like the one being saved."""
    hits: dict[int, DuplicateHit] = {}

    if isbn:
        for row in _rows(session, "b.isbn = :isbn", {"isbn": isbn}, owner_id, exclude_book_id):
            hits[row.book_id] = DuplicateHit(
                **row.__dict__, reason="Same ISBN is already on the shelf."
            )

    if title:
        wanted = normalise_title(title)
        if wanted:
            for row in _rows(session, "1 = 1", {}, owner_id, exclude_book_id):
                if normalise_title(row.title) == wanted and row.book_id not in hits:
                    hits[row.book_id] = DuplicateHit(
                        **row.__dict__, reason="Same title is already on the shelf."
                    )

    return list(hits.values())


def _rows(session: Session, clause: str, params: dict, owner_id: int, exclude_book_id: int | None):
    """Candidate rows carrying the display author, for a readable warning."""
    sql = f"""
        SELECT b.id AS book_id, b.title AS title, b.isbn AS isbn,
               b.ownership_status AS ownership_status,
               COALESCE(a.name, '') AS author_label
        FROM books b
        LEFT JOIN book_authors ba ON ba.book_id = b.id AND ba.position = 1
        LEFT JOIN authors a ON a.id = ba.author_id
        WHERE b.owner_id = :owner_id
          AND ({clause})
          AND (:exclude_id IS NULL OR b.id <> :exclude_id)
    """
    bound = {"owner_id": owner_id, "exclude_id": exclude_book_id, **params}
    return [Row(record) for record in session.execute(text(sql), bound)]


class Row:
    """Tiny attribute view over a result record, so DuplicateHit stays a dataclass."""

    def __init__(self, record) -> None:
        self.__dict__.update(dict(record._mapping))
