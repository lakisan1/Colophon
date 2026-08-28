# Hardcoded physical format enum (D8): book-shaped items only — no e-books, no
# audiobooks (D1). Each format carries the dimensions it typically implies,
# because SPEC §2 says dimensions are "picked from the format or entered by
# hand". The keys are exactly what books.format stores; the migration's CHECK
# constraint repeats this list and a test keeps the two in sync.
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Format:
    """One allowed value of books.format plus its typical millimetre size."""

    key: str
    label: str
    height_mm: int
    width_mm: int
    thickness_mm: int


FORMATS: tuple[Format, ...] = (
    Format("mass_market_paperback", "Mass-market paperback", 110, 175, 20),
    Format("pocket_a6", "Pocket (A6)", 105, 148, 20),
    Format("paperback", "Paperback", 130, 200, 20),
    Format("trade_paperback", "Trade paperback (6 x 9 in)", 152, 229, 20),
    Format("manga", "Manga (tankobon)", 115, 175, 15),
    Format("hardcover", "Hardcover", 155, 235, 30),
    Format("large_print", "Large print", 178, 254, 30),
    Format("dictionary", "Dictionary / encyclopedia", 190, 260, 80),
    Format("oversized_folio", "Oversized / folio", 300, 420, 40),
    Format("boxed_set", "Boxed set / slipcase", 160, 240, 160),
    Format("leather_bound", "Leather-bound gift edition", 125, 180, 35),
    Format("spiral_bound", "Spiral-bound", 210, 297, 15),
    Format("board_book", "Board book", 150, 150, 30),
)

FORMAT_BY_KEY: dict[str, Format] = {fmt.key: fmt for fmt in FORMATS}

# Convenience for forms and the migration-sync test.
FORMAT_KEYS: tuple[str, ...] = tuple(fmt.key for fmt in FORMATS)
