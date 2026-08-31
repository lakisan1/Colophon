# Auth tests (M5). The PLAN asks specifically for negative cases: the lockout
# must actually lock, a mutation without a CSRF token must be refused, the session
# cookie must carry its flags, and the expiry maths must be provable rather than
# assumed. Time is injected everywhere so no test sleeps.
from __future__ import annotations

import json
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
from app.lookup import PROVIDERS
from app.main import create_app
from app.prefs import Prefs
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
    # The Sign out button lives in the settings panel, so its CSRF token comes
    # from the settings page rather than the shelf header.
    page = auth_client.get("/settings")
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


def test_settings_page_has_no_back_to_shelf_button(auth_client: TestClient) -> None:
    sign_in(auth_client)
    page = auth_client.get("/settings")
    assert page.status_code == 200
    assert "Back to the shelf" not in page.text


def test_settings_page_includes_forms_css(auth_client: TestClient) -> None:
    sign_in(auth_client)
    page = auth_client.get("/settings")
    assert page.status_code == 200
    assert 'href="/static/css/forms.css"' in page.text


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


def _stored_search_providers(engine: Engine) -> str:
    """The admin's stored provider string; no settings row yet reads as nothing on."""
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT search_providers FROM settings "
                "WHERE owner_id = (SELECT id FROM users WHERE username = 'admin')"
            )
        ).first()
    return "" if row is None else str(row[0])


_SETTINGS_FIELDS = {
    "theme": "light",
    "zoom_preset": "M",
    "currency_symbol": "\u20ac",
    "date_format": "dd.mm.yyyy",
    "time_format": "24h",
}


def test_settings_offers_one_checkbox_per_search_provider(auth_client: TestClient) -> None:
    """The card is the registry rendered: a new provider needs no template edit."""
    sign_in(auth_client)
    page = auth_client.get("/settings")

    assert "<legend>Search providers</legend>" in page.text
    for spec in PROVIDERS:
        assert f'name="provider_{spec.id}"' in page.text
    # The single per-user flag is gone rather than merely hidden.
    assert 'name="google_books"' not in page.text


def test_both_search_providers_are_stored_together(
    auth_client: TestClient, auth_engine: Engine
) -> None:
    sign_in(auth_client)
    token = _csrf_from(auth_client.get("/settings").text)

    saved = auth_client.post(
        "/settings",
        data={
            "csrf_token": token,
            **_SETTINGS_FIELDS,
            "provider_openlibrary": "1",
            "provider_googlebooks": "1",
            "provider_key_googlebooks": "AIza-user-key",
        },
    )

    assert saved.status_code == 200
    assert _stored_search_providers(auth_engine) == "openlibrary,googlebooks"
    page = auth_client.get("/settings")
    assert 'name="provider_openlibrary" value="1" checked' in page.text
    assert 'name="provider_googlebooks" value="1" checked' in page.text


def test_enabling_a_keyed_provider_without_a_key_is_refused(
    auth_client: TestClient, auth_engine: Engine
) -> None:
    """A keyed provider is per-user only, so the checkbox alone is not a save."""
    sign_in(auth_client)
    token = _csrf_from(auth_client.get("/settings").text)

    refused = auth_client.post(
        "/settings",
        data={"csrf_token": token, **_SETTINGS_FIELDS, "provider_googlebooks": "1"},
    )

    assert refused.status_code == 200
    assert "Google Books is enabled, but you must enter an API key to use it." in refused.text
    # Nothing was written: the provider stays off and the box renders unchecked.
    assert "googlebooks" not in _stored_search_providers(auth_engine)
    assert 'name="provider_googlebooks" value="1" checked' not in auth_client.get("/settings").text

    # The rule is the registry's, so the next keyed provider is refused by name too.
    also = auth_client.post(
        "/settings",
        data={"csrf_token": token, **_SETTINGS_FIELDS, "provider_isbndb": "1"},
    )
    assert "ISBNdb is enabled, but you must enter an API key to use it." in also.text
    assert "isbndb" not in _stored_search_providers(auth_engine)


def _stored_provider_keys(engine: Engine) -> str:
    """The stored key JSON; a missing row or column reads as an empty object."""
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT provider_keys FROM settings "
                "WHERE owner_id = (SELECT id FROM users WHERE username = 'admin')"
            )
        ).first()
    return "{}" if row is None or row[0] is None else str(row[0])


