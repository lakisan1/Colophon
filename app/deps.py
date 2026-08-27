# Request dependencies. current_user is the single gate every signed-in route uses;
# M5 replaces its body with real DB-session lookups (T8). Until then a dev bypass
# flag keeps the app usable without auth so earlier milestones stay testable.
from __future__ import annotations

from dataclasses import dataclass

from app.settings import settings


class LoginRequired(Exception):
    """No way to identify the user. The app's exception handler redirects to /login."""


@dataclass(frozen=True)
class CurrentUser:
    """Stand-in for the users-table row that M5 will load; id 0 means 'not a real user'."""

    id: int
    username: str


def current_user() -> CurrentUser:
    if settings.dev_auth_bypass:
        return CurrentUser(id=0, username="dev")
    raise LoginRequired
