"""Add books.book_designer: the person who designed the cover or typography.

Cover and typography design is its own craft credit — Chip Kidd on the flaps,
Peter Mendelsund inside the house — and like illustrator/translator/editor it is
a plain optional free-text field that lives behind the form's Advanced
disclosure with pick-or-type-new suggestions.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("books", sa.Column("book_designer", sa.Text, nullable=True))


def downgrade() -> None:
    op.execute("ALTER TABLE books DROP COLUMN book_designer")
