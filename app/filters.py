# Filters (M6, SPEC §9). Everything here is pure: parse a query string into a
# Filters object, turn that object into WHERE fragments. No database, no request.
#
# Two rules shape the parsing:
#   * an unrecognised value is ignored, never an error. A stale link or a typo in
#     a bookmark should show a slightly wider list, not a 422 page;
#   * the page's *scope* (which shelf) is not a filter the user can widen. The
#     `ownership` parameter can only narrow inside the scope, so /wishlist?
#     ownership=owned cannot leak the library onto the wishlist.
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

OWNERSHIP_STATUSES: tuple[str, ...] = ("owned", "wanted", "borrowed", "sold", "gifted")
READING_STATES: tuple[str, ...] = ("unread", "reading", "finished", "dnf")

# Which statuses each page is allowed to show. SPEC §3: shelf 1 is what you have,
# shelf 2 what is on loan, shelf 3 what is gone (sold and gifted together, with a
# filter distinguishing them), shelf 4 the wishlist.
SCOPES: dict[str, tuple[str, ...]] = {
    "library": ("owned",),
    "borrowed": ("borrowed",),
    "gone": ("sold", "gifted"),
    "wishlist": ("wanted",),
    # SPEC §3: search covers shelves 2 and 3 only through an explicit toggle, so
    # the default search scope is the library plus the wishlist.
    "search": ("owned", "wanted"),
    "search_all": OWNERSHIP_STATUSES,
    "export": OWNERSHIP_STATUSES,
}

MAX_QUERY_LENGTH = 120
MAX_GENRE_LENGTH = 16
YEAR_MIN, YEAR_MAX = 1, 2100


@dataclass(frozen=True)
class Filters:
    """A parsed, validated set of filters for one page."""

    statuses: tuple[str, ...] = ("owned",)
    q: str | None = None
    author_id: int | None = None
    genre: str | None = None
    year: int | None = None
    reading: str | None = None
    room_id: int | None = None
    no_room: bool = False
    #: 'sold' or 'gifted' on the Gone shelf; ignored everywhere else.
    gone_kind: str | None = None
    #: The scope this object was parsed under, so links can be rebuilt verbatim.
    scope: str = field(default="library", compare=False)

    def is_active(self) -> bool:
        """True if anything narrows the page beyond its scope."""
        return any(
            (
                self.q,
                self.author_id,
                self.genre,
                self.year,
                self.reading,
                self.room_id,
                self.no_room,
                self.gone_kind,
            )
        )

    def clauses(self) -> tuple[list[str], dict[str, Any]]:
        """WHERE fragments (joined with AND by the caller) and their bound values.

        Values are always bound, never interpolated — the strings reaching here come
        straight from a query string.
        """
        fragments: list[str] = []
        params: dict[str, Any] = {}

        if self.statuses:
            keys = []
            for index, status in enumerate(self.statuses):
                key = f"status{index}"
                keys.append(f":{key}")
                params[key] = status
            fragments.append(f"b.ownership_status IN ({', '.join(keys)})")

        if self.q:
            # The FTS5 index is maintained by triggers (migration 0003). Matching
            # through a subquery keeps the main SELECT free of an extra join.
            from app.search import build_match

            expression = build_match(self.q)
            if expression is None:
                # The query was all punctuation: say "nothing" rather than "everything".
                fragments.append("0")
            else:
                fragments.append(
                    "b.id IN (SELECT rowid FROM books_fts WHERE books_fts MATCH :fts_match)"
                )
                params["fts_match"] = expression

        if self.author_id is not None:
            # Any listed author, not only the first — SPEC §9 wants an author's
            # books across all genre sections.
            fragments.append(
                "b.id IN (SELECT book_id FROM book_authors WHERE author_id = :author_id)"
            )
            params["author_id"] = self.author_id

        if self.genre:
            # D5 gives a book one primary genre; book_genres holds the extra,
            # filter-only ones. Filtering covers both.
            fragments.append(
                "(b.primary_genre_notation = :genre"
                " OR b.id IN (SELECT book_id FROM book_genres WHERE genre_notation = :genre))"
            )
            params["genre"] = self.genre

        if self.year is not None:
            fragments.append("b.publication_year = :year")
            params["year"] = self.year

        if self.reading:
            fragments.append("b.reading_state = :reading")
            params["reading"] = self.reading

        if self.room_id is not None:
            fragments.append("b.room_id = :room_id")
            params["room_id"] = self.room_id
        elif self.no_room:
            fragments.append("b.room_id IS NULL")

        if self.gone_kind:
            fragments.append("b.ownership_status = :gone_kind")
            params["gone_kind"] = self.gone_kind

        return fragments, params

    def as_query(self, **overrides: str | None) -> str:
        """Rebuild a query string so filtered links stay copyable (SPEC §1)."""
        values: dict[str, str] = {}
        if self.q:
            values["q"] = self.q
        if self.author_id is not None:
            values["author"] = str(self.author_id)
        if self.genre:
            values["genre"] = self.genre
        if self.year is not None:
            values["year"] = str(self.year)
        if self.reading:
            values["reading"] = self.reading
        if self.room_id is not None:
            values["room"] = str(self.room_id)
        elif self.no_room:
            values["room"] = "none"
        if self.gone_kind:
            values["kind"] = self.gone_kind
        values.update({key: value for key, value in overrides.items() if value})
        from urllib.parse import urlencode

        return urlencode(values)


def _clean_str(raw: object, *, limit: int) -> str | None:
    if not isinstance(raw, str):
        return None
    value = " ".join(raw.split())[:limit]
    return value or None


def _positive_int(raw: object) -> int | None:
    if isinstance(raw, bool) or raw is None:
        return None
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def parse_filters(params: Mapping[str, Any], *, scope: str = "library") -> Filters:
    """Parse request query parameters under a page scope."""
    allowed = SCOPES.get(scope, SCOPES["library"])

    statuses: tuple[str, ...] = allowed
    requested = params.get("ownership")
    if isinstance(requested, str) and requested in allowed:
        statuses = (requested,)

    gone_kind: str | None = None
    kind = params.get("kind")
    if isinstance(kind, str) and kind in ("sold", "gifted") and set(allowed) >= {"sold", "gifted"}:
        gone_kind = kind

    room_raw = params.get("room")
    no_room = isinstance(room_raw, str) and room_raw.strip().lower() == "none"
    room_id = None if no_room else _positive_int(room_raw)

    year = _positive_int(params.get("year"))
    if year is not None and not YEAR_MIN <= year <= YEAR_MAX:
        year = None

    reading = params.get("reading")
    if not (isinstance(reading, str) and reading in READING_STATES):
        reading = None

    q = _clean_str(params.get("q"), limit=MAX_QUERY_LENGTH)
    if q is not None and len(q) < 2:
        # One character would match half the library and is never deliberate.
        q = None

    return Filters(
        statuses=statuses,
        q=q,
        author_id=_positive_int(params.get("author")),
        genre=_clean_str(params.get("genre"), limit=MAX_GENRE_LENGTH),
        year=year,
        reading=reading,
        room_id=room_id,
        no_room=no_room,
        gone_kind=gone_kind,
        scope=scope,
    )
