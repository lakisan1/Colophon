# Placeholder pages so links and redirects have real targets before features land:
# the shelf page is M3, the login screen is M5. Both render paths are exercised
# by tests today so wiring regressions surface early.
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from app.deps import CurrentUser, current_user
from app.templating import templates

router = APIRouter()

LOGIN_STUB_HTML = (
    '<!doctype html><html lang="en"><body>'
    "<p>Colophon: login screen lands in milestone M5.</p>"
    "</body></html>"
)


@router.get("/")
def shelf_placeholder(
    request: Request,
    user: Annotated[CurrentUser, Depends(current_user)],
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "base.html",
        {
            "page_title": "Colophon",
            "placeholder": f"Shelf UI lands in milestone M3. Signed in as: {user.username}",
        },
    )


@router.get("/login", include_in_schema=False)
def login_stub() -> HTMLResponse:
    return HTMLResponse(LOGIN_STUB_HTML, status_code=501)
