# Network-egress hardening for the provider relay: /book/lookup and the
# provider-cover fetch are the only places Colophon talks to the internet on a
# reader's behalf, and both take URLs or keys that came from a form. Nothing
# here touches the network — the transport is replaced with httpx.MockTransport,
# the same pattern tests/test_lookup.py uses.
from __future__ import annotations

import re
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from app import db as db_module
from app import lookup as lookup_module
from app.lookup import fetch_provider_cover
from app.main import create_app
from app.settings import settings
from tests.alembic_runner import engine_for, run_alembic

ALLOWED = "https://covers.openlibrary.org/b/id/1-L.jpg"

_OL_ISBN_RESPONSE = {
    "ISBN:9780441018369": {
        "title": "Leviathan Wakes",
        "authors": [{"name": "James S.A. Corey"}],
        "publishers": [{"name": "Orbit"}],
        "publish_date": "2011",
        "number_of_pages": 592,
        "languages": [{"key": "/languages/eng"}],
    }
}


def _install(monkeypatch: pytest.MonkeyPatch, handler) -> None:
    def factory(**kwargs: Any) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(lookup_module, "CLIENT_FACTORY", factory)


@pytest.fixture()
def auth_engine(monkeypatch: pytest.MonkeyPatch, tmp_path) -> Engine:
    from sqlalchemy.orm import sessionmaker

    db_path = tmp_path / "net.db"
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
    page = client.get("/login")
    match = re.search(r'name="csrf_token" value="([^"]*)"', page.text)
    assert match, "login form is missing its CSRF token"
    response = client.post(
        "/login",
        data={"username": username, "password": password, "csrf_token": match.group(1)},
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text


# ---------------------------------------------------------------- cover fetch


def test_a_redirect_off_the_allowlist_is_never_followed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cover host answering 302 to the cloud metadata address must end the
    fetch, not be followed. Redirects are re-validated hop by hop, so an open
    redirect on a provider host cannot turn a form-fed cover URL into an
    internal GET (redirects used to be followed blind)."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "169.254.169.254":
            return httpx.Response(200, content=b"secret-metadata")
        return httpx.Response(
            302, headers={"location": "http://169.254.169.254/latest/meta-data/"}
        )

    _install(monkeypatch, handler)
    assert fetch_provider_cover(ALLOWED) is None


def test_a_scheme_relative_redirect_is_resolved_then_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """//evil.example.com resolves against the https hop URL but still names a
    host the allowlist never saw."""
    _install(
        monkeypatch,
        lambda request: httpx.Response(302, headers={"location": "//evil.example.com/x.jpg"}),
    )
    assert fetch_provider_cover(ALLOWED) is None


def test_a_relative_redirect_on_the_provider_host_still_works(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Relative Locations are ordinary HTTP. Resolved against the hop URL they
    stay on the allowed host, so a provider that redirects among its own paths
    keeps working — the SSRF rule must not break the feature."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/b/id/1-L.jpg":
            return httpx.Response(302, headers={"location": "/b/id/1-L-large.jpg"})
        return httpx.Response(200, content=b"jpeg-bytes")

    _install(monkeypatch, handler)
    assert fetch_provider_cover(ALLOWED) == b"jpeg-bytes"


def test_a_redirect_loop_is_abandoned(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(
        monkeypatch, lambda request: httpx.Response(302, headers={"location": ALLOWED})
    )
    assert fetch_provider_cover(ALLOWED) is None


def test_an_oversized_cover_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """The bytes land in RAM on the single worker: past the cap the answer is
    None, not an allocation sized by whatever the other end sends."""
    big = b"x" * (lookup_module.MAX_COVER_BYTES + 1)
    _install(monkeypatch, lambda request: httpx.Response(200, content=big))
    assert fetch_provider_cover(ALLOWED) is None


def test_covers_come_from_https_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """cover_url is form input; a plain-http fetch would let anyone on the wire
    of a public install observe every lookup. All three allowed hosts serve
    https, so http is refused before a byte is requested."""
    calls: list[str] = []

    def factory(**kwargs: Any) -> httpx.Client:
        calls.append("built")
        return httpx.Client(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"j")),
            **kwargs,
        )

    monkeypatch.setattr(lookup_module, "CLIENT_FACTORY", factory)
    assert fetch_provider_cover("http://covers.openlibrary.org/b/id/1-L.jpg") is None
    assert calls == []


@pytest.mark.parametrize(
    ("url", "expect_fetch"),
    [
        ("https://covers.openlibrary.org@evil.com/x.jpg", False),  # userinfo: host is evil.com
        ("https://evil.com/covers.openlibrary.org/x.jpg", False),
        ("ftp://covers.openlibrary.org/x.jpg", False),
        ("//covers.openlibrary.org/x.jpg", False),  # scheme-relative: not https
        ("https://covers.openlibrary.org.evil.com/x.jpg", False),
        ("https://foo.covers.openlibrary.org/x.jpg", True),  # the documented subdomain rule
    ],
)
def test_the_cover_host_allowlist_holds_its_edges(
    monkeypatch: pytest.MonkeyPatch, url: str, expect_fetch: bool
) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.host)
        return httpx.Response(200, content=b"j")

    _install(monkeypatch, handler)
    result = fetch_provider_cover(url)
    if expect_fetch:
        assert result == b"j"
        assert seen == ["foo.covers.openlibrary.org"]
    else:
        assert result is None
        assert seen == []


# ---------------------------------------------------------------- /book/lookup


def test_lookup_refuses_an_isbn_past_the_limit(auth_client: TestClient) -> None:
    sign_in(auth_client)
    response = auth_client.get("/book/lookup", params={"isbn": "9" * 33})
    assert response.status_code == 422


def test_lookup_answers_found_false_for_a_malformed_isbn(auth_client: TestClient) -> None:
    sign_in(auth_client)
    response = auth_client.get("/book/lookup", params={"isbn": "not-an-isbn"})
    assert response.status_code == 200
    assert response.json() == {"found": False}


def test_lookup_returns_only_the_whitelisted_metadata_keys(
    auth_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The JSON feeds the add-form's prefill: it must carry the six metadata
    fields and nothing else — no provider internals, no raw payload."""
    _install(monkeypatch, lambda request: httpx.Response(200, json=_OL_ISBN_RESPONSE))
    sign_in(auth_client)
    response = auth_client.get("/book/lookup", params={"isbn": "9780441018369"})
    assert response.status_code == 200
    assert response.json() == {
        "found": True,
        "title": "Leviathan Wakes",
        "authors": ["James S.A. Corey"],
        "publisher": "Orbit",
        "publication_year": 2011,
        "page_count": 592,
        "language": "English",
    }
