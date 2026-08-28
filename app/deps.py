# Request dependencies. current_user is the single gate every signed-in route uses;
# M5 replaces its body with real DB-session lookups (T8). Until then a dev bypass
# flag keeps the app usable without auth so earlier milestones stay testable.
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.settings import settings


class LoginRequired(Exception):
    """No way to identify the user. The app's exception handler redirects to /login."""


@dataclass(frozen=True)
class CurrentUser:
    """Stand-in for the users-table row that M5 will load; id 0 means 'not a real user'."""

    id: int
    username: str


def current_user() -> CurrentUser:
    if not settings.dev_auth_bypass:
        raise LoginRequired
    # `make seed` puts the demo library on the demo account, so the bypass signs
    # in as that account when it exists — otherwise `make dev` shows an empty
    # shelf next to 210 seeded books. The synthetic user keeps the bypass
    # working on a database that has not been seeded (or not even migrated).
    from app.db import SessionLocal

    return _seeded_demo_user(SessionLocal) or CurrentUser(id=0, username="dev")


def _seeded_demo_user(session_factory) -> CurrentUser | None:
    """The seeded demo account if this database has one. Takes the factory so
    tests can point it at a throwaway engine."""
    from app.seed_data import DEMO_USERNAME

    try:
        with session_factory() as session:
            row = session.execute(
                text("SELECT id, username FROM users WHERE username = :u"), {"u": DEMO_USERNAME}
            ).first()
    except OperationalError:  # no users table yet
        return None
    return CurrentUser(id=int(row[0]), username=str(row[1])) if row else None