def test_settings_offers_a_key_field_per_keyed_provider(auth_client: TestClient) -> None:
    """The registry decides who gets a key input; the template knows no provider id."""
    sign_in(auth_client)
    page = auth_client.get("/settings")

    assert "Google Books API key" in page.text
    assert 'name="provider_key_googlebooks"' in page.text
    # ISBNdb is keyed as well, so its checkbox and its key input came from the
    # registry with no template edit.
    assert "ISBNdb API key" in page.text
    assert 'name="provider_key_isbndb"' in page.text
    # Open Library needs no key, so it gets no field asking for one.
    assert 'name="provider_key_openlibrary"' not in page.text


def test_a_pasted_api_key_is_stored_and_prefilled(
    auth_client: TestClient, auth_engine: Engine
) -> None:
    """The key is per-user, on the owner's own settings row, and comes back."""
    sign_in(auth_client)
    token = _csrf_from(auth_client.get("/settings").text)

    saved = auth_client.post(
        "/settings",
        data={
            "csrf_token": token,
            **_SETTINGS_FIELDS,
            "provider_googlebooks": "1",
            "provider_key_googlebooks": "  AIza-user-key  ",
        },
    )

    assert saved.status_code == 200
    assert json.loads(_stored_provider_keys(auth_engine)) == {"googlebooks": "AIza-user-key"}

    again = auth_client.get("/settings")
    assert 'name="provider_key_googlebooks" value="AIza-user-key"' in again.text
    # It is a password field: the value is not readable as plain text in the label.
    assert 'type="password" name="provider_key_googlebooks"' in again.text


def test_a_blank_api_key_is_never_stored(auth_client: TestClient, auth_engine: Engine) -> None:
    """Blank means "no key at all", never a key whose value is whitespace."""
    sign_in(auth_client)
    token = _csrf_from(auth_client.get("/settings").text)

    # Enabled with nothing in the field: refused, so nothing reaches the column.
    auth_client.post(
        "/settings",
        data={"csrf_token": token, **_SETTINGS_FIELDS, "provider_googlebooks": "1"},
    )
    assert json.loads(_stored_provider_keys(auth_engine)) == {}

    auth_client.post(
        "/settings",
        data={
            "csrf_token": token,
            **_SETTINGS_FIELDS,
            "provider_googlebooks": "1",
            "provider_key_googlebooks": "AIza-user-key",
        },
    )
    assert json.loads(_stored_provider_keys(auth_engine)) == {"googlebooks": "AIza-user-key"}

    # Wiping the field while the box stays ticked is the same unfinished form: it is
    # refused, and the whole save (key included) is left alone.
    refused = auth_client.post(
        "/settings",
        data={
            "csrf_token": token,
            **_SETTINGS_FIELDS,
            "provider_googlebooks": "1",
            "provider_key_googlebooks": "   ",
        },
    )
    assert "must enter an API key" in refused.text
    assert json.loads(_stored_provider_keys(auth_engine)) == {"googlebooks": "AIza-user-key"}

    # Un-ticking the provider is how a key is retired: a provider nobody enabled
    # saves no key with it.
    auth_client.post(
        "/settings",
        data={"csrf_token": token, **_SETTINGS_FIELDS, "provider_openlibrary": "1"},
    )
    assert json.loads(_stored_provider_keys(auth_engine)) == {}


def test_a_personal_key_is_what_makes_the_provider_save(
    auth_client: TestClient, auth_engine: Engine
) -> None:
    """The same form the refusal above rejected goes through once a key comes with it."""
    sign_in(auth_client)
    token = _csrf_from(auth_client.get("/settings").text)

    saved = auth_client.post(
        "/settings",
        data={
            "csrf_token": token,
            **_SETTINGS_FIELDS,
            "provider_googlebooks": "1",
            "provider_key_googlebooks": "AIza-user-key",
        },
    )

    assert saved.status_code == 200
    assert "must enter an API key" not in saved.text
    assert "Saved." in saved.text
    assert _stored_search_providers(auth_engine) == "googlebooks"
    assert json.loads(_stored_provider_keys(auth_engine)) == {"googlebooks": "AIza-user-key"}


