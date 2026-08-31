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
from app.templating import STATIC_DIR


def create_app() -> FastAPI:
    app = FastAPI(title="Colophon", docs_url=None, redoc_url=None)

    app.include_router(healthz.router)
    app.include_router(auth.router)
    app.include_router(shelf.router)
    app.include_router(settings_router.router)
    app.include_router(books.router)
    app.include_router(export.router)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    # Static assets revalidate on every load so a CSS/JS change shows up on a
    # plain refresh (no hard-refresh). Without this the browser heuristically
    # caches stylesheets and keeps showing a stale "old" UI after an edit.
    @app.middleware("http")
    async def no_cache_static(request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/static/"):
            response.headers["cache-control"] = "no-cache"
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
