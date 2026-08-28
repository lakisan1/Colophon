# The filter truth table (SPEC §9). Parsing is pure and the SQL fragments come out
# of the same object, so the whole matrix can be pinned without a database: every
# parameter, its accepted forms, and everything that must be ignored instead of
# raising.
from __future__ import annotations

import pytest

from app.filters import MAX_QUERY_LENGTH, Filters, parse_filters


def params(**values: object) -> dict[str, object]:
    return dict(values)


# ------------------------------------------------------------------ scope


@pytest.mark.parametrize(
    ("scope", "statuses"),
    [
        ("library", ("owned",)),
        ("borrowed", ("borrowed",)),
        ("gone", ("sold", "gifted")),
        ("wishlist", ("wanted",)),
        ("search", ("owned", "wanted")),
        ("search_all", ("owned", "wanted", "borrowed", "sold", "gifted")),
    ],
)
def test_each_scope_locks_its_status_set(scope: str, statuses: tuple[str, ...]) -> None:
    assert parse_filters(params(), scope=scope).statuses == statuses


def test_an_unknown_scope_falls_back_to_the_library() -> None:
    assert parse_filters(params(), scope="nonsense").statuses == ("owned",)


def test_the_ownership_parameter_cannot_widen_a_scope() -> None:
    """A wishlist page must not leak the library onto it."""
    parsed = parse_filters(params(ownership="owned"), scope="wishlist")
    assert parsed.statuses == ("wanted",)


def test_the_ownership_parameter_narrows_a_gone_shelf() -> None:
    parsed = parse_filters(params(ownership="sold"), scope="gone")
    assert parsed.statuses == ("sold",)


def test_the_kind_parameter_only_works_where_sold_and_gifted_share_a_page() -> None:
    assert parse_filters(params(kind="sold"), scope="gone").gone_kind == "sold"
    assert parse_filters(params(kind="gifted"), scope="gone").gone_kind == "gifted"
    assert parse_filters(params(kind="sold"), scope="library").gone_kind is None
    assert parse_filters(params(kind="wanted"), scope="gone").gone_kind is None


# ------------------------------------------------------------------ year


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2024", 2024),
        ("1532", 1532),
        (" 2024 ", 2024),
        ("", None),
        ("abc", None),
        ("2024-01", None),
        ("0", None),
        ("-5", None),
        ("99999", None),
        ("2101", None),
        ("True", None),
    ],
)
def test_year_parsing(raw: str, expected: int | None) -> None:
    assert parse_filters(params(year=raw)).year == expected


# ------------------------------------------------------------------ room


def test_room_accepts_an_id_or_the_word_none() -> None:
    assert parse_filters(params(room="3")).room_id == 3
    no_room = parse_filters(params(room="none"))
    assert no_room.no_room is True
    assert no_room.room_id is None


@pytest.mark.parametrize("raw", ["0", "-1", "abc", "", "3.5"])
def test_room_rejects_junk(raw: str) -> None:
    parsed = parse_filters(params(room=raw))
    assert parsed.room_id is None and parsed.no_room is False


def test_room_none_takes_precedence_and_never_both() -> None:
    parsed = parse_filters(params(room="NONE"))
    assert parsed.no_room is True and parsed.room_id is None


# ------------------------------------------------------------------ ids, genre, reading


@pytest.mark.parametrize(
    ("raw", "expected"), [("7", 7), ("", None), ("x", None), ("-2", None), ("0", None)]
)
def test_author_id_parsing(raw: str, expected: int | None) -> None:
    assert parse_filters(params(author=raw)).author_id == expected


def test_reading_state_must_be_one_of_four() -> None:
    assert parse_filters(params(reading="finished")).reading == "finished"
    assert parse_filters(params(reading="abandoned")).reading is None


def test_genre_is_trimmed_and_bounded() -> None:
    assert parse_filters(params(genre="  FIC019000  ")).genre == "FIC019000"
    assert parse_filters(params(genre="")).genre is None
    assert len(parse_filters(params(genre="Z" * 90)).genre) == 16


# ------------------------------------------------------------------ free text


