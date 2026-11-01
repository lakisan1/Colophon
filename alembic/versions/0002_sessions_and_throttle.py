"""Auth sessions and login throttling.

Two tables, both throwaway by design — losing them costs nobody any data.

`sessions` is the DB-backed session behind the HTTP-only cookie (SPEC §13). It
carries its own `csrf_token` so a submitted token can be checked against the row
that issued it, and three timestamps that make the expiry rules readable:
`created_at` (starts the 90-day hard cap), `last_seen_at` (starts the 30-day
slide) and `expires_at` (the cap, computed once at login so the check is a
comparison rather than arithmetic on every request).

`login_attempts` records failures per identifier ("ip:1.2.3.4", "user:admin") so
lockout survives a restart. It is pruned, never read in bulk.

The `admin` / `admin` bootstrap account lands here because a migrated database
must be usable on its own. The hash is bcrypt with a fresh salt, and SPEC §13's
non-hideable banner warns until it is changed (D11).
"""

from __future__ import annotations

import bcrypt
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            csrf_token TEXT NOT NULL,
            created_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            ip TEXT,
            user_agent TEXT
        )
        """
    )
    op.execute("CREATE INDEX ix_sessions_user ON sessions(user_id)")
    op.execute("CREATE INDEX ix_sessions_expires ON sessions(expires_at)")

    op.execute(
        """
        CREATE TABLE login_attempts (
            id INTEGER PRIMARY KEY,
            identifier TEXT NOT NULL,
            failed_at TEXT NOT NULL
        )
        """
    )
    op.execute("CREATE INDEX ix_login_attempts_identifier ON login_attempts(identifier, failed_at)")

    # Bootstrap account. Warning-only by design: the app shows a persistent
    # banner while this password is still in place (D11).
    digest = bcrypt.hashpw(b"admin", bcrypt.gensalt(rounds=12)).decode()
    # bcrypt output is base64 with a fixed charset, so interpolating it into the
    # statement cannot break the SQL or inject anything.
    op.execute(
        "INSERT INTO users (username, password_hash, created_at)"
        f" VALUES ('admin', '{digest}', datetime('now'))"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS login_attempts")
    op.execute("DROP INDEX IF EXISTS ix_sessions_expires")
    op.execute("DROP INDEX IF EXISTS ix_sessions_user")
    op.execute("DROP TABLE IF EXISTS sessions")
    op.execute("DELETE FROM users WHERE username = 'admin'")
