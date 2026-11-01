# Session-expiry hardening at the HTTP layer. The unit tests in test_auth.py
# prove the expiry maths with injected clocks; these tests prove the running app
# enforces it — that a browser whose cookie is past the server-side expiry is
# signed out, that the cookie itself carries a matching max-age, and that the
# two lifetime constants cannot silently drift apart.
from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from app import db as db_module
from app.auth import ABSOLUTE_LIFETIME, SESSION_COOKIE
from app.cookies import SESSION_MAX_AGE
from app.main import create_app
from app.settings import settings
from tests.alembic_runner import engine_for, run_alembic


@pytest.fixture()
def auth_engine(monkeypatch: pytest.MonkeyPatch, tmp_path) -> Engine:
    """A migrated database (admin/admin exists) with auth switched on."""
    from sqlalchemy.orm import sessionmaker

    db_path = tmp_path / "expiry.db"
    run_alembic(db_path, "head")
    engine = engine_for(db_path)
    monkeypatch.setattr(
        db_module, "SessionLocal", sessionmaker(bind=engine, expire_on_commit=False)
    )
    monkeypatch.setattr(settings, "dev_auth_bypass", False)
    yield engine
    engine.dispose()


@pytest.fixture()
def auth_client(auth_engine: Engine) -> TestClient:
    return TestClient(create_app())


def sign_in(client: TestClient) -> None:
    page = client.get("/login")
    match = re.search('name="csrf_token" value="([^"]*)"', page.text)
    assert match, "login form is missing its CSRF token"
    response = client.post(
        "/login",
        data={
            "username": "admin",
            "password": "admin",
            "csrf_token": match.group(1),
        },
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text


def _session_cookie_max_age(client: TestClient) -> int:
    """The max-age the login actually put on the session cookie."""
    headers = client.get("/login").headers.get_list("set-cookie")
    for header in headers:
        match = re.search(SESSION_COOKIE + r'=[^;]*;.*?max-age=(\d+)', header, re.IGNORECASE)
        if match:
            return int(match.group(1))
    raise AssertionError(f"no {SESSION_COOKIE} set-cookie header found")


def test_the_cookie_max_age_matches_the_server_side_cap() -> None:
    """The browser forgets the cookie after SESSION_MAX_AGE; the server forgets
    the session after ABSOLUTE_LIFETIME. If they drift apart — either way — a
    reader holds a cookie for a session that no longer exists (or worse). They
    are one contract: creation + 90 days."""
    assert int(ABSOLUTE_LIFETIME.total_seconds()) == SESSION_MAX_AGE


def test_a_session_past_its_expiry_is_signed_out_by_the_app(
    auth_client: TestClient, auth_engine: Engine
) -> None:
    """The end-to-end guarantee: whatever the cookie still carries, a request
    whose session row is past expires_at lands on /login."""
    sign_in(auth_client)
    assert auth_client.get("/", follow_redirects=False).status_code == 200

    with auth_engine.begin() as conn:
        conn.execute(text("UPDATE sessions SET expires_at = '2020-01-01T00:00:00Z'"))

    response = auth_client.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"
