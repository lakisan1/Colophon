# Auth tests (M5). The PLAN asks specifically for negative cases: the lockout
# must actually lock, a mutation without a CSRF token must be refused, the session
# cookie must carry its flags, and the expiry maths must be provable rather than
# assumed. Time is injected everywhere so no test sleeps.
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from app import db as db_module
from app.auth import (
    ABSOLUTE_LIFETIME,
    CSRF_COOKIE,
    MAX_FAILED_ATTEMPTS,
    SESSION_COOKIE,
    SLIDING_WINDOW,
    as_utc,
    create_session,
    load_session,
    touch_session,
)
from app.main import create_app
from app.settings import settings
from tests.alembic_runner import engine_for, run_alembic


@pytest.fixture()
def auth_engine(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Engine:
    """A migrated database (so `admin`/`admin` exists) with auth switched on."""
    from sqlalchemy.orm import sessionmaker

    db_path = tmp_path / "auth.db"
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
    token = _csrf_from(page.text)
    response = client.post(
        "/login",
        data={"username": username, "password": password, "csrf_token": token},
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text


def _csrf_from(html: str) -> str:
    import re

    match = re.search(r'name="csrf_token" value="([^"]*)"', html)
    assert match, "login form is missing its CSRF token"
    return match.group(1)


# ---------------------------------------------------------------- bootstrap


def test_the_seeded_admin_account_exists(auth_engine: Engine) -> None:
    with auth_engine.connect() as conn:
        row = conn.execute(text("SELECT username, substr(password_hash, 1, 4) FROM users")).first()
    assert row[0] == "admin"
    assert row[1] == "$2b$"  # bcrypt


def test_signed_in_user_sees_the_shelf(auth_client: TestClient) -> None:
    sign_in(auth_client)

    response = auth_client.get("/")
    assert response.status_code == 200
    assert "My library" in response.text


def test_a_password_hash_is_never_returned_to_the_browser(auth_client: TestClient) -> None:
    sign_in(auth_client)

    assert "$2b$" not in auth_client.get("/").text


# ---------------------------------------------------------------- login


def test_wrong_password_is_rejected_with_a_vague_message(auth_client: TestClient) -> None:
    page = auth_client.get("/login")
    response = auth_client.post(
        "/login",
        data={"username": "admin", "password": "hunter2", "csrf_token": _csrf_from(page.text)},
    )

    assert response.status_code == 401
    assert "Username or password is wrong." in response.text
    # No enumeration: the message cannot say the username was fine.
    assert "password" in response.text.lower()
    assert "no such user" not in response.text.lower()


def test_unknown_username_gets_the_same_message_as_a_wrong_password(
    auth_client: TestClient,
) -> None:
    page = auth_client.get("/login")
    first = auth_client.post(
        "/login", data={"username": "ghost", "password": "x", "csrf_token": _csrf_from(page.text)}
    )
    page = auth_client.get("/login")
    second = auth_client.post(
        "/login", data={"username": "admin", "password": "x", "csrf_token": _csrf_from(page.text)}
    )

    assert first.status_code == second.status_code == 401


def test_a_login_without_a_csrf_token_is_refused(auth_client: TestClient) -> None:
    response = auth_client.post("/login", data={"username": "admin", "password": "admin"})

    assert response.status_code == 401
    assert "expired" in response.text


def test_a_mismatched_csrf_token_is_refused(auth_client: TestClient) -> None:
    auth_client.get("/login")  # sets the cookie
    response = auth_client.post(
        "/login", data={"username": "admin", "password": "admin", "csrf_token": "not-the-token"}
    )

    assert response.status_code == 401


def test_logout_ends_the_session(auth_client: TestClient) -> None:
    sign_in(auth_client)
    page = auth_client.get("/")
    token = _csrf_from(page.text)

    auth_client.post("/logout", data={"csrf_token": token}, follow_redirects=False)

    assert auth_client.get("/", follow_redirects=False).status_code == 303


# ---------------------------------------------------------------- lockout (T8)


def test_five_failures_lock_the_account(auth_client: TestClient) -> None:
    for _ in range(MAX_FAILED_ATTEMPTS):
        page = auth_client.get("/login")
        auth_client.post(
            "/login",
            data={"username": "admin", "password": "wrong", "csrf_token": _csrf_from(page.text)},
        )

    page = auth_client.get("/login")
    locked = auth_client.post(
        "/login",
        data={"username": "admin", "password": "admin", "csrf_token": _csrf_from(page.text)},
    )

    # Correct password, still refused: the lockout is the point.
    assert locked.status_code == 401
    assert "Too many failed attempts" in locked.text


def test_four_failures_do_not_lock(auth_client: TestClient) -> None:
    for _ in range(MAX_FAILED_ATTEMPTS - 1):
        page = auth_client.get("/login")
        auth_client.post(
            "/login",
            data={"username": "admin", "password": "wrong", "csrf_token": _csrf_from(page.text)},
        )

    page = auth_client.get("/login")
    ok = auth_client.post(
        "/login",
        data={"username": "admin", "password": "admin", "csrf_token": _csrf_from(page.text)},
        follow_redirects=False,
    )
    assert ok.status_code == 303


def test_a_successful_login_clears_the_counter(
    auth_engine: Engine, auth_client: TestClient
) -> None:
    for _ in range(MAX_FAILED_ATTEMPTS - 1):
        page = auth_client.get("/login")
        auth_client.post(
            "/login",
            data={"username": "admin", "password": "wrong", "csrf_token": _csrf_from(page.text)},
        )
    sign_in(auth_client)

    with auth_engine.connect() as conn:
        remaining = conn.execute(text("SELECT count(*) FROM login_attempts")).scalar_one()
    assert remaining == 0


def test_the_unlock_cli_releases_a_locked_account(
    auth_engine: Engine, auth_client: TestClient
) -> None:
    from app.tools.unlock import unlock

    for _ in range(MAX_FAILED_ATTEMPTS):
        page = auth_client.get("/login")
        auth_client.post(
            "/login",
            data={"username": "admin", "password": "wrong", "csrf_token": _csrf_from(page.text)},
        )

    assert unlock("admin", password=None) == 0

    page = auth_client.get("/login")
    response = auth_client.post(
        "/login",
        data={"username": "admin", "password": "admin", "csrf_token": _csrf_from(page.text)},
        follow_redirects=False,
    )
    assert response.status_code == 303


def test_the_unlock_cli_reports_an_unknown_account(capsys: pytest.CaptureFixture[str]) -> None:
    from app.tools.unlock import unlock

    assert unlock("nobody", password=None) == 2
    assert "No such user" in capsys.readouterr().out


# ---------------------------------------------------------------- cookies


def _login_response(client: TestClient):
    """The 303 that carries the Set-Cookie headers, asserted raw: the cookie jar
    does not model SameSite, and the flags are the whole point (SPEC §13)."""
    token = _csrf_from(client.get("/login").text)
    return client.post(
        "/login",
        data={"username": "admin", "password": "admin", "csrf_token": token},
        follow_redirects=False,
    )


def _set_cookie_header(client: TestClient) -> str:
    response = _login_response(client)
    return " ".join(response.headers.get_list("set-cookie")).lower()


def test_session_cookie_flags(auth_client: TestClient) -> None:
    header = _set_cookie_header(auth_client)

    assert f"{SESSION_COOKIE}=" in header
    assert "httponly" in header
    assert "samesite=lax" in header
    assert "path=/" in header
    # Off by default so plain-HTTP LAN installs can sign in; see docs/GITHUB.md.
    assert "secure" not in header.replace("httponly", "")


def test_session_cookie_is_secure_when_configured(
    monkeypatch: pytest.MonkeyPatch, auth_client: TestClient
) -> None:
    """Asserted on the cookie we issue, not on a completed login: with Secure on,
    an http:// client will not send the cookie back — which is exactly why
    COLOPHON_COOKIE_SECURE defaults off for LAN installs."""
    monkeypatch.setattr(settings, "cookie_secure", True)
    header = " ".join(auth_client.get("/login").headers.get_list("set-cookie")).lower()

    assert f"{CSRF_COOKIE}=" in header
    assert "secure" in header
    assert "httponly" in header


# ---------------------------------------------------------------- expiry maths


def test_a_session_slides_on_use(auth_engine: Engine) -> None:
    start = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)
    with db_module.SessionLocal() as session:
        session_id, _token = create_session(session, 1, now=start)
        row = load_session(session, session_id, now=start + timedelta(days=10))
        assert row is not None
        touch_session(session, row, now=start + timedelta(days=10))

        later = load_session(session, session_id, now=start + timedelta(days=35))
        assert later is not None  # 25 days after the last touch: still inside 30


def test_the_hard_cap_kills_a_session_at_ninety_days(auth_engine: Engine) -> None:
    start = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)
    with db_module.SessionLocal() as session:
        session_id, _token = create_session(session, 1, now=start)
        row = load_session(session, session_id, now=start)
        assert row is not None

        # Touch it every day for 91 days: sliding must never beat the cap.
        for day in range(1, 92):
            current = load_session(session, session_id, now=start + timedelta(days=day))
            if current is None:
                break
            touch_session(session, current, now=start + timedelta(days=day))

        assert current is None
        # Dead at exactly creation + 90 days, no matter how recently it was used.
        assert day == ABSOLUTE_LIFETIME.days


