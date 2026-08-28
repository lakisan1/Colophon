# Turning a human's search box into a safe FTS5 MATCH expression.
#
# FTS5 query syntax is a small language, and users type `"`, `-`, `*`, `AND` and
# parentheses into a search box expecting them to be literal. So every token is
# quoted and the tokens are ANDed — a cross-section search that behaves like the
# rest of the app. The last token also gets a prefix match, so "dun" finds "Dune"
# while typing, which is the only reason anyone reaches for the asterisk.
from __future__ import annotations

import re

# Words and runs of digits, Unicode-aware: "Lord of the Rings, The" splits on the
# comma and space, "1984" stays one token.
_TOKEN = re.compile(r"[^\W\d_]+|\d+", re.UNICODE)

MAX_TOKENS = 8


def build_match(query: str, *, max_tokens: int = MAX_TOKENS) -> str | None:
    """A quoted, ANDed, prefix-terminated MATCH expression, or None if unusable."""
    tokens = _TOKEN.findall(query or "")[: max(1, max_tokens)]
    if not tokens:
        return None
    quoted = [_quote(token) for token in tokens[:-1]]
    quoted.append(f"{_quote(tokens[-1])}*")
    return " AND ".join(quoted)


def _quote(token: str) -> str:
    # Inside an FTS5 phrase a double quote is escaped by doubling it.
    return '"' + token.replace('"', '""') + '"'
