"""Add settings.provider_keys: every per-user API key in one JSON column.

A keyed provider used to be answerable only with the server's own environment key,
so a reader with their own Google Books key had no way to use it. The obvious
column-per-provider shape makes every future provider a migration; a JSON object of
provider id -> key means the registry in app/lookup.py stays the only place that
knows which providers exist. The server key remains the fallback — an empty entry
means "no personal key", not "no key".
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "settings",
        sa.Column("provider_keys", sa.Text, nullable=False, server_default=sa.text("''")),
    )


def downgrade() -> None:
    op.drop_column("settings", "provider_keys")
