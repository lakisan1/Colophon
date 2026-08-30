"""Add users.is_admin: marks the single admin account.

The admin account can create and delete other users but can never be deleted or
renamed itself. Every other account is seeded with is_admin = 0 (a plain user).
The bootstrapped admin account (migration 0002) is the one flagged here.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "is_admin",
            sa.Integer,
            sa.CheckConstraint("is_admin IN (0, 1)", name="ck_users_is_admin"),
            nullable=False,
            server_default="0",
        ),
    )
    op.execute("UPDATE users SET is_admin = 1 WHERE username = 'admin'")


def downgrade() -> None:
    op.execute("ALTER TABLE users DROP COLUMN is_admin")
