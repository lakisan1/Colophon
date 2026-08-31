"""Add settings.google_books: per-user opt-in to the Google Books search provider.

Open Library is the default provider for the "Look it up" search. A user can
opt in to also query Google Books (which needs a server-side GOOGLE_BOOKS_KEY);
their results then appear alongside Open Library's. Stored as a 0/1 flag on
the per-user settings row, resolved by app/prefs.py like every other pref.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "settings",
        sa.Column("google_books", sa.Boolean, nullable=False, server_default=sa.text("0")),
    )


def downgrade() -> None:
    op.drop_column("settings", "google_books")
