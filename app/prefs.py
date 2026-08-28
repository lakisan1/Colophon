# Reading the owner's effective preferences: their own settings row, falling back
# to the global row, falling back to defaults. M5 owns the settings *page* and
# every write; the shelf only reads, because it cannot render without a zoom
# preset (A2) and a theme.
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.shelf import DEFAULT_ZOOM

# Column order shared by the two lookups below.
_COLUMNS = "currency_symbol, date_format, time_format, theme, zoom_preset"

DEFAULTS = {
    "currency_symbol": "€",
    "date_format": "dd.mm.yyyy",
    "time_format": "24h",
    "theme": "light",
    "zoom_preset": DEFAULT_ZOOM,
}


@dataclass(frozen=True)
class Prefs:
    """Effective preferences for one request."""

    currency_symbol: str
    date_format: str
    time_format: str
    theme: str
    zoom_preset: str


def load_prefs(session: Session, owner_id: int) -> Prefs:
    """User row wins, then the global row (owner_id IS NULL), then DEFAULTS."""
    values = dict(DEFAULTS)
    values.update(_fetch(session, "WHERE owner_id IS NULL"))
    values.update(_fetch(session, "WHERE owner_id = :owner_id", {"owner_id": owner_id}))
    # A NULL column means "not overridden", not "render nothing".
    values = {
        key: (value if value not in (None, "") else DEFAULTS[key]) for key, value in values.items()
    }
    return Prefs(**values)


def _fetch(session: Session, clause: str, params: dict | None = None) -> dict:
    row = session.execute(text(f"SELECT {_COLUMNS} FROM settings {clause}"), params or {}).first()
    if row is None:
        return {}
    return dict(row._mapping)


def persist_zoom(session: Session, owner_id: int, zoom: str) -> None:
    """Remember the shelf zoom on the user's own settings row (A2).

    Written as update-then-insert because a user without a row is the normal
    case until the settings page (M5) creates one. Other columns keep whatever
    the global row says, which `load_prefs` resolves at read time.
    """
    updated = session.execute(
        text("UPDATE settings SET zoom_preset = :zoom WHERE owner_id = :owner_id"),
        {"zoom": zoom, "owner_id": owner_id},
    )
    if updated.rowcount == 0:
        session.execute(
            text("INSERT INTO settings (owner_id, zoom_preset) VALUES (:owner_id, :zoom)"),
            {"owner_id": owner_id, "zoom": zoom},
        )
    session.commit()
