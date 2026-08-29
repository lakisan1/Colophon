# Login and logout (M5). The public surface is this screen and nothing else
# (SPEC §13): every other route redirects here when there is no valid session.
#
# Failure messages stay deliberately vague. "Wrong password" tells an attacker the
# username exists; "username or password is wrong" does not. Lockout is separate
# and explicit, because a locked account has to know it is locked or the person
# just keeps typing.
from __future__ import annotations

from secrets import token_urlsafe
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.auth import (
    CSRF_COOKIE,
    clear_failures,
    create_session,
    find_user,
    identifiers_for,
    is_locked_out,
    record_failure,
    verify_password,
)
from app.cookies import clear_session_cookies, set_session_cookies
from app.db import get_session
from app.deps import CurrentUser, current_user
from app.page import page_context
from app.settings import settings
from app.templating import templates

router = APIRouter()

GENERIC_FAILURE = "Username or password is wrong."
LOCKED_MESSAGE = (
    "Too many failed attempts. Wait fifteen minutes, or ask the owner to run the unlock command."
)


@router.get("/login", response_class=HTMLResponse, include_in_schema=False)
def login_form(request: Request) -> HTMLResponse:
    # One token, used in the form and mirrored into the cookie: the POST compares
    # the two. There is no session row yet, so the cookie is all we can match on.
    token = request.cookies.get(CSRF_COOKIE) or token_urlsafe(32)
    response = templates.TemplateResponse(
        request,
        "login.html",
        # No signed-in user: page_context falls back to the site defaults, so the
        # anonymous page still gets a real theme instead of a hard-coded string.
        page_context(request, page_title="Colophon — Sign in", error=None, csrf_token=token),
    )
    if not request.cookies.get(CSRF_COOKIE):
        response.set_cookie(
            key=CSRF_COOKIE,
            value=token,
            max_age=60 * 60,
            httponly=True,
            secure=settings.cookie_secure,
            samesite="lax",
            path="/",
        )
    return response


@router.post("/login", response_class=HTMLResponse, include_in_schema=False)
async def login(
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    username: Annotated[str, Form(max_length=150)],
    password: Annotated[str, Form(max_length=200)],
    csrf_token: Annotated[str | None, Form()] = None,
) -> HTMLResponse:
    cookie = request.cookies.get(CSRF_COOKIE)
    client_ip = request.client.host if request.client else None

    if not csrf_token or not cookie or csrf_token != cookie:
        return _rejected_login(request, "The sign-in form expired. Try again.")

    identifiers = identifiers_for(ip=client_ip, username=username)
    if is_locked_out(session, identifiers):
        return _rejected_login(request, LOCKED_MESSAGE)

    user = find_user(session, username.strip())
    if user is None or not verify_password(password, user["password_hash"]):
        # Recorded against both keys, so neither IP spraying nor account spraying works.
        record_failure(session, identifiers)
        return _rejected_login(request, GENERIC_FAILURE)

    clear_failures(session, identifiers)
    session_id, session_csrf = create_session(
        session, int(user["id"]), ip=client_ip, user_agent=request.headers.get("user-agent")
    )
    redirect = RedirectResponse(url="/", status_code=303)
    set_session_cookies(redirect, session_id, session_csrf)
    return redirect


@router.post("/logout", include_in_schema=False)
def logout(
    request: Request,
    user: Annotated[CurrentUser, Depends(current_user)],
    session: Annotated[Session, Depends(get_session)],
    csrf_token: Annotated[str | None, Form()] = None,
) -> RedirectResponse:
    from app.auth import revoke_session
    from app.forms import verify_csrf

    verify_csrf(user, csrf_token, request.cookies.get(CSRF_COOKIE))
    if user.session_id:
        revoke_session(session, user.session_id)

    response = RedirectResponse(url="/login", status_code=303)
    clear_session_cookies(response)
    return response


def _rejected_login(request: Request, message: str) -> HTMLResponse:
    response = templates.TemplateResponse(
        request,
        "login.html",
        page_context(
            request,
            page_title="Colophon — Sign in",
            error=message,
            csrf_token=request.cookies.get(CSRF_COOKIE) or token_urlsafe(32),
        ),
        status_code=401,
    )
    return response
