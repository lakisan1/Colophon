"""Add books.print_year: the year the physical copy was printed.

Publication year answers "when was this edition published"; print year
answers "when was this particular copy run off the press". They diverge for
later printings (a 1972 printing of a 1960 book), so they are separate fields
and both are optional. The CHECK keeps impossible years out at the database
even if some future writer forgets the form validation, and NULL means "not
known" — which is the common case for an old copy.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "books",
        sa.Column(
            "print_year",
            sa.Integer,
            sa.CheckConstraint(
                "print_year IS NULL OR print_year BETWEEN 1 AND 9999",
                name="ck_books_print_year",
            ),
        ),
    )


def downgrade() -> None:
    # Native ALTER TABLE ... DROP COLUMN (SQLite >= 3.35): no batch table
    # rebuild, so the FTS triggers bound to `books` survive, and the
    # column-level CHECK is removed with the column.
    op.execute("ALTER TABLE books DROP COLUMN print_year")