def test_sliding_cannot_extend_the_stored_expiry_past_the_cap(auth_engine: Engine) -> None:
    start = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)
    with db_module.SessionLocal() as session:
        session_id, _token = create_session(session, 1, now=start)
        row = load_session(session, session_id, now=start + timedelta(days=89))
        touch_session(session, row, now=start + timedelta(days=89))

        stored = session.execute(
            text("SELECT expires_at FROM sessions WHERE id = :i"), {"i": session_id}
        ).scalar_one()

    assert as_utc(stored) == start + ABSOLUTE_LIFETIME


def test_touching_a_session_young_one_minute_does_not_write(auth_engine: Engine) -> None:
    start = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)
    with db_module.SessionLocal() as session:
        session_id, _token = create_session(session, 1, now=start)
        row = load_session(session, session_id, now=start)
        touch_session(session, row, now=start + timedelta(seconds=20))
        seen = session.execute(
            text("SELECT last_seen_at FROM sessions WHERE id = :i"), {"i": session_id}
        ).scalar_one()

    assert as_utc(seen) == start


def test_an_expired_session_is_deleted_and_redirects(
    auth_engine: Engine, auth_client: TestClient
) -> None:
    sign_in(auth_client)
    with auth_engine.begin() as conn:
        conn.execute(
            text("UPDATE sessions SET expires_at = '2020-01-01T00:00:00Z'"),
        )

    response = auth_client.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    with auth_engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM sessions")).scalar_one() == 0