def test_query_whitespace_is_collapsed() -> None:
    assert parse_filters(params(q="  the   left\n  hand ")).q == "the left hand"


def test_a_single_character_query_is_dropped() -> None:
    assert parse_filters(params(q="d")).q is None


def test_query_length_is_capped() -> None:
    assert len(parse_filters(params(q="q" * 400)).q) == MAX_QUERY_LENGTH


def test_a_non_string_query_is_ignored() -> None:
    assert parse_filters(params(q=["a", "b"])).q is None


# ------------------------------------------------------------------ SQL fragments


def test_fragments_are_bound_never_interpolated() -> None:
    fragments, bound = Filters(
        q="dune", author_id=7, genre="FIC019000", year=1965, reading="unread"
    ).clauses()
    joined = " AND ".join(fragments)

    assert "dune" not in joined and "FIC019000" not in joined
    assert bound["year"] == 1965 and bound["reading"] == "unread"
    assert ":author_id" in joined and bound["author_id"] == 7


def test_status_placeholders_match_the_status_count() -> None:
    fragments, bound = Filters(statuses=("sold", "gifted")).clauses()
    assert fragments[0].count(":status") == 2
    assert {bound["status0"], bound["status1"]} == {"sold", "gifted"}


def test_search_uses_the_fts_index() -> None:
    fragments, bound = Filters(q="left hand").clauses()
    assert any("books_fts" in fragment for fragment in fragments)
    assert bound["fts_match"] == '"left" AND "hand" AND "hand"*'.replace(
        '"hand" AND "hand"*', '"hand"*'
    )


def test_a_punctuation_only_query_matches_nothing_rather_than_everything() -> None:
    fragments, bound = Filters(q="!!!").clauses()
    assert "0" in fragments
    assert "fts_match" not in bound


def test_room_none_and_room_id_are_mutually_exclusive() -> None:
    fragments, bound = Filters(no_room=True).clauses()
    assert "b.room_id IS NULL" in " AND ".join(fragments)
    assert "room_id" not in bound


def test_the_author_filter_uses_the_link_table_not_the_first_author() -> None:
    """SPEC §9 wants an author's books across all sections, including co-authored
    books where they are listed second."""
    joined = " AND ".join(Filters(author_id=3).clauses()[0])
    assert "SELECT book_id FROM book_authors WHERE author_id = :author_id" in joined


def test_the_genre_filter_covers_primary_and_extra_genres() -> None:
    joined = " AND ".join(Filters(genre="FIC019000").clauses()[0])
    assert "b.primary_genre_notation = :genre" in joined
    assert "book_genres" in joined


def test_gone_kind_adds_its_own_clause() -> None:
    fragments, bound = Filters(statuses=("sold", "gifted"), gone_kind="gifted").clauses()
    assert "b.ownership_status = :gone_kind" in fragments
    assert bound["gone_kind"] == "gifted"


# ------------------------------------------------------------------ links


def test_as_query_round_trips_through_parse() -> None:
    original = Filters(q="dune messiah", author_id=4, year=1969, reading="reading", room_id=2)
    from urllib.parse import parse_qsl

    # Decoded properly: urlencode writes '+' for a space, and a test that split on
    # '=' by hand would compare 'dune+messiah' with 'dune messiah' and call it a bug.
    reparsed = parse_filters(dict(parse_qsl(original.as_query())), scope="library")

    assert reparsed.q == "dune messiah"
    assert reparsed.author_id == 4
    assert reparsed.year == 1969
    assert reparsed.reading == "reading"
    assert reparsed.room_id == 2


def test_as_query_carries_no_room_as_none() -> None:
    assert "room=none" in Filters(no_room=True).as_query()


def test_as_query_is_empty_when_nothing_is_set() -> None:
    assert Filters().as_query() == ""


def test_as_query_supports_overrides_for_extra_toggle() -> None:
    query = Filters(q="dune").as_query(all="1")
    assert "all=1" in query and "q=dune" in query


def test_is_active_reports_only_real_narrowing() -> None:
    assert Filters().is_active() is False
    assert Filters(statuses=("sold", "gifted")).is_active() is False
    assert Filters(q="a word").is_active() is True
