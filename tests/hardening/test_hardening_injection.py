# Injection-surface hardening: every rendered page mixes reader-entered text
# into HTML, every query mixes filter parameters into SQL, and the add-form
# carries a cover URL from form input. The probes below go through the HTTP
# layer with hostile values and assert the server treats them as data.
from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from app import db as db_module
from app.main import create_app
from app.settings import settings
from tests.alembic_runner import engine_for, run_alembic

SCRIPT_TITLE = "<script>alert(1)</script>"
IMG_PAYLOAD = '<img src=x onerror=alert(1)>'
TAG_PAYLOAD = '"><svg onload=alert(1)>'


@pytest.fixture()
def auth_engine(monkeypatch: pytest.MonkeyPatch, tmp_path) -> Engine:
    from sqlalchemy.orm import sessionmaker

    db_path = tmp_path / "inj.db"
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


def sign_in(client: TestClient, username: str = "admin", password: str = "admin") -> None:
    response = client.post(
        "/login",
        data={
            "username": username,
            "password": password,
            "csrf_token": _csrf_from(client.get("/login").text),
        },
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text


@pytest.fixture()
def hostile_book(auth_client: TestClient, auth_engine: Engine) -> int:
    """A book whose text fields are XSS payloads, created through the real
    form path so the test covers exactly what a reader could store."""
    sign_in(auth_client)
    response = auth_client.post(
        "/book/new",
        data={
            "csrf_token": _csrf_from(auth_client.get("/book/new").text),
            "title": SCRIPT_TITLE,
            "authors": IMG_PAYLOAD,
            "tags": TAG_PAYLOAD,
            "cover_url": "javascript:alert(1)",
            "ownership_status": "owned",
            "reading_state": "unread",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text
    with auth_engine.connect() as conn:
        return conn.execute(text("SELECT max(id) FROM books")).scalar_one()


def test_hostile_text_never_renders_as_markup(
    auth_client: TestClient, hostile_book: int
) -> None:
    """Autoescape must turn the payloads into text on every page that shows
    them: the shelf spine, the detail page and the edit form (whose value
    attributes are the classic broken-escape spot)."""
    for path in ("/", f"/book/{hostile_book}", f"/book/{hostile_book}/edit"):
        page = auth_client.get(path)
        assert page.status_code == 200, path
        assert "<script>" not in page.text, path
        assert "<img" not in page.text, path
        assert "<svg" not in page.text, path
    # The escaped form IS present: the text reached the page as text, and the
    # assertion fails if a future change stops rendering the title at all.
    assert "&lt;script&gt;" in auth_client.get("/").text


def test_the_cover_url_never_becomes_a_browser_url(
    auth_client: TestClient, hostile_book: int
) -> None:
    """cover_url is server-side input (the provider-cover cache consumes it);
    it must never reach an href/src where javascript: would execute."""
    for path in (f"/book/{hostile_book}", f"/book/{hostile_book}/edit"):
        page = auth_client.get(path).text
        assert 'href="javascript:' not in page, path
        assert 'src="javascript:' not in page, path


def test_the_photo_status_message_cannot_inject(
    auth_client: TestClient, hostile_book: int
) -> None:
    page = auth_client.get(
        f"/book/{hostile_book}/edit",
        params={"photo_reason": '<script>alert(1)</script>'},
    )
    assert page.status_code == 200
    assert "<script>" not in page.text


@pytest.mark.parametrize(
    "params",
    [
        {"q": "'; DROP TABLE books;--"},
        {"q": '%"%_'},
        {"q": 'NEAR("a" "b")'},
        {"q": '"'},
        {"q": "*"},
        {"status": "owned'; DROP TABLE books;--"},
        {"ownership": "x; DROP TABLE books;--"},
        {"room": "1; DROP TABLE books;--"},
        {"year": "abc; DROP TABLE books;--"},
        {"reading": "unread; DROP TABLE books;--"},
        {"author": "1 OR 1=1"},
        {"tag": "1 UNION SELECT 1"},
        {"genre": "<script>alert(1)</script>"},
        {"kind": "x'; DROP TABLE books;--"},
    ],
)
def test_filter_parameters_are_data_not_code(
    auth_client: TestClient,
    hostile_book: int,
    auth_engine: Engine,
    params: dict,
) -> None:
    """Every shelf probe must render (filter junk falls back to defaults, FTS
    operators are quoted away by build_match) and the books table must still
    hold exactly the probe book afterwards."""
    response = auth_client.get("/", params=params)
    assert response.status_code == 200, params
    with auth_engine.connect() as conn:
        count = conn.execute(text("SELECT count(*) FROM books")).scalar()
    assert count == 1, params
    title = auth_engine.connect().execute(
        text("SELECT title FROM books WHERE id = :i"), {"i": hostile_book}
    ).scalar()
    assert title == SCRIPT_TITLE, params
