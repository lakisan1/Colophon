"""Add books.cloth_hue: the chosen spine colour (a hue, 0-359); NULL means automatic.

The spine colour is usually derived from the author surname (A5: the same author
looks the same on every shelf). A person can now pick a colour when adding or
editing a book. It is stored as a hue so it maps straight to the CSS hsl() the
shelf paints.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "books",
        sa.Column(
            "cloth_hue",
            sa.Integer,
            sa.CheckConstraint(
                "cloth_hue IS NULL OR cloth_hue BETWEEN 0 AND 359",
                name="ck_books_cloth_hue",
            ),
        ),
    )


def downgrade() -> None:
    op.execute("ALTER TABLE books DROP COLUMN cloth_hue")
