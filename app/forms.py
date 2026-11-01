# One place that reads a posted form, so "every mutation carries a CSRF token"
# (SPEC §13) is one rule instead of a per-route habit someone forgets.
#
# Double submit: the token lives in a cookie we set and in a hidden field of the
# form, and both must match. A cross-site form can send the cookie but cannot
# read it, so it cannot reproduce the field. With the dev bypass there is no
# session and no cookie in play, so the check is skipped — see app/deps.py.
from __future__ import annotations

from secrets import compare_digest
from typing import Annotated

from fastapi import Depends, Request

from app.auth import CSRF_COOKIE
from app.deps import CsrfFailed, CurrentUser, current_user


async def read_form(
    request: Request,
    user: Annotated[CurrentUser, Depends(current_user)],
) -> dict:
    """Parsed form values, with the mutation's CSRF token verified first."""
    form = await request.form()
    values = dict(form.items())
    verify_csrf(user, values.get("csrf_token"), request.cookies.get(CSRF_COOKIE))
    return values


def verify_csrf(user: CurrentUser, submitted: str | None, cookie: str | None) -> None:
    if user.csrf_token is None:
        return  # dev bypass: nothing to protect yet
    if not submitted or not cookie:
        raise CsrfFailed("Missing CSRF token.")
    # Constant-time on purpose: a token check is exactly the kind of comparison
    # that must not leak how much of a guessed prefix was right.
    if not compare_digest(submitted, cookie) or not compare_digest(submitted, user.csrf_token):
        raise CsrfFailed("CSRF token does not match this session.")
