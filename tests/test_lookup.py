# Lookup tests with a stubbed transport (M4's verification requirement). Nothing
# here touches the network: the provider is replaced with httpx.MockTransport, so
# a slow or failing Open Library cannot make the suite flaky — and the failure
# path is a first-class case, not an afterthought.
from __future__ import annotations

from typing import Any

import httpx
import pytest

from app import lookup as lookup_module
from app.lookup import (
    PROVIDER_LABELS,
    PROVIDER_ORDER,
    PROVIDERS,
    fetch_provider_cover,
    isbn_lookup,
    search,
)


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


def test_open_library_page_count_falls_back_to_the_median(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # OL's /search.json serves number_of_pages_median, not num_pages; the page
    # count must still be filled from it rather than left blank.
    document = {"key": "/works/OL2", "title": "No Pages Field", "number_of_pages_median": 480}
    _install(monkeypatch, lambda request: httpx.Response(200, json={"docs": [document]}))

    candidate = search("No Pages Field").candidates[0]

    assert candidate.page_count == 480


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
    """Per-user keys only: a reader who entered none leaves Google Books out."""
    hosts = _install_hosts(monkeypatch, lambda request: httpx.Response(200, json={"docs": []}))

    result = search("Dune", providers=("openlibrary", "googlebooks"))

    assert hosts == ["openlibrary.org"]  # no second provider is reached
    assert result.failed is False  # enabled but unconfigured is not a failure

    # A blank key is no key: the provider is skipped just as quietly.
    hosts.clear()
    search("Dune", providers=("openlibrary", "googlebooks"), provider_keys={"googlebooks": "  "})
    assert hosts == ["openlibrary.org"]


def test_a_readers_own_key_reaches_the_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """The key from the settings page is the only key there is, and it is the one used."""
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["host"] = request.url.host
        seen["key"] = request.url.params.get("key", "")
        return httpx.Response(200, json={"items": []})

    _install(monkeypatch, handler)
    search("Dune", providers=("googlebooks",), provider_keys={"googlebooks": "userkey"})

    assert seen["host"] == "www.googleapis.com"
    assert seen["key"] == "userkey"


def test_a_pasted_key_is_sent_trimmed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Whitespace around a pasted key is typing noise, not part of the key."""
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["key"] = request.url.params.get("key", "")
        return httpx.Response(200, json={"items": []})

    _install(monkeypatch, handler)
    search("Dune", providers=("googlebooks",), provider_keys={"googlebooks": "  userkey  "})

    assert seen["key"] == "userkey"


def test_every_keyed_provider_gets_only_its_own_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """One key per provider: Google Books takes it in the query, ISBNdb in the
    Authorization header, and neither one turns up in the other's request."""
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if "googleapis" in request.url.host:
            seen["google_key"] = request.url.params.get("key", "")
            seen["google_auth"] = request.headers.get("Authorization", "")
            return httpx.Response(200, json={"items": []})
        seen["isbndb_auth"] = request.headers.get("Authorization", "")
        seen["isbndb_key"] = request.url.params.get("key", "")
        return httpx.Response(200, json={"results": []})

    _install(monkeypatch, handler)
    search(
        "Leviathan Wakes",
        providers=("googlebooks", "isbndb"),
        provider_keys={"googlebooks": "g-key", "isbndb": "i-key"},
    )

    assert seen["google_key"] == "g-key"
    assert seen["google_auth"] == ""
    assert seen["isbndb_auth"] == "i-key"
    assert seen["isbndb_key"] == ""


def test_google_books_results_are_parsed_when_a_key_is_set(monkeypatch: pytest.MonkeyPatch) -> None:
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
    result = search(
        "The Left Hand of Darkness",
        providers=("openlibrary", "googlebooks"),
        provider_keys={"googlebooks": "test-key"},
    )

    google = [candidate for candidate in result.candidates if candidate.provider == "googlebooks"]
    assert len(google) == 1
    assert google[0].published_year == 1969
    assert google[0].page_count == 192
    assert google[0].isbn == "9780441478125"  # ISBN-10 re-keyed to 13 with a recomputed check digit
    assert google[0].covers[0].url.endswith("thumb.jpg")


# --- the provider registry: what Settings renders and search() dispatches on ---

_GOOGLE_ITEM = {
    "id": "g1",
    "volumeInfo": {
        "title": "Leviathan Wakes",
        "authors": ["James S.A. Corey"],
        "publisher": "Orbit",
        "publishedDate": "2011-08-16",
        "pageCount": 592,
        "language": "en",
        "imageLinks": {"thumbnail": "https://books.example/thumb.jpg"},
    },
}


def _one_book_each(request: httpx.Request) -> httpx.Response:
    """The same title from both hosts, so a candidate's provider says who answered."""
    if "googleapis" in request.url.host:
        return httpx.Response(200, json={"items": [_GOOGLE_ITEM]})
    return httpx.Response(200, json={"docs": [_OL_DOCUMENT]})


def test_every_registered_provider_is_searchable() -> None:
    # The registry is the single source: an entry without a query function would
    # be a checkbox in Settings that silently never returns anything.
    assert [spec.id for spec in PROVIDERS] == PROVIDER_ORDER
    assert {spec.id: spec.label for spec in PROVIDERS} == PROVIDER_LABELS
    for spec in PROVIDERS:
        assert spec.id in lookup_module._QUERY_FUNCS


def test_only_the_enabled_provider_is_asked(monkeypatch: pytest.MonkeyPatch) -> None:
    hosts = _install_hosts(monkeypatch, _one_book_each)

    result = search(
        "Leviathan Wakes", providers=("googlebooks",), provider_keys={"googlebooks": "test-key"}
    )

    assert hosts == ["www.googleapis.com"]  # Open Library stays untouched
    assert [candidate.provider for candidate in result.candidates] == ["googlebooks"]


def test_both_enabled_providers_answer_into_one_list(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, _one_book_each)

    result = search(
        "Leviathan Wakes",
        providers=("openlibrary", "googlebooks"),
        provider_keys={"googlebooks": "test-key"},
    )

    assert [candidate.provider for candidate in result.candidates] == [
        "openlibrary",
        "googlebooks",
    ]
    assert result.failed is False


def test_an_isbn_query_reaches_google_books_as_an_isbn_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if "googleapis" in request.url.host:
            seen["q"] = request.url.params.get("q", "")
            seen["key"] = request.url.params.get("key", "")
        return httpx.Response(200, json={"items": [], "docs": []})

    _install(monkeypatch, handler)
    search(
        "978-0441018369",
        providers=("openlibrary", "googlebooks"),
        provider_keys={"googlebooks": "test-key"},
    )

    assert seen["q"] == "isbn:9780441018369"  # exact, not the raw dashed string
    assert seen["key"] == "test-key"


def test_an_unknown_provider_id_yields_nothing_without_an_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A provider that left the registry can still sit in someone's row. Ignoring
    # it is right; failing the search is not — nothing went wrong.
    calls = _install(monkeypatch, lambda request: httpx.Response(200, json={"docs": []}))

    result = search("Dune", providers=("nope",))

    assert result.candidates == []
    assert result.failed is False
    assert calls == []


def test_one_provider_being_down_does_not_silence_the_other(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "googleapis" in request.url.host:
            return httpx.Response(200, json={"items": [_GOOGLE_ITEM]})
        raise httpx.ConnectTimeout("connection timed out")

    _install(monkeypatch, handler)
    result = search(
        "Leviathan Wakes",
        providers=("openlibrary", "googlebooks"),
        provider_keys={"googlebooks": "test-key"},
    )

    assert result.error == "Open Library is unreachable: ConnectTimeout"
    assert [candidate.provider for candidate in result.candidates] == ["googlebooks"]


# --- ISBNdb: the third provider, keyed, registered exactly like the other two ---

_ISBNDB_BOOK = {
    "isbn13": "9780441018369",
    "isbn10": "0441018369",
    "title": "Leviathan Wakes",
    "publisher": "Orbit",
    "language": "en",
    "date_published": "2011-08-16",
    "pages": 592,
    "overview": "Ignatius Reznikov finds the ring.",
    "image": "https://isbndb.com/cover/9780441018369.jpg",
    # A nameless author entry and a bare string are both junk the API really does
    # return; neither may reach the form as an author.
    "authors": [{"name": "James S.A. Corey"}, {"name": None}, "Martinez"],
}


def _isbndb_payload(request: httpx.Request) -> httpx.Response:
    """One book from ISBNdb, empty answers from everybody else."""
    if "isbndb" in request.url.host:
        return httpx.Response(200, json={"total": 1, "results": [_ISBNDB_BOOK]})
    return httpx.Response(200, json={"docs": []})


def _isbndb_mixed_handler(request: httpx.Request) -> httpx.Response:
    """The same title from Open Library and from ISBNdb, so each candidate's
    provider records who answered it."""
    if "isbndb" in request.url.host:
        return httpx.Response(200, json={"total": 1, "results": [_ISBNDB_BOOK]})
    return httpx.Response(200, json={"docs": [_OL_DOCUMENT]})


def test_isbndb_is_registered_after_google_books() -> None:
    # Settings renders this order and search() queries in it, so a candidate list
    # built from all three providers always reads Open Library, Google Books, ISBNdb.
    assert PROVIDER_ORDER == ["openlibrary", "googlebooks", "isbndb"]
    assert PROVIDER_LABELS["isbndb"] == "ISBNdb"


def test_an_isbn_query_hits_the_isbndb_book_endpoint_with_the_raw_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["host"] = request.url.host
        seen["path"] = request.url.path
        seen["auth"] = request.headers.get("Authorization", "")
        seen["ua"] = request.headers.get("User-Agent", "")
        return httpx.Response(200, json={"book": _ISBNDB_BOOK})

    _install(monkeypatch, handler)
    result = search("978-0441018369", providers=("isbndb",), provider_keys={"isbndb": "test-key"})

    assert seen["host"] == "api2.isbndb.com"
    assert seen["path"] == "/book/9780441018369"  # the normalised ISBN, as a path segment
    # ISBNdb wants the key alone in Authorization — no "Bearer " scheme in front.
    assert seen["auth"] == "test-key"
    assert seen["ua"].startswith("Colophon/")
    assert result.failed is False
    assert [candidate.provider for candidate in result.candidates] == ["isbndb"]


def test_a_title_query_hits_the_isbndb_books_path_with_a_page_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["url"] = str(request.url)
        seen["page_size"] = request.url.params.get("pageSize", "")
        return httpx.Response(200, json={"total": 0, "results": []})

    _install(monkeypatch, handler)
    search(
        "Leviathan Wakes Corey",
        providers=("isbndb",),
        provider_keys={"isbndb": "test-key"},
        limit=5,
    )

    # The query is a path segment, not a "?q=", and it travels percent-encoded.
    assert seen["path"] == "/books/Leviathan Wakes Corey"
    assert seen["url"].startswith("https://api2.isbndb.com/books/Leviathan%20Wakes%20Corey")
    assert seen["page_size"] == "5"


def test_an_isbndb_book_object_becomes_a_candidate(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, _isbndb_payload)
    result = search("Leviathan Wakes", providers=("isbndb",), provider_keys={"isbndb": "test-key"})

    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.provider == "isbndb"
    assert candidate.provider_key == "9780441018369"
    assert candidate.title == "Leviathan Wakes"
    assert candidate.authors == ("James S.A. Corey",)
    assert candidate.publisher == "Orbit"
    assert candidate.published_year == 2011
    assert candidate.page_count == 592
    assert candidate.isbn == "9780441018369"
    assert candidate.language == "en"
    assert [cover.url for cover in candidate.covers] == [
        "https://isbndb.com/cover/9780441018369.jpg"
    ]


def test_isbndb_books_without_a_title_are_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"total": 2, "results": [{"publisher": "Nobody Press"}, _ISBNDB_BOOK]}
    _install(monkeypatch, lambda request: httpx.Response(200, json=payload))

    candidates = search(
        "Leviathan Wakes", providers=("isbndb",), provider_keys={"isbndb": "test-key"}
    ).candidates

    assert [candidate.title for candidate in candidates] == ["Leviathan Wakes"]


def test_isbndb_answers_after_open_library_in_registry_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch, _isbndb_mixed_handler)

    result = search(
        "Leviathan Wakes",
        providers=("openlibrary", "isbndb"),
        provider_keys={"isbndb": "test-key"},
    )

    assert [candidate.provider for candidate in result.candidates] == ["openlibrary", "isbndb"]
    assert result.failed is False