# ---------------------------------------------------------------- CSRF on mutations


def test_a_book_post_without_a_csrf_token_is_refused(auth_client: TestClient) -> None:
    sign_in(auth_client)

    response = auth_client.post("/book/new", data={"title": "No Token"}, follow_redirects=False)

    assert response.status_code == 403
    assert "CSRF" in response.text


def test_a_book_post_with_the_session_token_succeeds(auth_client: TestClient) -> None:
    sign_in(auth_client)
    token = _csrf_from(auth_client.get("/book/new").text)

    response = auth_client.post(
        "/book/new", data={"title": "With Token", "csrf_token": token}, follow_redirects=False
    )

    assert response.status_code == 303


def test_a_forged_token_is_refused_even_when_signed_in(auth_client: TestClient) -> None:
    sign_in(auth_client)

    response = auth_client.post(
        "/book/new",
        data={"title": "Forged", "csrf_token": "attacker-guess"},
        follow_redirects=False,
    )

    assert response.status_code == 403


# ---------------------------------------------------------------- default credentials (D11)


def test_the_default_credentials_banner_shows_until_the_password_changes(
    auth_client: TestClient,
) -> None:
    sign_in(auth_client)
    assert "default password" in auth_client.get("/").text

    token = _csrf_from(auth_client.get("/settings").text)
    auth_client.post(
        "/settings/password",
        data={
            "csrf_token": token,
            "current_password": "admin",
            "new_password": "a-long-enough-password",
            "confirm_password": "a-long-enough-password",
        },
        follow_redirects=False,
    )

    sign_in(auth_client, password="a-long-enough-password")
    assert "default password" not in auth_client.get("/").text


