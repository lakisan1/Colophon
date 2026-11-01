# Application entry point. create_app() exists so tests can build isolated apps;
# the module-level `app` is what `uvicorn app.main:app` imports. Wiring is
# routers, static files, and the login-redirect handler.
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.deps import CsrfFailed, LoginRequired
from app.routers import auth, books, export, healthz, shelf
from app.routers import settings as settings_router
from app.settings import settings
from app.templating import STATIC_DIR


def create_app() -> FastAPI:
    # The route map is an implementation detail: docs/redoc/openapi stay off so
    # an anonymous probe cannot enumerate the API surface.
    app = FastAPI(title="Colophon", docs_url=None, redoc_url=None, openapi_url=None)

    app.include_router(healthz.router)
    app.include_router(auth.router)
    app.include_router(shelf.router)
    app.include_router(settings_router.router)
    app.include_router(books.router)
    app.include_router(export.router)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    # Headers every response carries, applied in one place so no route can
    # forget them. Static assets revalidate on every load so a CSS/JS change
    # shows up on a plain refresh (no hard-refresh) — without it the browser
    # heuristically caches stylesheets and keeps showing a stale UI after an
    # edit. nosniff (T7) stops a served image or download from being
    # reinterpreted as a different content type.
    #
    # The rest is the public-deployment set (see tests/hardening). Dynamic
    # responses default to private/no-store — pages are rendered per reader
    # (theme, prefs, their books), so a shared proxy caching them would leak
    # one reader's shelf to another; the photo route sets its own directive,
    # which setdefault leaves alone. The CSP allows no inline script: the two
    # former inline blocks live in static/js now, so script-src 'self' holds.
    # style-src keeps unsafe-inline only for the spine-rendering style
    # attributes (server-computed integers, no user text). HSTS is sent only
    # when cookies are marked Secure — on a plain-HTTP install it would do
    # nothing, and advertising it anyway pins the domain to HTTPS silently.
    CONTENT_SECURITY_POLICY = (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; object-src 'none'; base-uri 'none'; "
        "frame-ancestors 'none'; form-action 'self'"
    )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/static/"):
            response.headers["cache-control"] = "no-cache"
        else:
            response.headers.setdefault("cache-control", "private, no-store")
        response.headers["x-content-type-options"] = "nosniff"
        # The app frames nothing, not even its own pages, so a foreign page
        # cannot clickjack a signed-in reader's forms.
        response.headers["x-frame-options"] = "DENY"
        response.headers["content-security-policy"] = CONTENT_SECURITY_POLICY
        response.headers["referrer-policy"] = "strict-origin-when-cross-origin"
        response.headers["permissions-policy"] = "camera=(), microphone=(), geolocation=()"
        if settings.cookie_secure:
            response.headers["strict-transport-security"] = "max-age=31536000"
        return response

    @app.exception_handler(LoginRequired)
    def login_required_handler(_request: Request, _exc: LoginRequired) -> RedirectResponse:
        # 303 so a browser re-issues a plain GET to /login after any method.
        return RedirectResponse(url="/login", status_code=303)

    @app.exception_handler(CsrfFailed)
    def csrf_failed_handler(_request: Request, exc: CsrfFailed) -> HTMLResponse:
        # 403, not a redirect: a rejected mutation must not look like a success.
        return HTMLResponse(
            '<!doctype html><html lang="en"><body><h1>Rejected</h1>'
            f'<p>{exc}</p><p><a href="/">Back to the shelf</a></p></body></html>',
            status_code=403,
        )

    return app


app = create_app()
