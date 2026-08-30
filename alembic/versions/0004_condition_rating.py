"""Add books.condition_rating: a 1-5 mark on the physical copy.

Condition is the one attribute of a used book a collector compares across the
whole library ("is this copy worth upgrading?") and the only one free text
cannot answer well: "goodish", "VG-", "fine" sort and filter badly. Five points
mirrors the dealer's scale, and the words live in the UI
(app/routers/books.py CONDITION_LABELS), so the stored value stays a plain
integer and the form, the page and any future filter all say the same thing
for the same number.

NULL means "not rated", which is most of the library: rating is optional and a
missing rating must never read as a 1. The CHECK keeps bad data out at the
database even if some future writer forgets the form validation.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # SQLite ADD COLUMN accepts a column-level CHECK; NULL passes it (a CHECK
    # only fails on FALSE, never on NULL), so existing unrated rows stay put.
    op.add_column(
        "books",
        sa.Column(
            "condition_rating",
            sa.Integer,
            sa.CheckConstraint(
                "condition_rating IS NULL OR condition_rating BETWEEN 1 AND 5",
                name="ck_books_condition_rating",
            ),
        ),
    )


def downgrade() -> None:
    # Native ALTER TABLE ... DROP COLUMN (SQLite >= 3.35). Alembic's SQLite
    # backend insists on batch mode for op.drop_column, but a batch table
    # rebuild breaks the FTS triggers bound to `books`; the native drop does
    # no rebuild and removes the column-level CHECK with the column.
    op.execute("ALTER TABLE books DROP COLUMN condition_rating")
