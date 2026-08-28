# Request dependencies. `current_user` is the single gate every signed-in route
# uses: it reads the session cookie, enforces the hard cap, and slides the expiry.
# `require_login_form` adds CSRF on top for mutations (SPEC §13).
#
# The dev bypass (COLOPHON_DEV_AUTH_BYPASS=1) stays for local work: it signs in as
# the seeded demo account when the database has one. With the bypass on there is
# no session cookie anywhere, so CSRF checks are skipped — a cross-site request
# cannot ride a cookie the application never reads.
from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.auth import (
    SESSION_COOKIE,
    load_session,
    touch_session,
)
from app.db import get_session
from app.settings import settings


class LoginRequired(Exception):
    """No valid session. The app's exception handler redirects to /login."""


class CsrfFailed(Exception):
    """A mutation arrived without a token that matches the one we issued."""


@dataclass(frozen=True)
class CurrentUser:
    """The signed-in identity, plus the CSRF secret that came with this session."""

    id: int
    username: str
    session_id: str | None = None
    csrf_token: str | None = None


def current_user(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
) -> CurrentUser:
    if settings.dev_auth_bypass:
        from app.deps_dev import dev_user

        user = dev_user(session)
        request.state.colophon_default_credentials = False
        return user

    row = load_session(session, request.cookies.get(SESSION_COOKIE))
    if row is None:
        raise LoginRequired
    touch_session(session, row)

    # The non-hideable banner (D11) reads this; computing it here means one
    # memoised hash comparison instead of one per template.
    from sqlalchemy.exc import OperationalError

    from app.auth import uses_default_credentials

    try:
        request.state.colophon_default_credentials = uses_default_credentials(
            session, int(row["user_id"])
        )
    except OperationalError:
        request.state.colophon_default_credentials = False

    return CurrentUser(
        id=int(row["user_id"]),
        username=str(row["username"]),
        session_id=str(row["id"]),
        csrf_token=str(row["csrf_token"]),
    )