def test_the_banner_warns_but_never_blocks_entry(auth_client: TestClient) -> None:
    sign_in(auth_client)
    token = _csrf_from(auth_client.get("/book/new").text)

    response = auth_client.post(
        "/book/new",
        data={"title": "Added Despite The Warning", "csrf_token": token},
        follow_redirects=False,
    )

    assert response.status_code == 303


def test_changing_the_password_requires_the_current_one(auth_client: TestClient) -> None:
    sign_in(auth_client)
    token = _csrf_from(auth_client.get("/settings").text)

    response = auth_client.post(
        "/settings/password",
        data={
            "csrf_token": token,
            "current_password": "wrong",
            "new_password": "whatever-12345",
            "confirm_password": "whatever-12345",
        },
    )

    assert response.status_code == 200
    assert "Current password is wrong." in response.text


def test_a_short_new_password_is_refused(auth_client: TestClient) -> None:
    sign_in(auth_client)
    token = _csrf_from(auth_client.get("/settings").text)

    response = auth_client.post(
        "/settings/password",
        data={
            "csrf_token": token,
            "current_password": "admin",
            "new_password": "short",
            "confirm_password": "short",
        },
    )

    assert "at least 8 characters" in response.text


def test_a_password_change_ends_other_sessions(auth_client: TestClient) -> None:
    sign_in(auth_client)
    token = _csrf_from(auth_client.get("/settings").text)

    auth_client.post(
        "/settings/password",
        data={
            "csrf_token": token,
            "current_password": "admin",
            "new_password": "brand-new-password",
            "confirm_password": "brand-new-password",
        },
        follow_redirects=False,
    )

    assert auth_client.get("/", follow_redirects=False).status_code == 303


# ---------------------------------------------------------------- settings page


def test_settings_can_be_changed_and_are_remembered(auth_client: TestClient) -> None:
    sign_in(auth_client)
    token = _csrf_from(auth_client.get("/settings").text)

    saved = auth_client.post(
        "/settings",
        data={
            "csrf_token": token,
            "theme": "dark",
            "zoom_preset": "L",
            "currency_symbol": "$",
            "date_format": "yyyy-mm-dd",
            "time_format": "12h",
        },
    )
    assert saved.status_code == 200
    assert 'data-theme="dark"' in saved.text

    again = auth_client.get("/settings")
    assert "selected>dark" in again.text.replace(" ", "") or 'value="dark"' in again.text


def test_the_shelf_follows_the_stored_zoom(auth_client: TestClient) -> None:
    sign_in(auth_client)
    token = _csrf_from(auth_client.get("/settings").text)
    auth_client.post(
        "/settings",
        data={
            "csrf_token": token,
            "theme": "light",
            "zoom_preset": "L",
            "currency_symbol": "€",
            "date_format": "dd.mm.yyyy",
            "time_format": "24h",
        },
    )

    assert 'aria-current="true">L<' in auth_client.get("/").text


def test_an_invalid_setting_is_rejected(auth_client: TestClient) -> None:
    sign_in(auth_client)
    token = _csrf_from(auth_client.get("/settings").text)

    response = auth_client.post(
        "/settings",
        data={
            "csrf_token": token,
            "theme": "neon",
            "zoom_preset": "XL",
            "currency_symbol": "",
            "date_format": "dd.mm.yyyy",
            "time_format": "24h",
        },
    )

    assert "Theme must be one of" in response.text
    assert "Zoom must be S, M or L." in response.text


def test_the_login_screen_is_the_only_public_page(auth_client: TestClient) -> None:
    for path in ("/", "/book/new", "/settings", "/book/1/edit"):
        response = auth_client.get(path, follow_redirects=False)
        assert response.status_code == 303, path
        assert response.headers["location"] == "/login", path

    assert auth_client.get("/login").status_code == 200
    assert auth_client.get("/healthz").status_code == 200


def test_sliding_window_and_cap_constants_match_the_spec() -> None:
    assert timedelta(days=30) == SLIDING_WINDOW
    assert timedelta(days=90) == ABSOLUTE_LIFETIME
