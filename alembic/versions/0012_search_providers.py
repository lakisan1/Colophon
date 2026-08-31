"""Replace settings.google_books with settings.search_providers: the list of enabled ids.

A 0/1 flag could only ever mean "Open Library, and Google Books or not". A
comma-separated list of provider ids means adding a provider is a registry entry
in app/lookup.py rather than a migration and a new column: the user's row simply
names the providers they enabled. Rows that had opted in to Google Books keep it
alongside Open Library — the Open Library default never disappears.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "settings",
        sa.Column(
            "search_providers",
            sa.Text,
            nullable=False,
            server_default=sa.text("'openlibrary'"),
        ),
    )
    op.execute(
        "UPDATE settings SET search_providers = 'openlibrary,googlebooks' WHERE google_books = 1"
    )
    op.drop_column("settings", "google_books")


def downgrade() -> None:
    op.add_column(
        "settings",
        sa.Column("google_books", sa.Boolean, nullable=False, server_default=sa.text("0")),
    )
    op.execute("UPDATE settings SET google_books = 1 WHERE search_providers LIKE '%googlebooks%'")
    op.drop_column("settings", "search_providers")
