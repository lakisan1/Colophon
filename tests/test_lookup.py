# Lookup tests with a stubbed transport (M4's verification requirement). Nothing
# here touches the network: the provider is replaced with httpx.MockTransport, so
# a slow or failing Open Library cannot make the suite flaky — and the failure
# path is a first-class case, not an afterthought.
from __future__ import annotations

from typing import Any

import httpx
import pytest

from app import lookup as lookup_module
from app.lookup import fetch_provider_cover, isbn_lookup, search


def _stub(handler, calls: list[str]):
    """A CLIENT_FACTORY that answers locally and records what was requested."""

    def factory(**kwargs: Any) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(handler), **kwargs)

    return factory


def _install_hosts(monkeypatch: pytest.MonkeyPatch, handler) -> list[str]:
    """Like _install, but records the host of every request that was made."""
    hosts: list[str] = []

    def recording(request: httpx.Request) -> httpx.Response:
        hosts.append(request.url.host)
        return handler(request)

    def factory(**kwargs) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(recording), **kwargs)

    monkeypatch.setattr(lookup_module, "CLIENT_FACTORY", factory)
    return hosts


def _install(monkeypatch: pytest.MonkeyPatch, handler) -> list[str]:
    calls: list[str] = []
    monkeypatch.setattr(lookup_module, "CLIENT_FACTORY", _stub(handler, calls))
    return calls


_OL_DOCUMENT = {
    "key": "/works/OL123A",
    "title": "Leviathan Wakes",
    "subtitle": "The Expanse 1",
    "author_name": ["James S.A. Corey"],
    "publisher": ["Orbit"],
    "publish_year": [2011],
    "num_pages": [592],
    "isbn": ["9780441018369", "0441018369"],
    "language": ["eng"],
    "cover_i": 8033155,
}


