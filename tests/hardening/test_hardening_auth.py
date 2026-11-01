# Invite-only and multi-user hardening: the app goes on a public domain with no
# signup. Every account action must require an authenticated admin, no form may
# grant privileges, one reader's ids must be inert in another reader's browser,
# and a session must not survive the account that owned it.
from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from app import db as db_module
from app.auth import SESSION_COOKIE, create_user
from app.main import create_app
from app.settings import settings
from tests.alembic_runner import engine_for, run_alembic

READER_PASSWORD = "a-long-reader-password"


@pytest.fixture()
def auth_engine(monkeypatch: pytest.MonkeyPatch, tmp_path) -> Engine:
    """A migrated database (admin/admin exists) with auth switched on."""
    from sqlalchemy.orm import sessionmaker

    db_path = tmp_path / "invite.db"
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


def _csrf_from(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]*)"', html)
    assert match, "no CSRF token in the page"
    return match.group(1)


def _login(client: TestClient, username: str = "admin", password: str = "admin"):
    page = client.get("/login")
    response = client.post(
        "/login",
        data={
            "username": username,
            "password": password,
            "csrf_token": _csrf_from(page.text),
        },
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text
    return response


def sign_in(client: TestClient, username: str = "admin", password: str = "admin") -> None:
    _login(client, username, password)


def _make_reader(engine: Engine, username: str = "reader") -> int:
    from sqlalchemy.orm import sessionmaker

    with sessionmaker(bind=engine)() as session:
        create_user(session, username, READER_PASSWORD)
        return session.execute(
            text("SELECT id FROM users WHERE username = :u"), {"u": username}
        ).scalar_one()


def _scalar(engine: Engine, sql: str, params: dict | None = None):
    with engine.connect() as conn:
        return conn.execute(text(sql), params or {}).scalar()


def _book_form(token: str) -> dict:
    return {
        "csrf_token": token,
        "title": "Leviathan Wakes",
        "authors": "James S.A. Corey",
        "ownership_status": "owned",
        "reading_state": "unread",
    }


# ---------------------------------------------------------------- invite-only


def test_only_an_admin_can_create_a_user(auth_client: TestClient, auth_engine: Engine) -> None:
    """There is no public signup: the create-user route answers a signed-in
    reader with the same silent redirect it gives anyone, and the users table
    must not grow."""
    _make_reader(auth_engine)
    reader = TestClient(create_app())
    sign_in(reader, "reader", READER_PASSWORD)
    before = _scalar(auth_engine, "SELECT count(*) FROM users")

    response = reader.post(
        "/settings/users/new",
        data={
            "username": "sneaky",
            "password": "another-long-pass",
            "confirm_password": "another-long-pass",
            "csrf_token": _csrf_from(reader.get("/settings").text),
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert _scalar(auth_engine, "SELECT count(*) FROM users") == before
    assert _scalar(auth_engine, "SELECT count(*) FROM users WHERE username = 'sneaky'") == 0


def test_only_an_admin_can_delete_a_user(auth_client: TestClient, auth_engine: Engine) -> None:
    reader_id = _make_reader(auth_engine)
    reader = TestClient(create_app())
    sign_in(reader, "reader", READER_PASSWORD)
    token = _csrf_from(reader.get("/settings").text)

    response = reader.post(
        f"/settings/users/{reader_id}/delete",
        data={"current_password": READER_PASSWORD, "confirm_delete": "1", "csrf_token": token},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert _scalar(auth_engine, "SELECT count(*) FROM users") == 2

    # Trying to delete the admin is the same silent refusal.
    response = reader.post(
        "/settings/users/1/delete",
        data={"current_password": READER_PASSWORD, "confirm_delete": "1", "csrf_token": token},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert _scalar(auth_engine, "SELECT count(*) FROM users WHERE username = 'admin'") == 1


def test_anonymous_account_actions_are_redirected_to_login(
    auth_client: TestClient, auth_engine: Engine
) -> None:
    for path, data in (
        ("/settings/users/new", {"username": "x", "password": "long-enough-pass"}),
        ("/settings/users/1/delete", {"confirm_delete": "1"}),
    ):
        response = auth_client.post(path, data=data, follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"].startswith("/login"), path
    assert _scalar(auth_engine, "SELECT count(*) FROM users") == 1


# ---------------------------------------------------------------- escalation


def test_no_form_field_can_grant_admin(auth_client: TestClient, auth_engine: Engine) -> None:
    """is_admin never travels through a form. A reader posting it anywhere —
    their own settings, the create-user route — must stay a reader."""
    _make_reader(auth_engine)

    # The admin's own create-user form ignores an is_admin field.
    sign_in(auth_client)
    auth_client.post(
        "/settings/users/new",
        data={
            "username": "hoped-admin",
            "password": "long-enough-pass",
            "confirm_password": "long-enough-pass",
            "is_admin": "1",
            "csrf_token": _csrf_from(auth_client.get("/settings").text),
        },
        follow_redirects=False,
    )
    assert (
        _scalar(auth_engine, "SELECT is_admin FROM users WHERE username = 'hoped-admin'") == 0
    )

    # A reader posting is_admin=1 to their own settings stays a reader.
    reader = TestClient(create_app())
    sign_in(reader, "reader", READER_PASSWORD)
    response = reader.post(
        "/settings",
        data={
            "theme": "dark",
            "zoom_preset": "M",
            "date_format": "dd.mm.yyyy",
            "time_format": "24h",
            "currency_symbol": "\u20ac",
            "is_admin": "1",
            "csrf_token": _csrf_from(reader.get("/settings").text),
        },
        follow_redirects=False,
    )
    assert response.status_code in (200, 303)
    assert _scalar(auth_engine, "SELECT is_admin FROM users WHERE username = 'reader'") == 0


# ---------------------------------------------------------------- isolation


def test_a_reader_cannot_open_or_change_another_readers_book(
    auth_client: TestClient, auth_engine: Engine
) -> None:
    """Another owner's book id answers 404, not 403: ids must not leak
    existence. Exports are scoped to the signed-in owner, so the CSV must not
    carry the other reader's rows either."""
    sign_in(auth_client)
    response = auth_client.post(
        "/book/new",
        data=_book_form(_csrf_from(auth_client.get("/book/new").text)),
        follow_redirects=False,
    )
    assert response.status_code == 303
    book_id = _scalar(auth_engine, "SELECT max(id) FROM books")
    _make_reader(auth_engine)

    reader = TestClient(create_app())
    sign_in(reader, "reader", READER_PASSWORD)
    for path in (
        f"/book/{book_id}",
        f"/book/{book_id}/edit",
        f"/book/{book_id}/photos/front",
    ):
        assert reader.get(path).status_code == 404, path

    export = reader.get("/export.csv")
    assert export.status_code == 200
    assert "Leviathan Wakes" not in export.text

    # Mutations are just as dead: the edit post does not reach the row.
    response = reader.post(
        f"/book/{book_id}/edit",
        data={**_book_form(_csrf_from(reader.get("/book/new").text)), "title": "Hacked"},
        follow_redirects=False,
    )
    assert response.status_code == 404
    assert _scalar(auth_engine, "SELECT title FROM books WHERE id = :i", {"i": book_id}) == (
        "Leviathan Wakes"
    )

    # Both delete routes (the canonical one and its legacy alias) refuse.
    token = _csrf_from(reader.get("/book/new").text)
    for path in (f"/book/{book_id}/delete", f"/books/{book_id}/delete"):
        assert (
            reader.post(path, data={"csrf_token": token}, follow_redirects=False).status_code
            == 404
        ), path
    assert _scalar(auth_engine, "SELECT count(*) FROM books WHERE id = :i", {"i": book_id}) == 1


# ---------------------------------------------------------------- sessions


def test_login_hands_out_a_fresh_session_every_time(auth_client: TestClient) -> None:
    """No session fixation: every login mints a new random token, never a
    reuse of a pre-login or prior value."""
    first = _login(auth_client).cookies[SESSION_COOKIE]
    second = _login(auth_client).cookies[SESSION_COOKIE]
    assert first and second and first != second


def test_deleting_a_user_ends_its_sessions_and_removes_its_photos(
    auth_client: TestClient,
    auth_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """delete_user is permanent: the photos come off disk, the owned rows go
    with the account, the login throttle history is dropped, and a cookie from
    the deleted account opens nothing afterwards."""
    from pathlib import Path

    upload_dir = tmp_path / "photos"
    upload_dir.mkdir()
    monkeypatch.setattr(settings, "upload_dir", str(upload_dir))

    reader_id = _make_reader(auth_engine)
    reader = TestClient(create_app())
    sign_in(reader, "reader", READER_PASSWORD)
    reader.post(
        "/book/new",
        data=_book_form(_csrf_from(reader.get("/book/new").text)),
        follow_redirects=False,
    )
    book_id = _scalar(auth_engine, "SELECT max(id) FROM books")

    # The photo row and file the app itself would have written.
    photo_name = f"book{book_id}-front-deadbeef.jpg"
    assert isinstance(upload_dir, Path)
    (upload_dir / photo_name).write_bytes(b"jpeg")
    from sqlalchemy.orm import sessionmaker

    with sessionmaker(bind=auth_engine)() as session:
        session.execute(
            text(
                "INSERT INTO photos (owner_id, book_id, slot, provider_filename) "
                "VALUES (:owner, :book, 'front', :name)"
            ),
            {"owner": reader_id, "book": book_id, "name": photo_name},
        )
        session.commit()

    sign_in(auth_client)
    response = auth_client.post(
        f"/settings/users/{reader_id}/delete",
        data={
            "current_password": "admin",
            "confirm_delete": "1",
            "csrf_token": _csrf_from(auth_client.get("/settings").text),
        },
        follow_redirects=False,
    )
    assert response.status_code == 303

    assert not (upload_dir / photo_name).exists()
    assert (
        _scalar(auth_engine, "SELECT count(*) FROM photos WHERE book_id = :i", {"i": book_id})
        == 0
    )
    assert (
        _scalar(auth_engine, "SELECT count(*) FROM books WHERE owner_id = :o", {"o": reader_id})
        == 0
    )

    # The dead account's cookie opens nothing, and its credentials sign nobody in.
    assert reader.get("/", follow_redirects=False).status_code == 303
    page = reader.get("/login")
    response = reader.post(
        "/login",
        data={
            "username": "reader",
            "password": READER_PASSWORD,
            "csrf_token": _csrf_from(page.text),
        },
        follow_redirects=False,
    )
    assert response.status_code == 401
