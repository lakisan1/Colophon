"""Add books.illustrator: the artist behind a manga or comic, distinct from the author.

Manga and comic books are usually drawn by someone other than the writer. This
adds a plain, optional free-text field for that person (a single name or a
comma-separated list).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("books", sa.Column("illustrator", sa.Text, nullable=True))


def downgrade() -> None:
    op.execute("ALTER TABLE books DROP COLUMN illustrator")