def test_an_isbn_query_searches_by_isbn(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["isbn"] = request.url.params.get("isbn", "")
        seen["ua"] = request.headers.get("User-Agent", "")
        return httpx.Response(200, json={"docs": []})

    _install(monkeypatch, handler)
    search("978-0441018369")

    assert seen["path"].endswith("/search.json")
    assert seen["isbn"] == "9780441018369"  # dashes normalised away
    assert seen["ua"].startswith("Colophon/")


def test_a_title_query_searches_by_title(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["title"] = request.url.params.get("title", "")
        seen["has_isbn_param"] = "isbn" in request.url.params
        return httpx.Response(200, json={"docs": []})

    _install(monkeypatch, handler)
    search("Leviathan Wakes Corey")

    assert seen["title"] == "Leviathan Wakes Corey"
    assert seen["has_isbn_param"] is False


def test_open_library_document_becomes_a_candidate(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, lambda request: httpx.Response(200, json={"docs": [_OL_DOCUMENT]}))
    result = search("Leviathan Wakes")

    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.title == "Leviathan Wakes"
    assert candidate.subtitle == "The Expanse 1"
    assert candidate.authors == ("James S.A. Corey",)
    assert candidate.publisher == "Orbit"
    assert candidate.published_year == 2011
    assert candidate.page_count == 592
    assert candidate.isbn == "9780441018369"
    assert candidate.provider == "openlibrary"


def test_cover_candidates_are_offered_large_first(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, lambda request: httpx.Response(200, json={"docs": [_OL_DOCUMENT]}))
    candidate = search("x").candidates[0]

    assert [cover.label for cover in candidate.covers] == ["large", "medium"]
    assert all("covers.openlibrary.org/b/id/8033155" in cover.url for cover in candidate.covers)


def test_documents_without_a_title_are_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(
        monkeypatch,
        lambda request: httpx.Response(200, json={"docs": [{"author_name": ["Nobody"]}]}),
    )

    assert search("x").candidates == []


def test_a_timeout_is_reported_not_raised(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("connection timed out")

    _install(monkeypatch, handler)
    result = search("Dune")

    assert result.failed
    assert "Open Library" in (result.error or "")
    assert result.candidates == []


def test_a_500_from_the_provider_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, lambda request: httpx.Response(500, text="boom"))
    result = search("Dune")

    assert result.failed
    assert "Open Library" in (result.error or "")


def test_an_empty_query_calls_no_one(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _install(monkeypatch, lambda request: httpx.Response(200, json={"docs": []}))

    assert search("   ").candidates == []
    assert calls == []


def test_google_books_is_not_called_without_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.settings import settings

    monkeypatch.setattr(settings, "google_books_key", None)
    hosts = _install_hosts(monkeypatch, lambda request: httpx.Response(200, json={"docs": []}))

    search("Dune", include_google=True)

    assert hosts == ["openlibrary.org"]  # no second provider is reached


def test_google_books_results_are_parsed_when_a_key_is_set(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.settings import settings

    monkeypatch.setattr(settings, "google_books_key", "test-key")

    payload = {
        "items": [
            {
                "id": "abc123",
                "volumeInfo": {
                    "title": "The Left Hand of God",
                    "authors": ["Ursula K. Le Guin"],
                    "publisher": "Ace",
                    "publishedDate": "1969-01-01",
                    "pageCount": 192,
                    "language": "en",
                    "industryIdentifiers": [{"type": "ISBN_10", "identifier": "0441478123"}],
                    "imageLinks": {"thumbnail": "https://books.example/thumb.jpg"},
                },
            }
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if "googleapis" in request.url.host:
            return httpx.Response(200, json=payload)
        return httpx.Response(200, json={"docs": []})

    _install(monkeypatch, handler)
    result = search("The Left Hand of Darkness", include_google=True)

    google = [candidate for candidate in result.candidates if candidate.provider == "googlebooks"]
    assert len(google) == 1
    assert google[0].published_year == 1969
    assert google[0].page_count == 192
    assert google[0].isbn == "9780441478125"  # ISBN-10 re-keyed to 13 with a recomputed check digit
    assert google[0].covers[0].url.endswith("thumb.jpg")


def test_provider_cover_download_returns_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, lambda request: httpx.Response(200, content=b"\xff\xd8\xff\xfffakejpeg"))

    assert (
        fetch_provider_cover("https://covers.openlibrary.org/b/id/1-L.jpg")
        == b"\xff\xd8\xff\xfffakejpeg"
    )


def test_provider_cover_download_failure_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route")

    _install(monkeypatch, handler)

    assert fetch_provider_cover("https://covers.openlibrary.org/b/id/1-L.jpg") is None


def test_provider_cover_rejects_non_http_urls() -> None:
    assert fetch_provider_cover("file:///etc/passwd") is None
    assert fetch_provider_cover("") is None


def test_isbn_lookup_calls_the_open_library_isbn_api(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["bibkeys"] = request.url.params.get("bibkeys", "")
        seen["jscmd"] = request.url.params.get("jscmd", "")
        seen["ua"] = request.headers.get("User-Agent", "")
        return httpx.Response(
            200,
            json={
                "ISBN:9780140328721": {
                    "title": "Fantastic Mr. Fox",
                    "authors": [{"name": "Roald Dahl"}],
                    "publishers": [{"name": "Puffin"}],
                    "publish_date": "October 1, 1988",
                    "number_of_pages": 96,
                    "languages": [{"key": "/languages/eng"}],
                }
            },
        )

    _install(monkeypatch, handler)
    candidate = isbn_lookup("978-0140328721")

    assert seen["path"].endswith("/api/books")
    assert seen["bibkeys"] == "ISBN:9780140328721"  # dashes normalised away
    assert seen["jscmd"] == "data"
    assert seen["ua"].startswith("Colophon/")
    assert candidate is not None
    assert candidate.title == "Fantastic Mr. Fox"
    assert candidate.authors == ("Roald Dahl",)
    assert candidate.publisher == "Puffin"
    assert candidate.published_year == 1988
    assert candidate.page_count == 96
    assert candidate.language == "English"
    assert candidate.isbn == "9780140328721"
    assert candidate.provider == "openlibrary"


def test_isbn_lookup_returns_none_for_an_invalid_isbn(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _install(monkeypatch, lambda request: httpx.Response(200, json={}))
    assert isbn_lookup("not-an-isbn") is None
    assert calls == []  # no network call for garbage


def test_isbn_lookup_returns_none_when_the_edition_is_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch, lambda request: httpx.Response(200, json={}))
    assert isbn_lookup("9780140328721") is None


def test_isbn_lookup_returns_none_on_a_provider_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("connection timed out")

    _install(monkeypatch, handler)
    assert isbn_lookup("9780140328721") is None
