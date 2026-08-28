# Price display (SPEC §10). The spec only says "plain number, symbol from Settings,
# no conversion", so the exact rendering is a decision — pinned here so changing it
# is one edit and one test run rather than a hunt through templates.
from __future__ import annotations

from decimal import Decimal

from app.money import format_price


def test_symbol_before_the_number() -> None:
    assert format_price(12, "€") == "€12.00"


def test_the_settings_symbol_is_used_verbatim() -> None:
    assert format_price(5, "$") == "$5.00"
    assert format_price(5, "RSD ") == "RSD 5.00"


def test_thousands_are_grouped() -> None:
    assert format_price(1234.5, "€") == "€1,234.50"
    assert format_price(1234567, "€") == "€1,234,567.00"


def test_cents_survive_a_decimal_from_the_database() -> None:
    # Two decimals must not drift on the way out. Sub-cent values round half-even in
    # str.format, and that is written down rather than assumed: a price typed as
    # 95.005 is a data-entry slip, and rendering €95.00 is the least surprising answer.
    assert format_price(Decimal("19.99"), "€") == "€19.99"
    assert format_price(Decimal("95.005"), "€") == "€95.00"


def test_no_price_is_not_rendered_as_zero() -> None:
    """An unrecorded price and a free book are different facts."""
    assert format_price(None, "€") is None
    assert format_price("", "€") is None
    assert format_price(0, "€") == "€0.00"


def test_unreadable_data_shows_nothing_rather_than_crashing() -> None:
    assert format_price("three", "€") is None
    assert format_price(object(), "€") is None
