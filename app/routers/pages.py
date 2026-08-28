# Non-page routes that have to exist before their milestone does. The shelf is
# no longer here (M3 gave it a router of its own); what is left is the /login
# target the LoginRequired redirect needs, so that path stays tested.
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()

LOGIN_STUB_HTML = (
    '<!doctype html><html lang="en"><body>'
    "<p>Colophon: login screen lands in milestone M5.</p>"
    "</body></html>"
)


@router.get("/login", include_in_schema=False)
def login_stub() -> HTMLResponse:
    return HTMLResponse(LOGIN_STUB_HTML, status_code=501)
