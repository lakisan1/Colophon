# ISBN normalisation and validation. Books store one canonical form — ISBN-13 —
# so a book found via its 10-digit ISBN matches the same book entered as 13
# digits (SPEC §2). Garbage input never raises: every function returns None or
# False instead, because ISBNs arrive from user typing and provider payloads.
from __future__ import annotations

# EAN prefixes that identify bookland. A 13-digit code outside these is a retail
# barcode, not an ISBN, and is rejected on purpose.
_BOOKLAND_PREFIXES = ("978", "979")


def _clean(raw: str) -> str:
    """Strip the noise people actually type: hyphens, spaces, stray case."""
    return raw.strip().upper().replace("-", "").replace(" ", "")


def is_valid_isbn10(raw: str) -> bool:
    """True when raw is a well-formed ISBN-10: 9 digits + check digit (0-9 or X)."""
    value = _clean(raw)
    if len(value) != 10:
        return False
    digits, check = value[:9], value[9]
    if not digits.isdigit():
        return False
    if check not in "0123456789X":
        return False
    check_value = 10 if check == "X" else int(check)
    total = sum(int(d) * (10 - i) for i, d in enumerate(digits)) + check_value
    return total % 11 == 0


def is_valid_isbn13(raw: str) -> bool:
    """True when raw is a 13-digit ISBN: bookland prefix and a correct mod-10 check digit."""
    value = _clean(raw)
    if len(value) != 13 or not value.isdigit():
        return False
    if not value.startswith(_BOOKLAND_PREFIXES):
        return False
    total = sum(int(d) * (1 if i % 2 == 0 else 3) for i, d in enumerate(value[:12]))
    return (10 - total % 10) % 10 == int(value[12])


def is_valid_isbn(raw: str) -> bool:
    """True when raw validates as either ISBN-10 or ISBN-13 (hyphens allowed)."""
    value = _clean(raw)
    if len(value) == 10:
        return is_valid_isbn10(value)
    if len(value) == 13:
        return is_valid_isbn13(value)
    return False


def normalize_isbn(raw: str) -> str | None:
    """Canonical form for storage: digits-only ISBN-13, or None for anything invalid.

    ISBN-10 input (with or without hyphens, any check digit case) is converted by
    swapping the 978 prefix in and recomputing the ISBN-13 check digit. Valid
    ISBN-13 input is returned unchanged apart from cleaning.
    """
    value = _clean(raw)
    if is_valid_isbn13(value):
        return value
    if is_valid_isbn10(value):
        body = "978" + value[:9]
        total = sum(int(d) * (1 if i % 2 == 0 else 3) for i, d in enumerate(body))
        return body + str((10 - total % 10) % 10)
    return None
