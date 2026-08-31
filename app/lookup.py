# Book metadata lookup (M4). Providers live in the PROVIDERS registry below:
# Open Library is on for everyone, Google Books is opted into per user on the
# settings page and still needs the server-side GOOGLE_BOOKS_KEY.
#
# Two rules shape this module. First: a provider call never raises — a network
# timeout must not turn into a 500 page, so failures come back as LookupResult.error
# and the form re-renders with the message. Second: one provider's failure never
# triggers a retry against another. Silent failover makes "the lookup is down"
# indistinguishable from "this book does not exist".
from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

import httpx

from app.isbn import normalize_isbn
from app.settings import settings

OPEN_LIBRARY_SEARCH = "https://openlibrary.org/search.json"
GOOGLE_BOOKS_SEARCH = "https://www.googleapis.com/books/v1/volumes"
USER_AGENT = "Colophon/0.1 (self-hosted book inventory)"
DEFAULT_TIMEOUT = 8.0

# Indirection so tests can hand the module a stub transport instead of reaching
# the network. Production always uses httpx.Client.
CLIENT_FACTORY = httpx.Client


@dataclass(frozen=True)
class ProviderSpec:
    """One search provider the settings page can offer.

    Adding a provider is one entry in PROVIDERS plus one function in
    _QUERY_FUNCS: the settings checkbox, the dispatch in search() and the label
    in an error message all read from this registry, so nothing else has to
    learn the new provider's name.
    """

    id: str
    label: str
    needs_key: bool = False


PROVIDERS: tuple[ProviderSpec, ...] = (
    ProviderSpec("openlibrary", "Open Library"),
    ProviderSpec("googlebooks", "Google Books", needs_key=True),
)

# Registry order is query order, so the same two providers produce the same
# candidate order for every reader, whichever box they ticked first.
PROVIDER_ORDER = [spec.id for spec in PROVIDERS]
PROVIDER_LABELS = {spec.id: spec.label for spec in PROVIDERS}
_PROVIDERS_BY_ID = {spec.id: spec for spec in PROVIDERS}


def _provider_key(provider_id: str) -> str | None:
    """The server-side API key a needs_key provider requires, when configured.

    A key lives in app/settings.py (one environment variable per provider), never
    in the user's row: the preference says "ask this provider", the server says
    whether it is able to.
    """
    if provider_id == "googlebooks":
        return settings.google_books_key
    return None


@dataclass(frozen=True)
class CoverCandidate:
    """One image the provider offers, before anything is downloaded or stored."""

    provider: str
    url: str
    label: str


@dataclass(frozen=True)
class Candidate:
    """A work as the provider describes it. Feeds the form, never the database."""

    provider: str
    provider_key: str
    title: str
    subtitle: str | None
    authors: tuple[str, ...]
    publisher: str | None
    published_year: int | None
    page_count: int | None
    isbn: str | None
    language: str | None
    covers: tuple[CoverCandidate, ...] = field(default_factory=tuple)


@dataclass
class LookupResult:
    query: str
    candidates: list[Candidate] = field(default_factory=list)
    error: str | None = None

    @property
    def failed(self) -> bool:
        return self.error is not None


def search(
    query: str, *, providers: Iterable[str] = ("openlibrary",), limit: int = 8
) -> LookupResult:
    """Look a book up by ISBN when the query is one, otherwise by title/author.

    `providers` is the reader's enabled set; everything happens in registry
    order. An id the registry does not know is ignored rather than fatal, and an
    empty set calls nobody — no candidates and no error, because nothing failed.
    """
    raw = (query or "").strip()
    if not raw:
        return LookupResult(query=raw)

    isbn = normalize_isbn(raw)
    result = LookupResult(query=raw)
    enabled = set(providers)

    for provider_id in PROVIDER_ORDER:
        if provider_id not in enabled or provider_id not in PROVIDER_LABELS:
            continue
        query_func = _QUERY_FUNCS.get(provider_id)
        if query_func is None:  # registered without a query function: nobody to ask
            continue
        if _PROVIDERS_BY_ID[provider_id].needs_key and not _provider_key(provider_id):
            # Enabled but unusable: the server has no key, so this provider stays
            # silent instead of failing every search it takes part in.
            continue
        try:
            result.candidates += query_func(raw, isbn, limit)
        except httpx.HTTPError as exc:
            # Named, not retried somewhere else. It ends this provider's turn and
            # nothing more: a second provider the reader enabled still answers.
            result.error = f"{PROVIDER_LABELS[provider_id]} is unreachable: {type(exc).__name__}"

    return result


