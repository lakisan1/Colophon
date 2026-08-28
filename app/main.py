# Application entry point. create_app() exists so tests can build isolated apps;
# the module-level `app` is what `uvicorn app.main:app` imports. Wiring is
# routers, static files, and the login-redirect handler.
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.deps import LoginRequired
from app.routers import healthz, pages, shelf
from app.templating import STATIC_DIR


def create_app() -> FastAPI:
    app = FastAPI(title="Colophon", docs_url=None, redoc_url=None)

    app.include_router(healthz.router)
    app.include_router(shelf.router)
    app.include_router(pages.router)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.exception_handler(LoginRequired)
    def login_required_handler(_request: Request, _exc: LoginRequired) -> RedirectResponse:
        # 303 so a browser re-issues a plain GET to /login after any method.
        return RedirectResponse(url="/login", status_code=303)

    return app


app = create_app()