def test_every_search_provider_can_be_switched_off(
    auth_client: TestClient, auth_engine: Engine
) -> None:
    """No box ticked is a preference now: the row stores nobody enabled.

    The ask that used to be the server's floor ("Open Library stays on") is a
    client-side confirm on the form, so a POST with nothing ticked — exactly what
    a confirming browser sends — saves an empty provider list.
    """
    sign_in(auth_client)
    token = _csrf_from(auth_client.get("/settings").text)

    opted_in = auth_client.post(
        "/settings",
        data={
            "csrf_token": token,
            **_SETTINGS_FIELDS,
            "provider_googlebooks": "1",
            "provider_key_googlebooks": "AIza-user-key",
        },
    )
    assert opted_in.status_code == 200
    assert _stored_search_providers(auth_engine) == "googlebooks"

    saved = auth_client.post("/settings", data={"csrf_token": token, **_SETTINGS_FIELDS})

    assert saved.status_code == 200
    assert "Saved." in saved.text
    assert _stored_search_providers(auth_engine) == ""
    page = auth_client.get("/settings")
    for spec in PROVIDERS:
        assert f'name="provider_{spec.id}" value="1" checked' not in page.text


def test_all_off_still_refuses_a_keyed_provider_it_cannot_answer(
    auth_client: TestClient, auth_engine: Engine
) -> None:
    """Switching everything off is allowed; a keyed provider with no key is not."""
    sign_in(auth_client)
    token = _csrf_from(auth_client.get("/settings").text)

    refused = auth_client.post(
        "/settings",
        data={
            "csrf_token": token,
            **_SETTINGS_FIELDS,
            "provider_isbndb": "1",
            "provider_key_isbndb": "",
        },
    )

    assert refused.status_code == 200
    assert "ISBNdb is enabled, but you must enter an API key to use it." in refused.text
    # All-off is allowed on the same form; a keyed box with no key still is not, and
    # the refused write leaves the row exactly as the next (valid) save left it.
    auth_client.post("/settings", data={"csrf_token": token, **_SETTINGS_FIELDS})
    assert _stored_search_providers(auth_engine) == ""


def test_an_empty_provider_list_disables_the_new_book_lookup(
    auth_client: TestClient, auth_engine: Engine
) -> None:
    """The Add-book page says the lookup is off rather than returning no matches."""
    sign_in(auth_client)
    token = _csrf_from(auth_client.get("/settings").text)

    auth_client.post("/settings", data={"csrf_token": token, **_SETTINGS_FIELDS})

    assert _stored_search_providers(auth_engine) == ""
    page = auth_client.get("/book/new")

    assert page.status_code == 200
    assert "Search is disabled" in page.text
    assert 'href="/settings">Settings</a>' in page.text
    assert '<button type="submit" class="btn" disabled>Search</button>' in page.text
    query = _lookup_input(page.text)
    assert "disabled" in query
    assert 'placeholder="ISBN or title"' in query


def test_a_provider_on_keeps_the_new_book_lookup_live(auth_client: TestClient) -> None:
    """The other half: with a provider enabled nothing on the card is disabled."""
    sign_in(auth_client)

    page = auth_client.get("/book/new")

    assert page.status_code == 200
    assert "Search is disabled" not in page.text
    assert '<button type="submit" class="btn">Search</button>' in page.text
    query = _lookup_input(page.text)
    assert "disabled" not in query
    assert 'placeholder="ISBN or title"' in query


def _lookup_input(html: str) -> str:
    """The lookup field's own tag, so a test can ask what it carries."""
    import re

    match = re.search('<input[^>]*name="q"[^>]*>', html)
    assert match is not None, "the Add-book page must carry the lookup input"
    return match.group(0)


def _prefs_for(engine: Engine, owner_id: int) -> Prefs:
    """Effective prefs straight from the resolution order, no HTTP in the way."""
    from sqlalchemy.orm import sessionmaker

    from app.prefs import load_prefs

    with sessionmaker(bind=engine, expire_on_commit=False)() as session:
        return load_prefs(session, owner_id)


def _admin_id(engine: Engine) -> int:
    with engine.connect() as conn:
        return int(conn.execute(text("SELECT id FROM users WHERE username = 'admin'")).scalar_one())


