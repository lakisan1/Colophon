# The shared page context must be impossible to get wrong.
#
# Regression background: every router assembled its own template context, and the
# book edit page shipped rendering `data-theme="light"` for a user whose settings
# row says dark (the `_render_form` helper in app/routers/books.py hard-coded it).
# The common keys now come from one builder (app/page.py), and these tests walk
# the live route table — enumerated from the FastAPI app, never a hand-kept list —
# so a page added next month cannot drop the theme either.
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.orm import sessionmaker

from app import db as db_module
from app.deps import current_user
from app.main import create_app
from app.seed import seed
from app.seed_data import DEMO_PASSWORD, DEMO_USERNAME
from app.settings import settings
from tests.alembic_runner import engine_for, run_alembic

THEME_RE = re.compile(r'data-theme="([^"]*)"')
CSRF_RE = re.compile(r'name="csrf_token"[^>]*value="([^"]*)"')
PATH_PARAM_RE = re.compile(r"\{(\w+)(?::[^}]*)?\}")


# --------------------------------------------------------------------- fixtures


@pytest.fixture()
def signed_in_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[TestClient, Engine]:
    """Migrated + seeded app, real login (no bypass), demo user on the dark theme."""
    db_path = tmp_path / "page-context.db"
    run_alembic(db_path, "head")
    engine = engine_for(db_path)
    seed(engine)
    monkeypatch.setattr(
        db_module,
        "SessionLocal",
        sessionmaker(bind=engine, autoflush=False, expire_on_commit=False),
    )
    monkeypatch.setattr(settings, "dev_auth_bypass", False)

    client = TestClient(create_app())
    login_page = client.get("/login")
    token = CSRF_RE.search(login_page.text)
    assert token, "the login form must carry its CSRF token"
    login = client.post(
        "/login",
        data={"username": DEMO_USERNAME, "password": DEMO_PASSWORD, "csrf_token": token.group(1)},
        follow_redirects=False,
    )
    assert login.status_code == 303, "the signed-in fixture must actually be signed in"

    set_theme(engine, owner_id(engine), "dark")
    yield client, engine
    engine.dispose()


# --------------------------------------------------------------------- helpers


def owner_id(engine: Engine) -> int:
    with engine.connect() as conn:
        return int(
            conn.execute(
                text("SELECT id FROM users WHERE username = :u"), {"u": DEMO_USERNAME}
            ).scalar_one()
        )


def set_theme(engine: Engine, user_id: int, theme: str) -> None:
    """Write the user's own settings row — the exact row load_prefs resolves."""
    with engine.begin() as conn:
        updated = conn.execute(
            text("UPDATE settings SET theme = :t WHERE owner_id = :o"), {"t": theme, "o": user_id}
        )
        if updated.rowcount == 0:
            conn.execute(
                text("INSERT INTO settings (owner_id, theme) VALUES (:o, :t)"),
                {"o": user_id, "t": theme},
            )


def owned_book_id(engine: Engine) -> int:
    with engine.connect() as conn:
        return int(
            conn.execute(
                text(
                    "SELECT id FROM books WHERE owner_id = :o AND ownership_status = 'owned'"
                    " ORDER BY id LIMIT 1"
                ),
                {"o": owner_id(engine)},
            ).scalar_one()
        )


def iter_routes(app: FastAPI):
    """Every concrete route of the app, flattening FastAPI's own wrappers.

    FastAPI 0.141 keeps `include_router` results as opaque _IncludedRouter
    objects, and Mounts nest further, so `app.routes` alone is not the route
    table; descend through whatever holds a nested `.routes` instead.
    """
    stack = list(app.routes)
    while stack:
        node = stack.pop(0)
        inner = getattr(node, "original_router", None)
        if inner is not None:
            stack[:0] = list(inner.routes)
            continue
        nested = getattr(node, "routes", None)
        if nested and type(node).__name__ != "Route":
            stack[:0] = list(nested)
            continue
        yield node


def resolve_url(path: str, params: dict[str, str]) -> str:
    """A requestable URL for a route path; unknown parameters get a plain '1'."""
    return PATH_PARAM_RE.sub(lambda m: params.get(m.group(1), "1"), path)


