# Dashboard counts (SPEC §11). One aggregate query per axis rather than one query
# per number: the dashboard is a single page load and the numbers have to agree
# with each other, which separate queries taken at different moments do not.
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.orm import Session


@dataclass(frozen=True)
class Dashboard:
    """The five ownership counts SPEC §11 asks for, plus context that costs nothing."""

    owned: int = 0
    wanted: int = 0
    borrowed: int = 0
    sold: int = 0
    gifted: int = 0
    total: int = 0
    reading: dict[str, int] | None = None
    pages: int = 0
    authors: int = 0
    series: int = 0
    loans_out: int = 0
    # Total value of the books the owner currently holds (owned/wanted/borrowed).
    # Sold and gifted books are no longer part of the collection, so their price
    # is not counted. 0.0 when nothing is held or no price is set.
    value: float = 0.0

    @property
    def gone(self) -> int:
        """Shelf 3 is sold and gifted together (SPEC §3)."""
        return self.sold + self.gifted


def dashboard(session: Session, owner_id: int) -> Dashboard:
    counts = dict(
        session.execute(
            text(
                "SELECT ownership_status, count(*) FROM books"
                " WHERE owner_id = :o GROUP BY ownership_status"
            ),
            {"o": owner_id},
        ).all()
    )
    reading = dict(
        session.execute(
            text(
                "SELECT reading_state, count(*) FROM books"
                " WHERE owner_id = :o GROUP BY reading_state"
            ),
            {"o": owner_id},
        ).all()
    )
    totals = session.execute(
        text(
            """
            SELECT
                count(*)                                   AS total,
                COALESCE(sum(page_count), 0)               AS pages,
                (SELECT count(DISTINCT ba.author_id) FROM book_authors ba
                   JOIN books b2 ON b2.id = ba.book_id WHERE b2.owner_id = :o)  AS authors,
                (SELECT count(*) FROM series s WHERE s.owner_id = :o)           AS series,
                (SELECT count(*) FROM loans l
                   WHERE l.owner_id = :o AND l.returned_at IS NULL)             AS loans_out,
                (SELECT COALESCE(sum(price), 0) FROM books
                  WHERE owner_id = :o
                    AND ownership_status NOT IN ('sold', 'gifted')) AS value
            FROM books WHERE owner_id = :o
            """
        ),
        {"o": owner_id},
    ).first()

    return Dashboard(
        owned=int(counts.get("owned", 0)),
        wanted=int(counts.get("wanted", 0)),
        borrowed=int(counts.get("borrowed", 0)),
        sold=int(counts.get("sold", 0)),
        gifted=int(counts.get("gifted", 0)),
        total=int(totals[0] or 0),
        reading={str(key): int(value) for key, value in reading.items()},
        pages=int(totals[1] or 0),
        authors=int(totals[2] or 0),
        series=int(totals[3] or 0),
        loans_out=int(totals[4] or 0),
        value=float(totals[5] or 0),
    )