def _set_stored_providers(engine: Engine, owner_id: int, value: str) -> None:
    """Write the reader's own provider column — "" is a writable, meaningful value."""
    with engine.begin() as conn:
        updated = conn.execute(
            text("UPDATE settings SET search_providers = :p WHERE owner_id = :o"),
            {"p": value, "o": owner_id},
        )
        if updated.rowcount == 0:
            conn.execute(
                text("INSERT INTO settings (owner_id, search_providers) VALUES (:o, :p)"),
                {"o": owner_id, "p": value},
            )


def test_load_prefs_reads_an_empty_provider_list_as_nothing_enabled(
    auth_engine: Engine,
) -> None:
    """An empty provider column is the reader's own decision, not a missing value."""
    admin = _admin_id(auth_engine)
    _set_stored_providers(auth_engine, admin, "")

    assert _prefs_for(auth_engine, admin).search_providers == frozenset()


def test_load_prefs_falls_back_only_for_a_reader_with_no_preference_of_their_own(
    auth_engine: Engine,
) -> None:
    """No row of their own is still "no preference", which DEFAULTS answers.

    The written-empty case above is the one that changed; this is the case that
    did not, so the two together say "empty means off, absent means default".
    """
    admin = _admin_id(auth_engine)
    with auth_engine.begin() as conn:
        conn.execute(text("DELETE FROM settings WHERE owner_id = :o"), {"o": admin})

    assert _prefs_for(auth_engine, admin).search_providers == frozenset({"openlibrary"})

    _set_stored_providers(auth_engine, admin, "")
    assert _prefs_for(auth_engine, admin).search_providers == frozenset()


def test_the_settings_page_renders_what_the_registry_says_about_a_provider(
    auth_client: TestClient,
) -> None:
    """A provider may describe itself; the card links it and links where to help."""
    sign_in(auth_client)

    page = auth_client.get("/settings")

    about = '<a href="https://openlibrary.org" target="_blank" rel="noopener">'
    contribute = '<a href="https://openlibrary.org/about" target="_blank" rel="noopener">'
    assert about in page.text
    assert "free &amp; open source</a>" in page.text
    assert contribute + "Contribute</a>" in page.text
    # A provider with nothing to say renders no note, so the count is the registry's.
    with_a_note = [spec for spec in PROVIDERS if spec.description]
    assert page.text.count('class="provider-note') == len(with_a_note)


def test_the_settings_form_asks_before_switching_every_provider_off(
    auth_client: TestClient,
) -> None:
    """The confirm is client-side, so the page must carry it."""
    sign_in(auth_client)

    page = auth_client.get("/settings")

    assert "Are you sure you want to disable all search providers?" in page.text
    assert "The New book lookup won't work." in page.text
    assert 'input[name^="provider_"]:checked' in page.text
    assert "preventDefault" in page.text


def test_the_shelf_follows_the_stored_zoom(auth_client: TestClient) -> None:
    sign_in(auth_client)
    token = _csrf_from(auth_client.get("/settings").text)
    auth_client.post(
        "/settings",
        data={
            "theme": "light",
            "zoom_preset": "L",
            "currency_symbol": "$",
            "date_format": "yyyy-mm-dd",
            "time_format": "24h",
            "csrf_token": token,
        },
    )

    shelf = auth_client.get("/")
    assert shelf.status_code == 200
    assert 'aria-current="true">L<' in shelf.text


def test_searchable_assets_are_included_on_the_shelf(auth_client: TestClient) -> None:
    sign_in(auth_client)
    page = auth_client.get("/")
    assert page.status_code == 200
    assert 'src="/static/js/searchable.js"' in page.text
    assert 'href="/static/css/searchable.css"' in page.text
    assert 'select class="searchable' in page.text


def test_book_form_selects_are_searchable(auth_client: TestClient) -> None:
    sign_in(auth_client)
    page = auth_client.get("/book/new")
    assert page.status_code == 200
    assert 'src="/static/js/searchable.js"' in page.text
    assert 'select class="searchable' in page.text


def test_settings_selects_are_searchable(auth_client: TestClient) -> None:
    sign_in(auth_client)
    page = auth_client.get("/settings")
    assert page.status_code == 200
    assert 'src="/static/js/searchable.js"' in page.text
    assert 'select class="searchable' in page.text


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
