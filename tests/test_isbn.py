# Unit tests for app/isbn.py. The ISBN-10/ISBN-13 pairs below are real book
# ISBNs (their checksums were independently re-computed before being pinned),
# so a broken algorithm fails here instead of corrupting stored data.
from __future__ import annotations

import pytest

from app.isbn import is_valid_isbn, is_valid_isbn10, is_valid_isbn13, normalize_isbn

# (ISBN-10, ISBN-13) — real, correct pairs.
VALID_PAIRS = [
    ("0306406152", "9780306406157"),  # Computers and Thought (classic example)
    ("080442957X", "9780804429573"),  # The Elements of Style — the X check digit case
    ("0131103628", "9780131103627"),  # The C Programming Language
    ("0201616165", "9780201616163"),  # The Pragmatic Programmer
    ("0198526636", "9780198526636"),  # The Road to Reality
    ("0262033844", "9780262033848"),  # Structure and Interpretation of Computer Programs
]

INVALID = [
    "",
    "   ",
    "hello world",
    "123",
    "03064061",  # too short
    "030640615",  # 9 digits
    "978030640615",  # 12 digits
    "0306406153",  # ISBN-10 checksum wrong
    "030640615X",  # ISBN-10 checksum wrong (X instead of 2)
    "013110362X",  # X where the checksum wants 8
    "9780306406158",  # ISBN-13 checksum wrong
    "978X306406157",  # X in the middle of an ISBN-13
    "9770306406157",  # not a bookland (978/979) prefix
    "97803064061571",  # 14 digits
    "08044-2957A",  # letters instead of a check digit
]


@pytest.mark.parametrize(("isbn10", "isbn13"), VALID_PAIRS)
def test_isbn10_normalises_to_isbn13(isbn10: str, isbn13: str) -> None:
    assert normalize_isbn(isbn10) == isbn13


@pytest.mark.parametrize(("isbn10", "isbn13"), VALID_PAIRS)
def test_isbn13_passes_through_unchanged(isbn10: str, isbn13: str) -> None:
    assert normalize_isbn(isbn13) == isbn13


@pytest.mark.parametrize(("isbn10", "isbn13"), VALID_PAIRS)
def test_hyphens_and_spaces_are_ignored(isbn10: str, isbn13: str) -> None:
    assert (
        normalize_isbn(f"{isbn13[:3]}-{isbn13[3:5]}-{isbn13[5:8]}-{isbn13[8:12]}-{isbn13[12]}")
        == isbn13
    )
    assert normalize_isbn(f"  {isbn10} ") == isbn13


def test_lowercase_x_check_digit_is_accepted() -> None:
    assert normalize_isbn("0-8044-2957-x") == "9780804429573"
    assert is_valid_isbn10("080442957x")


def test_is_valid_isbn_accepts_both_forms_with_noise() -> None:
    assert is_valid_isbn("0306406152")
    assert is_valid_isbn("978-0-306-40615-7")
    assert is_valid_isbn("080442957X")
    assert not is_valid_isbn("9780306406158")


@pytest.mark.parametrize("garbage", INVALID)
def test_invalid_input_is_rejected_without_raising(garbage: str) -> None:
    # The contract: garbage returns None/False deterministically, never an exception.
    assert normalize_isbn(garbage) is None
    assert is_valid_isbn(garbage) is False
    assert is_valid_isbn10(garbage) is False
    assert is_valid_isbn13(garbage) is False


def test_979_prefix_isbn13_is_valid() -> None:
    # 979-prefixed ISBNs have no ISBN-10 form but must still validate. This is
    # the checksum-correct 979 number for body 979109512500 (check digit 6).
    assert is_valid_isbn13("9791095125006") is True
    assert normalize_isbn("9791095125006") == "9791095125006"
