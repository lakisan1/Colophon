# The development-only identity behind COLOPHON_DEV_AUTH_BYPASS=1, kept out of
# deps.py so the real authentication path stays readable.
#
# It signs in as the seeded demo account when the database has one, because
# `make seed` puts the demo library there; without this `make dev` shows an empty
# shelf next to 210 books. The synthetic user keeps the bypass usable on a
# database that has not been seeded, or not even migrated.
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.deps import CurrentUser


def dev_user(session: Session) -> CurrentUser:
    from app.seed_data import DEMO_USERNAME

    try:
        row = session.execute(
            text("SELECT id, username FROM users WHERE username = :u"), {"u": DEMO_USERNAME}
        ).first()
    except OperationalError:  # no users table yet
        return CurrentUser(id=0, username="dev")
    if row is None:
        return CurrentUser(id=0, username="dev")
    return CurrentUser(id=int(row[0]), username=str(row[1]))
