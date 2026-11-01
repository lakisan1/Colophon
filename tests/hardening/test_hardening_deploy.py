# Deployment hardening for a public-facing install: the response headers and the
# shipped process flags that keep the app safe once anyone on the internet can
# reach it. These are contract tests — if a header or the proxy-header flag is
# ever dropped, the failure is loud here instead of silent in production.
#
# Header decisions (why, not just what) live in app/main.py's security_headers
# middleware; this file pins them from the outside.
from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from app import db as db_module
from app.auth import hash_password
from app.main import create_app
from app.settings import settings
from tests.alembic_runner import engine_for, run_alembic

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture()
def auth_engine(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Engine:
    """A migrated database (so admin/admin exists) with auth switched on."""
    from sqlalchemy.orm import sessionmaker

    db_path = tmp_path / "deploy.db"
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


def sign_in(client: TestClient, username: str = "admin", password: str = "admin") -> None:
    """GET the form to collect the double-submit token, then post it back."""
    page = client.get("/login")
    match = re.search(r'name="csrf_token" value="([^"]*)"', page.text)
    assert match, "login form is missing its CSRF token"
    response = client.post(
        "/login",
        data={"username": username, "password": password, "csrf_token": match.group(1)},
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text


# The policy the middleware must send. A page the CSP would break (an inline
# script, a remote origin) shows up as a broken page in a browser and as the
# no-inline-script test below in CI — keep both in sync on purpose.
_REQUIRED_CSP_PARTS = (
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self'",
    "img-src 'self'",
    "object-src 'none'",
    "base-uri 'none'",
    "frame-ancestors 'none'",
    "form-action 'self'",
)


def _assert_hardening_headers(response) -> None:
    csp = response.headers.get("content-security-policy", "")
    for part in _REQUIRED_CSP_PARTS:
        assert part in csp, f"CSP missing {part!r}: {csp}"
    assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert "camera=()" in response.headers["permissions-policy"]
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_the_login_page_carries_the_full_hardening_header_set(auth_client: TestClient) -> None:
    _assert_hardening_headers(auth_client.get("/login"))


def test_signed_in_pages_carry_the_full_hardening_header_set(auth_client: TestClient) -> None:
    sign_in(auth_client)
    _assert_hardening_headers(auth_client.get("/"))
    _assert_hardening_headers(auth_client.get("/settings"))
    _assert_hardening_headers(auth_client.get("/healthz"))


def test_hsts_is_sent_only_when_cookies_are_secure(
    auth_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Default install (plain HTTP LAN or dev): advertising HSTS would silently
    # pin the domain to HTTPS for visitors, so it must be absent.
    assert "strict-transport-security" not in auth_client.get("/login").headers

    monkeypatch.setattr(settings, "cookie_secure", True)
    hsts = auth_client.get("/login").headers["strict-transport-security"]
    assert "max-age=" in hsts


def test_dynamic_responses_are_private_and_not_stored(
    auth_client: TestClient,
) -> None:
    """Every rendered page is per-reader (theme, prefs, their books): a shared
    cache storing one must never serve it to another reader. The photo route
    sets its own directive and the static mount revalidates; everything else
    defaults to private, no-store."""
    sign_in(auth_client)
    for path in ("/", "/settings", "/healthz"):
        cache = auth_client.get(path).headers["cache-control"]
        assert "private" in cache and "no-store" in cache, path
    assert auth_client.get("/login").headers["cache-control"] == "private, no-store"


def test_static_assets_still_revalidate(auth_client: TestClient) -> None:
    response = auth_client.get("/static/css/theme.css")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache"


def test_no_inline_script_reaches_the_browser(auth_client: TestClient) -> None:
    """script-src 'self' holds only if every page loads its JS from files. Both
    former inline blocks (login pre-paint theme, settings provider confirm)
    were moved to static/js for exactly this."""
    sign_in(auth_client)
    inline = re.compile(r"<script(?![^>]*\bsrc=)[^>]*>", re.IGNORECASE)
    for path in ("/login", "/", "/settings"):
        page = auth_client.get(path).text
        leak = inline.search(page)
        assert leak is None, f"inline <script> on {path}: {leak.group(0) if leak else ''}"


def test_healthz_answers_status_and_nothing_else(auth_client: TestClient) -> None:
    """A public liveness probe must not fingerprint the install: no version,
    no database path, no user info."""
    response = auth_client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}




# ---------------------------------------------------------------- login page exposure


def test_the_login_page_hides_the_demo_hint_when_there_is_no_demo_account(
    auth_client: TestClient,
) -> None:
    """The login screen is the one page strangers see. A fresh install with no
    demo user must not advertise one (the hint used to be hard-coded)."""
    page = auth_client.get("/login")
    assert page.status_code == 200
    assert "demo" not in page.text.lower().replace("colophon", "")


def test_the_demo_hint_shows_only_while_the_demo_password_is_the_seed(
    seeded_db: tuple[TestClient, Engine],
) -> None:
    client, engine = seeded_db
    page = client.get("/login")
    assert "password" in page.text.lower() and "demo" in page.text.lower()

    # The moment the demo password stops being the seeded one, the public page
    # stops telling the world what it was.
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE users SET password_hash = :h WHERE username = 'demo'"),
            {"h": hash_password("changed-long-password")},
        )
    assert "demo" not in client.get("/login").text.lower().replace("colophon", "")


def test_shipped_start_commands_enable_proxy_headers() -> None:
    """The per-IP login lockout keys on request.client.host. Behind a reverse
    proxy without --proxy-headers every visitor shares the proxy's address, so
    five bad logins from one attacker lock out the whole site for fifteen
    minutes — repeatable forever. uvicorn must be started with the flag in
    every shipped entry point; FORWARDED_ALLOW_IPS narrows who may set the
    header (default 127.0.0.1 keeps direct installs honest)."""
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    cmd_line = next(line for line in dockerfile.splitlines() if line.startswith("CMD"))
    assert "--proxy-headers" in cmd_line, "Dockerfile CMD must start uvicorn with --proxy-headers"

    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    serve = next(
        line
        for line in makefile.splitlines()
        if line.strip().startswith("$(VENV)/bin/uvicorn") and "serve" not in line
    )
    assert "--proxy-headers" in serve, "make serve must start uvicorn with --proxy-headers"
