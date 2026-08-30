"""Add books.translator, books.editor, books.original_title, books.original_language.

These are the less-common cataloguing fields, so on the form they live behind an
"Advanced" disclosure: a translator or editor (with the same pick-or-type-new
behaviour as author/illustrator) and the original title/language of a translated
work. All optional, free-text columns.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("books", sa.Column("translator", sa.Text, nullable=True))
    op.add_column("books", sa.Column("editor", sa.Text, nullable=True))
    op.add_column("books", sa.Column("original_title", sa.Text, nullable=True))
    op.add_column("books", sa.Column("original_language", sa.Text, nullable=True))


def downgrade() -> None:
    op.execute("ALTER TABLE books DROP COLUMN original_language")
    op.execute("ALTER TABLE books DROP COLUMN original_title")
    op.execute("ALTER TABLE books DROP COLUMN editor")
    op.execute("ALTER TABLE books DROP COLUMN translator")
