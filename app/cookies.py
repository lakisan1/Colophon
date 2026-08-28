# Session and CSRF cookies, in one place because their flags are the security
# boundary (SPEC §13): the session cookie is HttpOnly so scripts cannot read it,
# SameSite=Lax so a cross-site POST never carries it, and Path=/ because the
# whole app is one site.
#
# `Secure` is off by default and switched on with COLOPHON_COOKIE_SECURE=1. That
# is deliberate: local development and any plain-HTTP LAN install would otherwise
# be unable to log in at all. TLS deployments must set it — see docs/GITHUB.md.
from __future__ import annotations

from fastapi import Response

from app.auth import CSRF_COOKIE, SESSION_COOKIE
from app.settings import settings

SESSION_MAX_AGE = 90 * 24 * 60 * 60  # the hard cap; the server enforces it anyway


def set_session_cookies(response: Response, session_id: str, csrf_token: str) -> None:
    response.set_cookie(
        key=SESSION_COOKIE,
        value=session_id,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )
    # Readable-by-server only. The value is echoed into every form and compared
    # on submission (double submit), which a cross-site form cannot do.
    response.set_cookie(
        key=CSRF_COOKIE,
        value=csrf_token,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )


def clear_session_cookies(response: Response) -> None:
    for key in (SESSION_COOKIE, CSRF_COOKIE):
        response.delete_cookie(key, path="/")
