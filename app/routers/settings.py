# Settings (M5): display preferences and the password.
#
# Preferences live in the per-user `settings` row; the global row keeps the site
# defaults for anyone without one (M1's resolution order). Password changes drop
# every session for that user, because a stolen-cookie scenario is exactly the
# reason someone changes a password.
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.auth import (
    create_user,
    delete_user,
    drop_sessions_for_user,
    find_user,
    set_password,
    uses_default_credentials,
    verify_password,
)
from app.db import get_session
from app.deps import CurrentUser, current_user
from app.forms import read_form
from app.page import load_prefs_into_state, page_context
from app.settings import settings
from app.shelf import ZOOM_ROW_PX
from app.templating import templates

router = APIRouter()

DATE_FORMATS = ("dd.mm.yyyy", "mm/dd/yyyy", "yyyy-mm-dd")
TIME_FORMATS = ("24h", "12h")
THEMES = ("light", "dark")
MIN_PASSWORD_LENGTH = 8


@router.get("/settings", response_class=HTMLResponse)
def settings_page(
    request: Request,
    user: Annotated[CurrentUser, Depends(current_user)],
    session: Annotated[Session, Depends(get_session)],
) -> HTMLResponse:
    notice = None
    if request.query_params.get("user_created") == "1":
        notice = "User created."
    elif request.query_params.get("user_deleted") == "1":
        notice = "User deleted."
    users = _users_for_admin(session) if user.is_admin else []
    return _render(request, session, user, notice=notice, errors=[], users=users)


@router.post("/settings", response_class=HTMLResponse)
async def update_settings(
    request: Request,
    user: Annotated[CurrentUser, Depends(current_user)],
    session: Annotated[Session, Depends(get_session)],
    values: Annotated[dict, Depends(read_form)],
) -> HTMLResponse:
    errors = []
    theme = (values.get("theme") or "").strip()
    zoom = (values.get("zoom_preset") or "").strip().upper()
    date_format = (values.get("date_format") or "").strip()
    time_format = (values.get("time_format") or "").strip()
    currency = (values.get("currency_symbol") or "").strip()
    google_books = values.get("google_books") == "1"

    if theme not in THEMES:
        errors.append(f"Theme must be one of: {', '.join(THEMES)}.")
    if zoom not in ZOOM_ROW_PX:
        errors.append("Zoom must be S, M or L.")
    if date_format not in DATE_FORMATS:
        errors.append(f"Date format must be one of: {', '.join(DATE_FORMATS)}.")
    if time_format not in TIME_FORMATS:
        errors.append(f"Time format must be one of: {', '.join(TIME_FORMATS)}.")
    if not currency or len(currency) > 4:
        errors.append("Currency symbol must be one to four characters.")

    if errors:
        return _render(request, session, user, notice=None, errors=errors)

    _upsert_prefs(session, user.id, currency, date_format, time_format, theme, zoom, google_books)
    # The page must show what was just saved: the request cached its prefs before
    # this write (in current_user), so re-read them before rendering.
    load_prefs_into_state(request, session, user.id)
    return _render(request, session, user, notice="Saved.", errors=[])


@router.post("/settings/password", response_class=HTMLResponse)
async def change_password(
    request: Request,
    user: Annotated[CurrentUser, Depends(current_user)],
    session: Annotated[Session, Depends(get_session)],
    values: Annotated[dict, Depends(read_form)],
) -> HTMLResponse:
    current = values.get("current_password") or ""
    new = values.get("new_password") or ""
    confirm = values.get("confirm_password") or ""

    account = find_user(session, user.username)
    errors = []
    if account is None or not verify_password(current, account["password_hash"]):
        errors.append("Current password is wrong.")
    if len(new) < MIN_PASSWORD_LENGTH:
        errors.append(f"New password must be at least {MIN_PASSWORD_LENGTH} characters.")
    if new != confirm:
        errors.append("New password and confirmation do not match.")

    if errors:
        return _render(request, session, user, notice=None, errors=errors)

    set_password(session, user.id, new)
    drop_sessions_for_user(session, user.id)

    response = RedirectResponse(url="/login", status_code=303)
    from app.cookies import clear_session_cookies

    clear_session_cookies(response)
    return response