def depends_on_current_user(route: Any) -> bool:
    """True when the route renders *as the signed-in user* (uses the gate).

    This is how the sweep tells a page route from an anonymous one (login)
    without hard-coding paths: the dependency is the fact, the path is noise.
    """
    dependant = getattr(route, "dependant", None)
    stack = [dependant] if dependant is not None else []
    while stack:
        dep = stack.pop()
        if dep.call is current_user:
            return True
        stack.extend(dep.dependencies)
    return False


# ----------------------------------------------------------------------- tests


def test_every_html_page_route_carries_the_signed_in_users_theme(
    signed_in_db: tuple[TestClient, Engine],
) -> None:
    """The anti-regression sweep: no page route may drop the theme.

    Routes come from the live app; non-HTML answers (JSON, CSV, photo bytes,
    static assets, misses) are skipped by content type rather than by name. A
    route that renders as the signed-in user must carry *their* theme; one that
    renders anonymously (login) must still carry a theme at all.
    """
    client, engine = signed_in_db
    params = {"book_id": str(owned_book_id(engine)), "slot": "front"}

    checked_pages = 0
    for route in iter_routes(client.app):
        methods = getattr(route, "methods", None) or set()
        if "GET" not in methods:
            continue
        response = client.get(resolve_url(route.path, params))
        content_type = response.headers.get("content-type", "")
        if response.status_code >= 400 or "text/html" not in content_type:
            continue  # not a page: JSON endpoint, export, photo bytes, static, miss

        theme = THEME_RE.search(response.text)
        assert theme is not None, f"{route.path} rendered HTML without a data-theme"
        if depends_on_current_user(route):
            checked_pages += 1
            assert theme.group(1) == "dark", (
                f"{route.path} ignored the signed-in user's dark theme setting"
            )

    # Guard the sweep itself: if the enumeration silently degenerates (wrapper
    # internals change, routes vanish), zero checks must not read as success.
    assert checked_pages >= 9, f"expected the known signed-in pages, only checked {checked_pages}"


def test_book_edit_page_is_dark_when_the_user_prefers_dark(
    signed_in_db: tuple[TestClient, Engine],
) -> None:
    """The reported bug, spelled out: GET /book/{id}/edit emitted data-theme="light"."""
    client, engine = signed_in_db
    book_id = owned_book_id(engine)

    response = client.get(f"/book/{book_id}/edit")

    assert response.status_code == 200
    assert 'data-theme="dark"' in response.text
    assert 'data-theme="light"' not in response.text


def test_theme_follows_the_settings_row_not_a_hard_coded_default(
    signed_in_db: tuple[TestClient, Engine],
) -> None:
    """Same session, same routes — only the row changes, and the pages flip with it."""
    client, engine = signed_in_db
    user_id = owner_id(engine)
    urls = ["/", "/stats", f"/book/{owned_book_id(engine)}/edit", f"/book/{owned_book_id(engine)}"]

    for url in urls:
        assert 'data-theme="dark"' in client.get(url).text, url

    set_theme(engine, user_id, "light")
    for url in urls:
        assert 'data-theme="light"' in client.get(url).text, url

    set_theme(engine, user_id, "dark")
    for url in urls:
        assert 'data-theme="dark"' in client.get(url).text, url


def test_saving_settings_dark_shows_dark_in_the_same_response(
    signed_in_db: tuple[TestClient, Engine],
) -> None:
    """The POST that saves dark must render dark immediately, not the pre-write read."""
    client, engine = signed_in_db
    set_theme(engine, owner_id(engine), "light")

    page = client.get("/settings")
    token = CSRF_RE.search(page.text)
    assert token

    response = client.post(
        "/settings",
        data={
            "csrf_token": token.group(1),
            "theme": "dark",
            "zoom_preset": "M",
            "currency_symbol": "\u20ac",
            "date_format": "dd.mm.yyyy",
            "time_format": "24h",
        },
    )

    assert response.status_code == 200
    assert 'data-theme="dark"' in response.text


def test_anonymous_login_page_still_renders_without_a_user(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No session, no user: page_context must degrade to the defaults, not crash."""
    db_path = tmp_path / "anonymous.db"
    run_alembic(db_path, "head")
    engine = engine_for(db_path)
    monkeypatch.setattr(
        db_module,
        "SessionLocal",
        sessionmaker(bind=engine, autoflush=False, expire_on_commit=False),
    )
    monkeypatch.setattr(settings, "dev_auth_bypass", False)
    client = TestClient(create_app())

    response = client.get("/login")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert THEME_RE.search(response.text), "the anonymous page must render a theme too"
    engine.dispose()