def _open_library(raw: str, isbn: str | None, limit: int) -> list[Candidate]:
    params: dict[str, str | int] = {"limit": limit, "fields": _OL_FIELDS}
    params["isbn"] = isbn if isbn else None
    if isbn is None:
        params.pop("isbn")
        params["title"] = raw

    with CLIENT_FACTORY(timeout=DEFAULT_TIMEOUT, headers={"User-Agent": USER_AGENT}) as client:
        response = client.get(OPEN_LIBRARY_SEARCH, params=params)
        response.raise_for_status()
        documents = response.json().get("docs", [])

    candidates: list[Candidate] = []
    for document in documents:
        candidate = _candidate_from_ol(document)
        if candidate is not None:
            candidates.append(candidate)
    return candidates


_OL_FIELDS = (
    "key,title,subtitle,author_name,publisher,publish_year,num_pages,"
    "number_of_pages_median,isbn,language,cover_i"
)


def _candidate_from_ol(document: dict) -> Candidate | None:
    title = (document.get("title") or "").strip()
    if not title:
        return None

    ol_key = document.get("key") or ""
    covers: list[CoverCandidate] = []
    cover_id = document.get("cover_i")
    if cover_id:
        # Open Library serves sized variants of the same id; large first, because
        # the picker shows them in that order.
        covers.append(
            CoverCandidate(
                "openlibrary", f"https://covers.openlibrary.org/b/id/{cover_id}-L.jpg", "large"
            )
        )
        covers.append(
            CoverCandidate(
                "openlibrary", f"https://covers.openlibrary.org/b/id/{cover_id}-M.jpg", "medium"
            )
        )

    isbn = None
    for value in document.get("isbn") or []:
        candidate_isbn = normalize_isbn(str(value))
        if candidate_isbn:
            isbn = candidate_isbn
            break

    return Candidate(
        provider="openlibrary",
        provider_key=ol_key,
        title=title[:300],
        subtitle=(document.get("subtitle") or "").strip()[:300] or None,
        authors=tuple(str(name).strip() for name in (document.get("author_name") or []) if name)[
            :6
        ],
        publisher=str((document.get("publisher") or [None])[0] or "").strip()[:200] or None,
        published_year=_first_int(document.get("publish_year")),
        page_count=_first_int(document.get("num_pages"))
        or _first_int(document.get("number_of_pages_median")),
        isbn=isbn,
        language=_first_language(document.get("language")),
        covers=tuple(covers),
    )


def _google_books(raw: str, isbn: str | None, limit: int) -> list[Candidate]:
    params: dict[str, str | int] = {"maxResults": limit, "key": settings.google_books_key}
    # Same rule as Open Library: an ISBN is searched exactly, anything else is
    # the free-text title/author query.
    params["q"] = f"isbn:{isbn}" if isbn else raw
    with CLIENT_FACTORY(timeout=DEFAULT_TIMEOUT, headers={"User-Agent": USER_AGENT}) as client:
        response = client.get(GOOGLE_BOOKS_SEARCH, params=params)
        response.raise_for_status()
        items = response.json().get("items", [])

    candidates: list[Candidate] = []
    for item in items:
        info = item.get("volumeInfo") or {}
        title = (info.get("title") or "").strip()
        if not title:
            continue
        isbn = None
        for identifier in info.get("industryIdentifiers") or []:
            if identifier.get("type") in ("ISBN_13", "ISBN_10"):
                isbn = normalize_isbn(str(identifier.get("identifier", ""))) or isbn
        thumbnails = info.get("imageLinks") or {}
        covers = tuple(
            CoverCandidate("googlebooks", thumbnails[key], label)
            for key, label in (("thumbnail", "medium"), ("large", "large"))
            if thumbnails.get(key)
        )
        candidates.append(
            Candidate(
                provider="googlebooks",
                provider_key=item.get("id") or "",
                title=title[:300],
                subtitle=(info.get("subtitle") or "").strip()[:300] or None,
                authors=tuple(str(a).strip() for a in (info.get("authors") or []) if a)[:6],
                publisher=(info.get("publisher") or "")[:200] or None,
                published_year=_year_from_date(info.get("publishedDate")),
                page_count=info.get("pageCount")
                if isinstance(info.get("pageCount"), int)
                else None,
                isbn=isbn,
                language=info.get("language"),
                covers=covers,
            )
        )
    return candidates


# One query function per provider id, every one of them taking
# (raw query, normalised isbn or None, limit) and returning candidates.
_QUERY_FUNCS: dict[str, Callable[[str, str | None, int], list[Candidate]]] = {
    "openlibrary": _open_library,
    "googlebooks": _google_books,
}


