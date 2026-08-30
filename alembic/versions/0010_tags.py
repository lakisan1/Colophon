"""Add tags: a many-to-many set of custom labels per book.

Tags let a reader organise beyond genre (e.g. "book club", "reference", "to read
with the kids"). Mirror the authors pattern: a tags row per (owner, name) and a
book_tags link, all owning rows that cascade when the user or book is deleted.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tags",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "owner_id",
            sa.Integer,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column(
            "created_at",
            sa.Text,
            nullable=False,
            server_default=sa.text("strftime('%Y-%m-%dT%H:%M:%SZ', 'now')"),
        ),
        sa.UniqueConstraint("owner_id", "name", name="uq_tags_owner_name"),
    )
    op.create_table(
        "book_tags",
        sa.Column(
            "book_id",
            sa.Integer,
            sa.ForeignKey("books.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "tag_id",
            sa.Integer,
            sa.ForeignKey("tags.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "owner_id",
            sa.Integer,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("book_id", "tag_id"),
    )


def downgrade() -> None:
    op.drop_table("book_tags")
    op.drop_table("tags")