def test_isbndb_is_not_called_when_it_is_not_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hosts = _install_hosts(monkeypatch, _isbndb_payload)

    search("Leviathan Wakes", providers=("openlibrary",), provider_keys={"isbndb": "test-key"})

    assert hosts == ["openlibrary.org"]  # a key sitting in the row is not an opt-in


def test_isbndb_is_not_called_without_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    hosts = _install_hosts(monkeypatch, _isbndb_payload)

    result = search("Leviathan Wakes", providers=("openlibrary", "isbndb"))

    assert hosts == ["openlibrary.org"]  # enabled but unanswerable: skipped, not failed
    assert result.failed is False
    assert [candidate.provider for candidate in result.candidates] == []


def test_a_key_for_another_provider_never_leaks_into_this_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Open Library is keyless: keys in the reader's row stay in their row."""
    seen: list[tuple[str, str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(
            (
                request.url.host,
                request.url.params.get("key", ""),
                request.headers.get("Authorization", ""),
            )
        )
        return httpx.Response(200, json={"docs": []})

    _install(monkeypatch, handler)
    search(
        "Dune",
        providers=("openlibrary",),
        provider_keys={"googlebooks": "g-key", "isbndb": "i-key"},
    )

    assert seen == [("openlibrary.org", "", "")]


def test_an_isbndb_rejection_is_reported_by_name_and_not_retried_elsewhere(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A 401 from a bad key surfaces as an HTTPError, which search() turns into a
    # named message. Open Library is never asked again to make up the shortfall.
    def handler(request: httpx.Request) -> httpx.Response:
        if "isbndb" in request.url.host:
            return httpx.Response(401, json={"message": "Unauthorized"})
        return httpx.Response(200, json={"docs": []})

    hosts = _install_hosts(monkeypatch, handler)
    result = search(
        "Leviathan Wakes",
        providers=("openlibrary", "isbndb"),
        provider_keys={"isbndb": "wrong-key"},
    )

    assert result.error == "ISBNdb is unreachable: HTTPStatusError"
    assert hosts == ["openlibrary.org", "api2.isbndb.com"]


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
