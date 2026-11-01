# Idempotent dev/demo fixture loader. Run it with `make seed` or
# `python -m app.seed`.
#
# Strategy: rebuild-from-scratch for the demo user only. Every run deletes that
# user's loans, photos, books, links, authors, series and rooms, then re-inserts
# the fixture, so two consecutive runs leave identical row counts. Rows belonging
# to any other user are never touched — that is what makes this safe to run
# against a DB file that already holds real data.
#
# Dimensions come from the format's typical size with a deterministic nudge
# derived from the title, plus a page-count-derived thickness. Deterministic
# because a fixture that changes shape between runs cannot be asserted against;
# nudged because a shelf where all 150 books are exactly the same size proves
# nothing about the sizing UI.
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

import bcrypt
from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.formats import FORMAT_BY_KEY
from app.seed_data import (
    BOOKS,
    DEMO_PASSWORD,
    DEMO_USERNAME,
    GIFTS,
    LOANS,
    PHOTO_PROVIDERS,
    ROOMS,
    SIGNED,
    SPRAYED_EDGE,
)

# Formats whose size is set by the box or the binding, not by the page block.
FIXED_SIZE_FORMATS = frozenset({"boxed_set", "dictionary", "oversized_folio", "board_book"})

# Millimetres of spine per page, roughly 45µm paper.
MM_PER_PAGE = 0.04


@dataclass(frozen=True)
class SeedSummary:
    """Row counts written by one seed run; the tests assert against these."""

    user_id: int
    rooms: int
    series: int
    authors: int
    books: int
    book_authors: int
    book_genres: int
    loans: int
    photos: int

    def as_dict(self) -> dict[str, int]:
        return {
            "rooms": self.rooms,
            "series": self.series,
            "authors": self.authors,
            "books": self.books,
            "book_authors": self.book_authors,
            "book_genres": self.book_genres,
            "loans": self.loans,
            "photos": self.photos,
        }


def sort_key(name: str) -> str:
    """Surname-first sort key: 'Dan Abnett' -> 'abnett, dan'."""
    parts = [p for p in name.split() if p]
    if len(parts) <= 1:
        return name.lower()
    return f"{parts[-1].lower()}, {' '.join(parts[:-1]).lower()}"


