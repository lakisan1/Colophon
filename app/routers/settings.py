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

from app.auth import drop_sessions_for_user, set_password, uses_default_credentials
from app.db import get_session
from app.deps import CurrentUser, current_user
from app.forms import read_form
from app.prefs import load_prefs
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
    return _render(request, session, user, load_prefs(session, user.id), notice=None, errors=[])


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
        return _render(
            request, session, user, load_prefs(session, user.id), notice=None, errors=errors
        )

    _upsert_prefs(session, user.id, currency, date_format, time_format, theme, zoom)
    return _render(request, session, user, load_prefs(session, user.id), notice="Saved.", errors=[])


@router.post("/settings/password", response_class=HTMLResponse)
async def change_password(
    request: Request,
    user: Annotated[CurrentUser, Depends(current_user)],
    session: Annotated[Session, Depends(get_session)],
    values: Annotated[dict, Depends(read_form)],
) -> HTMLResponse:
    from app.auth import find_user, verify_password

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
        return _render(
            request, session, user, load_prefs(session, user.id), notice=None, errors=errors
        )

    set_password(session, user.id, new)
    drop_sessions_for_user(session, user.id)

    response = RedirectResponse(url="/login", status_code=303)
    from app.cookies import clear_session_cookies

    clear_session_cookies(response)
    return response


def _upsert_prefs(
    session: Session,
    owner_id: int,
    currency: str,
    date_format: str,
    time_format: str,
    theme: str,
    zoom: str,
) -> None:
    updated = session.execute(
        text(
            """
            UPDATE settings
            SET currency_symbol = :currency, date_format = :date_format, time_format = :time_format,
                theme = :theme, zoom_preset = :zoom
            WHERE owner_id = :owner_id
            """
        ),
        {
            "currency": currency,
            "date_format": date_format,
            "time_format": time_format,
            "theme": theme,
            "zoom": zoom,
            "owner_id": owner_id,
        },
    )
    if updated.rowcount == 0:
        session.execute(
            text(
                """
                INSERT INTO settings
                    (owner_id, currency_symbol, date_format, time_format, theme, zoom_preset)
                VALUES (:owner_id, :currency, :date_format, :time_format, :theme, :zoom)
                """
            ),
            {
                "currency": currency,
                "date_format": date_format,
                "time_format": time_format,
                "theme": theme,
                "zoom": zoom,
                "owner_id": owner_id,
            },
        )
    session.commit()


def _render(
    request: Request,
    session: Session,
    user: CurrentUser,
    prefs,
    *,
    notice: str | None,
    errors: list[str],
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "user": user,
            "theme": prefs.theme,
            "page_title": "Colophon — Settings",
            "prefs": prefs,
            "notice": notice,
            "errors": errors,
            "zoom_presets": list(ZOOM_ROW_PX),
            "date_formats": DATE_FORMATS,
            "time_formats": TIME_FORMATS,
            "default_credentials": uses_default_credentials(session, user.id),
        },
    )