@router.post("/settings/users/new", response_class=HTMLResponse)
async def create_user_route(
    request: Request,
    user: Annotated[CurrentUser, Depends(current_user)],
    session: Annotated[Session, Depends(get_session)],
    values: Annotated[dict, Depends(read_form)],
) -> HTMLResponse:
    if not user.is_admin:
        return RedirectResponse(url="/settings", status_code=303)
    username = (values.get("username") or "").strip()[:150]
    password = values.get("password") or ""
    confirm = values.get("confirm_password") or ""
    errors = []
    if not username:
        errors.append("A username is required.")
    elif find_user(session, username) is not None:
        errors.append("That username already exists.")
    if len(password) < MIN_PASSWORD_LENGTH:
        errors.append(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
    if password != confirm:
        errors.append("Password and confirmation do not match.")
    if errors:
        return _render(
            request,
            session,
            user,
            notice=None,
            errors=errors,
            users=_users_for_admin(session),
            admin_username=username,
        )
    create_user(session, username, password)
    return RedirectResponse(url="/settings?user_created=1", status_code=303)


@router.post("/settings/users/{user_id}/delete", response_class=HTMLResponse)
async def delete_user_route(
    request: Request,
    user: Annotated[CurrentUser, Depends(current_user)],
    session: Annotated[Session, Depends(get_session)],
    user_id: int,
    values: Annotated[dict, Depends(read_form)],
) -> HTMLResponse:
    if not user.is_admin:
        return RedirectResponse(url="/settings", status_code=303)
    errors = []
    target = session.execute(
        text("SELECT id, username, is_admin FROM users WHERE id = :id"),
        {"id": user_id},
    ).first()
    if target is None:
        errors.append("No such user.")
    elif bool(target[2]):
        errors.append("The admin account cannot be deleted.")
    elif int(target[0]) == user.id:
        errors.append("You cannot delete the account you are signed in as.")
    else:
        account = find_user(session, user.username)
        if account is None or not verify_password(
            values.get("current_password") or "", account["password_hash"]
        ):
            errors.append("Your password is wrong (needed to confirm deletion).")
        if values.get("confirm_delete") != "1":
            errors.append(
                "Tick the box to confirm you understand this permanently deletes "
                "the user and all their data."
            )
    if errors:
        return _render(
            request,
            session,
            user,
            notice=None,
            errors=errors,
            users=_users_for_admin(session),
        )
    delete_user(session, int(target[0]), str(target[1]))
    return RedirectResponse(url="/settings?user_deleted=1", status_code=303)


def _users_for_admin(session: Session) -> list[dict]:
    """All accounts, admin first, for the admin user-management section."""
    rows = session.execute(
        text("SELECT id, username, is_admin FROM users ORDER BY is_admin DESC, username")
    ).all()
    return [dict(row._mapping) for row in rows]


def _upsert_prefs(
    session: Session,
    owner_id: int,
    currency: str,
    date_format: str,
    time_format: str,
    theme: str,
    zoom: str,
    google_books: bool,
) -> None:
    flag = 1 if google_books else 0
    updated = session.execute(
        text(
            """
            UPDATE settings
            SET currency_symbol = :currency, date_format = :date_format, time_format = :time_format,
                theme = :theme, zoom_preset = :zoom, google_books = :google_books
            WHERE owner_id = :owner_id
            """
        ),
        {
            "currency": currency,
            "date_format": date_format,
            "time_format": time_format,
            "theme": theme,
            "zoom": zoom,
            "google_books": flag,
            "owner_id": owner_id,
        },
    )
    if updated.rowcount == 0:
        session.execute(
            text(
                """
                INSERT INTO settings
                    (owner_id, currency_symbol, date_format, time_format, theme, zoom_preset,
                     google_books)
                VALUES (:owner_id, :currency, :date_format, :time_format, :theme, :zoom,
                        :google_books)
                """
            ),
            {
                "currency": currency,
                "date_format": date_format,
                "time_format": time_format,
                "theme": theme,
                "zoom": zoom,
                "google_books": flag,
                "owner_id": owner_id,
            },
        )
    session.commit()


def _render(
    request: Request,
    session: Session,
    user: CurrentUser,
    *,
    notice: str | None,
    errors: list[str],
    users: list[dict] | None = None,
    admin_username: str | None = None,
) -> HTMLResponse:
    # The admin section needs the full user list; load it when the caller did not.
    # A non-admin never sees the section, so it gets an empty list.
    if user.is_admin and users is None:
        users = _users_for_admin(session)
    users = users if user.is_admin else []
    # `prefs` itself is one of the shared keys now (app/page.py), which is what
    # the form below binds against; the router does not hand-assemble the theme.
    return templates.TemplateResponse(
        request,
        "settings.html",
        page_context(
            request,
            user,
            page_title="Colophon — Settings",
            notice=notice,
            errors=errors,
            zoom_presets=list(ZOOM_ROW_PX),
            date_formats=DATE_FORMATS,
            time_formats=TIME_FORMATS,
            default_credentials=uses_default_credentials(session, user.id),
            google_books_key_configured=bool(settings.google_books_key),
            users=users,
            admin_username=admin_username,
        ),
    )