def dimensions(fmt_key: str, title: str, pages: int | None) -> tuple[int, int, int]:
    """(height, width, thickness) in mm for one fixture book."""
    fmt = FORMAT_BY_KEY[fmt_key]
    if fmt_key in FIXED_SIZE_FORMATS:
        return fmt.height_mm, fmt.width_mm, fmt.thickness_mm

    digest = int(hashlib.sha256(title.encode("utf-8")).hexdigest()[:8], 16)
    height = fmt.height_mm + (digest % 7) - 3
    width = fmt.width_mm + ((digest >> 3) % 7) - 3
    if pages:
        thickness = max(6, round(pages * MM_PER_PAGE) + fmt.thickness_mm // 3)
    else:
        thickness = fmt.thickness_mm
    return height, width, thickness


def _validate() -> None:
    """Fail loudly on fixture typos instead of letting a CHECK constraint do it."""
    titles = [row[0] for row in BOOKS]
    duplicates = {t for t in titles if titles.count(t) > 1}
    allowed_duplicates = {"The Count of Monte Cristo", "The Very Hungry Caterpillar"}
    unexpected = duplicates - allowed_duplicates
    if unexpected:
        raise ValueError(f"fixture has unplanned duplicate rows: {sorted(unexpected)}")

    for row in BOOKS:
        (
            title,
            _authors,
            series,
            position,
            fmt,
            _year,
            _pages,
            _status,
            _state,
            room,
            genre,
            _price,
        ) = row
        if fmt not in FORMAT_BY_KEY:
            raise ValueError(f"{title!r}: unknown format {fmt!r}")
        # A boxed set inside a series carries no position; a position without a
        # series is always a typo.
        if series is None and position is not None:
            raise ValueError(f"{title!r}: position without a series")
        if room is not None and room not in ROOMS:
            raise ValueError(f"{title!r}: room {room!r} is not in seed_data.ROOMS")
        if genre not in {g.notation for g in _all_genres()}:
            raise ValueError(f"{title!r}: genre {genre!r} is not in the curated list")

    borrowed = {row[0] for row in BOOKS if row[7] == "borrowed"}
    loaned = {row[0] for row in LOANS}
    if borrowed != loaned:
        raise ValueError(f"'borrowed' rows and LOANS disagree: {sorted(borrowed ^ loaned)}")

    gift_rows = {row[0] for row in BOOKS if row[7] == "gifted"}
    if gift_rows != set(GIFTS):
        raise ValueError(f"'gifted' rows and GIFTS disagree: {sorted(gift_rows ^ set(GIFTS))}")

    known = set(titles)
    for flag in SIGNED | SPRAYED_EDGE:
        if flag[0] not in known:
            raise ValueError(f"flag references an unknown title: {flag[0]!r}")
    for title in PHOTO_PROVIDERS:
        if title not in known:
            raise ValueError(f"photo references an unknown title: {title!r}")


def _all_genres() -> tuple[Any, ...]:
    from app.genres import GENRES

    return GENRES


def _wipe(session, owner_id: int) -> None:
    """Delete the demo user's rows, children before parents."""
    children = (
        "loans",
        "photos",
        "book_genres",
        "book_authors",
        "books",
        "authors",
        "series",
        "rooms",
    )
    for table in children:
        session.execute(text(f"DELETE FROM {table} WHERE owner_id = :o"), {"o": owner_id})


def seed(engine: Engine, *, verbose: bool = False) -> SeedSummary:
    """Rebuild the demo fixture. Safe and idempotent; returns what it wrote."""
    _validate()

    with engine.begin() as session:
        row = session.execute(
            text("SELECT id FROM users WHERE username = :u"), {"u": DEMO_USERNAME}
        ).first()
        if row is None:
            password_hash = bcrypt.hashpw(DEMO_PASSWORD.encode("utf-8"), bcrypt.gensalt()).decode()
            owner_id = int(
                session.execute(
                    text("INSERT INTO users (username, password_hash) VALUES (:u, :h)"),
                    {"u": DEMO_USERNAME, "h": password_hash},
                ).lastrowid
            )
        else:
            owner_id = int(row[0])

        _wipe(session, owner_id)

        room_ids = {
            name: _insert(session, "rooms", {"owner_id": owner_id, "name": name}) for name in ROOMS
        }

        series_ids: dict[str, int] = {}
        for name in _ordered_unique(row[2] for row in BOOKS):
            series_ids[name] = _insert(session, "series", {"owner_id": owner_id, "name": name})

        author_ids: dict[str, int] = {}
        for name in _ordered_unique(author for row in BOOKS for author in _split_authors(row[1])):
            author_ids[name] = _insert(
                session,
                "authors",
                {"owner_id": owner_id, "name": name, "sort_key": sort_key(name)},
            )

        book_ids: dict[tuple[str, str], int] = {}
        for (
            title,
            authors,
            series,
            position,
            fmt,
            year,
            pages,
            status,
            state,
            room,
            genre,
            price,
        ) in BOOKS:
            height, width, thickness = dimensions(fmt, title, pages)
            book_id = _insert(
                session,
                "books",
                {
                    "owner_id": owner_id,
                    "title": title,
                    "format": fmt,
                    "publication_year": year,
                    "page_count": pages,
                    "language": "eng",
                    "room_id": room_ids[room] if room else None,
                    "series_id": series_ids[series] if series else None,
                    "series_position": position,
                    "primary_genre_notation": genre,
                    "price": price,
                    "ownership_status": status,
                    "reading_state": state,
                    "gifted_to": GIFTS.get(title) if status == "gifted" else None,
                    "is_signed": (title, fmt) in SIGNED,
                    "is_sprayed_edge": (title, fmt) in SPRAYED_EDGE,
                    "height_mm": height,
                    "width_mm": width,
                    "thickness_mm": thickness,
                },
            )
            book_ids[(title, fmt)] = book_id

            for position_index, author in enumerate(_split_authors(authors), start=1):
                session.execute(
                    text(
                        "INSERT INTO book_authors (book_id, author_id, owner_id, position)"
                        " VALUES (:b, :a, :o, :p)"
                    ),
                    {
                        "b": book_id,
                        "a": author_ids[author],
                        "o": owner_id,
                        "p": position_index,
                    },
                )
            session.execute(
                text(
                    "INSERT INTO book_genres (book_id, genre_notation, owner_id)"
                    " VALUES (:b, :g, :o)"
                ),
                {"b": book_id, "g": genre, "o": owner_id},
            )

        loans_written = 0
        for title, borrower, loaned_at, expected, returned in LOANS:
            book_id = _only_book(book_ids, title)
            session.execute(
                text(
                    "INSERT INTO loans (book_id, owner_id, borrowed_to, loaned_at,"
                    " expected_return_date, returned_at) VALUES (:b, :o, :who, :when, :exp, :ret)"
                ),
                {
                    "b": book_id,
                    "o": owner_id,
                    "who": borrower,
                    "when": loaned_at,
                    "exp": expected,
                    "ret": returned,
                },
            )
            loans_written += 1

        photos_written = 0
        for title, slots in PHOTO_PROVIDERS.items():
            book_id = _only_book(book_ids, title)
            for slot in slots:
                session.execute(
                    text(
                        "INSERT INTO photos (book_id, owner_id, slot, provider_filename)"
                        " VALUES (:b, :o, :s, :f)"
                    ),
                    {
                        "b": book_id,
                        "o": owner_id,
                        "s": slot,
                        # Same shape the cover cache writes, so the cache path is
                        # testable without a live provider.
                        "f": f"openlibrary/{book_id}-{slot}.jpg",
                    },
                )
                photos_written += 1

        if verbose:
            print(f"seeded demo user id={owner_id}")

    return SeedSummary(
        user_id=owner_id,
        rooms=len(ROOMS),
        series=len(series_ids),
        authors=len(author_ids),
        books=len(BOOKS),
        book_authors=sum(len(_split_authors(row[1])) for row in BOOKS),
        book_genres=len(BOOKS),
        loans=loans_written,
        photos=photos_written,
    )


def _insert(session, table: str, values: dict[str, Any]) -> int:
    columns = ", ".join(values)
    placeholders = ", ".join(f":{key}" for key in values)
    result = session.execute(
        text(f"INSERT INTO {table} ({columns}) VALUES ({placeholders})"), values
    )
    return int(result.lastrowid)


def _split_authors(field: str) -> list[str]:
    return [part.strip() for part in field.split(",") if part.strip()]


def _ordered_unique(values) -> list[str]:
    """First-seen order, deduplicated."""
    seen: dict[str, None] = {}
    for value in values:
        if value is not None:
            seen.setdefault(value, None)
    return list(seen)


def _only_book(book_ids: dict[tuple[str, str], int], title: str) -> int:
    matches = [book_id for (name, _fmt), book_id in book_ids.items() if name == title]
    if len(matches) != 1:
        raise ValueError(f"{title!r} must match exactly one seeded book, matched {len(matches)}")
    return matches[0]


def main() -> None:
    from app.db import engine

    summary = seed(engine)
    print(f"Seeded {DEMO_USERNAME} (id={summary.user_id}), password '{DEMO_PASSWORD}':")
    for name, count in summary.as_dict().items():
        print(f"  {name:14} {count}")


if __name__ == "__main__":
    main()
