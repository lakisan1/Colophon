# Money formatting in one place (SPEC §10).
#
# SPEC §10 says very little: a price is a plain number, the currency symbol comes
# from Settings, and there is no conversion. Everything past that — symbol before
# the number, two decimals, comma thousands grouping — is a display choice, so it
# lives here where it can be vetoed in one edit rather than being repeated in every
# template. Grouping follows the English convention because SPEC §10 fixes the UI
# language to English; a Serbian-style `1.234,56` would be a locale decision, and
# the app has no locale system in v1.
from __future__ import annotations


def format_price(value: object, symbol: str) -> str | None:
    """`€1,234.50`, or None when no price was recorded — an absent price is not 0."""
    if value is None or value == "":
        return None
    try:
        amount = float(value)
    except (TypeError, ValueError):
        # A price column that cannot be read is a data problem, not a reason to
        # 500 a page: show nothing rather than a traceback or a wrong number.
        return None
    return f"{symbol}{amount:,.2f}"