def _first_int(values) -> int | None:
    # OL returns some of these as a bare integer (num_pages, number_of_pages_median)
    # and others as a list (publish_year); treat a scalar as a one-item list.
    if isinstance(values, (int, str)):
        values = [values]
    for value in values or []:
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def _year_from_date(raw) -> int | None:
    # Publish dates look like "1988", "October 1, 1988", "2000-05-01" — the
    # year is the first 4-digit token, not necessarily the first number.
    text = str(raw or "")
    digits = "".join(character if character.isdigit() else " " for character in text).split()
    for token in digits:
        if len(token) == 4:
            return int(token)
    return None


def _first_language(values) -> str | None:
    for value in values or []:
        if isinstance(value, str):
            return value[:10]
    return None


def fetch_provider_cover(url: str, *, timeout: float = DEFAULT_TIMEOUT) -> bytes | None:
    """Download one provider image for caching (T6: provider bytes, our filename).

    Returns None on any failure. A book saved without art is still a saved book,
    and the shelf has no business showing a stack trace because a cover host
    timed out.
    """
    if not url.startswith(("https://", "http://")):
        return None
    try:
        with CLIENT_FACTORY(
            timeout=timeout, headers={"User-Agent": USER_AGENT}, follow_redirects=True
        ) as client:
            response = client.get(url)
            response.raise_for_status()
            content = response.content
    except httpx.HTTPError:
        return None
    return content if content else None


# Open Library language keys -> readable names, used by the ISBN lookup.
_OL_LANGUAGE = {
    "/languages/eng": "English",
    "/languages/fre": "French",
    "/languages/ger": "German",
    "/languages/spa": "Spanish",
    "/languages/ita": "Italian",
    "/languages/por": "Portuguese",
    "/languages/dut": "Dutch",
    "/languages/rus": "Russian",
    "/languages/pol": "Polish",
    "/languages/tur": "Turkish",
    "/languages/ara": "Arabic",
    "/languages/heb": "Hebrew",
    "/languages/hin": "Hindi",
    "/languages/jpn": "Japanese",
    "/languages/chi": "Chinese",
    "/languages/kor": "Korean",
    "/languages/swe": "Swedish",
    "/languages/nor": "Norwegian",
    "/languages/dan": "Danish",
    "/languages/fin": "Finnish",
    "/languages/cze": "Czech",
    "/languages/slo": "Slovak",
    "/languages/hun": "Hungarian",
    "/languages/gre": "Greek",
    "/languages/lat": "Latin",
}


def _ol_language_name(languages) -> str | None:
    """Map an Open Library language list to a readable language name."""
    for language in languages or []:
        if isinstance(language, dict):
            key = language.get("key") or language.get("name") or ""
        elif isinstance(language, str):
            key = language
        else:
            key = ""
        name = _OL_LANGUAGE.get(key) or key.rsplit("/", 1)[-1]
        if name:
            return name
    return None


def isbn_lookup(isbn: str) -> Candidate | None:
    """Look up a single ISBN on Open Library's free, no-key ISBN API.

    GET https://openlibrary.org/api/books?bibkeys=ISBN:{isbn}&jscmd=data&format=json
    returns exact edition metadata: title, authors, publishers, publish date,
    page count and languages.
    """
    normalized = normalize_isbn(isbn)
    if not normalized:
        return None
    bibkey = f"ISBN:{normalized}"
    url = "https://openlibrary.org/api/books"
    params = {"bibkeys": bibkey, "jscmd": "data", "format": "json"}
    try:
        with CLIENT_FACTORY(timeout=DEFAULT_TIMEOUT, headers={"User-Agent": USER_AGENT}) as client:
            response = client.get(url, params=params)
            if response.status_code != 200:
                return None
            payload = response.json()
    except Exception:
        return None

    record = payload.get(bibkey) or {}
    title = record.get("title") or ""
    if not title:
        return None

    authors = tuple((a.get("name") or "").strip() for a in (record.get("authors") or []))
    publisher = (
        ", ".join((p.get("name") or "").strip() for p in (record.get("publishers") or [])) or None
    )
    published_year = _year_from_date(record.get("publish_date") or "")
    page_count = _first_int([record.get("number_of_pages")])
    language = _ol_language_name(record.get("languages") or [])
    return Candidate(
        provider="openlibrary",
        provider_key=bibkey,
        title=title,
        subtitle=None,
        authors=authors,
        publisher=publisher,
        published_year=published_year,
        page_count=page_count,
        isbn=normalized,
        language=language,
        covers=(),
    )
