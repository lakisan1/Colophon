# The shelf's physical model (A1-A5): millimetres in, pixels out, plus the
# ordering that decides where a book stands. Kept free of HTTP and of the
# database so the geometry can be asserted directly — a wrong scale is exactly
# the kind of bug a browser review catches too late.
#
# Height is strictly linear in mm (A1). Spine width is clamped (A3): an
# unclamped 8 mm thin paperback at zoom S would render 5 px wide and be
# unclickable, so a deliberate, zoom-dependent deviation from strict
# proportionality is bought in exchange for usability.
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

# A1: 240 mm is the reference book height; a shelf row is this many pixels tall.
REFERENCE_HEIGHT_MM = 240.0

# A1: a book taller than the preset defines the whole row's height. Leave a
# little air above such a book so its top does not sit flush on the back panel.
TALL_BOOK_MARGIN_PX = 10.0

# A2: zoom presets are shelf-row heights in pixels. Proportions are exactly true
# at L; S and M gain their width from the clamp instead.
# A2: zoom presets are shelf-row heights in pixels. Proportions are exactly
# true at L. S and M step up to the old M and L presets; L is larger still.
ZOOM_ROW_PX: dict[str, float] = {"S": 220.0, "M": 300.0, "L": 400.0}
DEFAULT_ZOOM = "M"

# A3: floor per zoom (so thin spines stay clickable) and a ceiling that scales
# with zoom (so a boxed set cannot eat a whole row, yet still grows when you
# zoom in). MAX_SPINE_MM is the widest a spine may be, in millimetres; it is
# scaled to pixels by max_spine_px().
MIN_SPINE_PX: dict[str, float] = {"S": 12.0, "M": 14.0, "L": 16.0}
MAX_SPINE_MM = 72.0
# A3: the thinnest a spine is ever treated as. Keeps a short novella from
# becoming a hairline sliver that cannot carry its series badge.
MIN_THICKNESS_MM = 5.0

# A4: level of detail thresholds, measured in spine pixels.
LOD_FULL_PX = 26.0
LOD_TITLE_PX = 14.0

# A5: hues of the cloth tokens in theme.css. A spine takes one of these by hash
# instead of an arbitrary hue, which is what keeps generated colours on-palette
# in both themes.
CLOTH_HUES: tuple[int, ...] = (0, 38, 130, 200, 220, 285)


@dataclass(frozen=True)
class Spine:
    """One book as the shelf draws it: real size plus the pixels it earns."""

    book_id: int
    title: str
    author_label: str
    sort_key: str
    series_name: str | None
    series_position: float | None
    primary_genre_notation: str | None
    genre_label: str
    height_mm: int
    thickness_mm: int
    has_provider_cover: bool
    height_px: float
    width_px: float
    lod: str
    cloth_hue: int

    @property
    def badge(self) -> str:
        """Series number for the spine badge (D9): '2' not '2.0', '1.5' stays."""
        return _trim_number(self.series_position)

    @property
    def label(self) -> str:
        """Tooltip text: what the spine cannot show at this zoom."""
        position = ""
        if self.series_name is not None:
            position = f" · {self.series_name} {_trim_number(self.series_position)}"
        return f"{self.title} — {self.author_label}{position}"


@dataclass
class ShelfSection:
    """One genre's wooden row (D5: one primary genre, so one row per genre)."""

    notation: str
    label: str
    sort_order: int = 9999
    row_px: float = 0.0
    spines: list[Spine] = field(default_factory=list)


def normalise_zoom(value: str | None) -> str:
    """Unknown or missing zoom falls back to the default preset."""
    if value is None:
        return DEFAULT_ZOOM
    candidate = value.strip().upper()
    return candidate if candidate in ZOOM_ROW_PX else DEFAULT_ZOOM


def scale_for(zoom: str) -> float:
    """Pixels per millimetre at this zoom (A1)."""
    return ZOOM_ROW_PX[normalise_zoom(zoom)] / REFERENCE_HEIGHT_MM


def max_spine_px(zoom: str) -> float:
    """The widest a spine may be at this zoom, in pixels.

    It scales with the zoom (like every other book) so a very thick boxed set
    whose width is clamped still changes size when the shelf is zoomed in — the
    old fixed 90 px ceiling left such a spine frozen at 90 px at every preset.
    """
    return round(MAX_SPINE_MM * scale_for(zoom), 2)


def height_px(height_mm: int | None, zoom: str) -> float:
    """Shelf height for a book. A missing measurement renders at the reference."""
    if not height_mm or height_mm <= 0:
        height_mm = int(REFERENCE_HEIGHT_MM)
    return round(height_mm * scale_for(zoom), 2)


def spine_px(thickness_mm: int | None, zoom: str) -> float:
    """Clamped spine width in pixels (A3)."""
    if not thickness_mm or thickness_mm <= 0:
        thickness_mm = 20
    elif thickness_mm < MIN_THICKNESS_MM:
        # A3: no spine is thinner than 5 mm, so a novella keeps a visible spine
        # and the geometry that goes with it.
        thickness_mm = MIN_THICKNESS_MM
    raw = thickness_mm * scale_for(zoom)
    return round(min(max(raw, MIN_SPINE_PX[normalise_zoom(zoom)]), max_spine_px(zoom)), 2)


def level_of_detail(width_px: float) -> str:
    """full / title / sliver, from the rendered spine width (A4)."""
    if width_px >= LOD_FULL_PX:
        return "full"
    if width_px >= LOD_TITLE_PX:
        return "title"
    return "sliver"


def cloth_hue(key: str) -> int:
    """Stable colour for a spine (A5): same author, same colour, every reload.

    sha256 rather than Python's hash() because the latter is salted per process,
    which would repaint the library on every server restart.
    """
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    return CLOTH_HUES[int.from_bytes(digest[:4], "big") % len(CLOTH_HUES)]


def order_spines(spines: list[Spine]) -> list[Spine]:
    """D6: series position with unnumbered books last, then author, then title."""
    return sorted(spines, key=_order_key)


def _order_key(spine: Spine) -> tuple[int, float, str, str]:
    return (
        0 if spine.series_position is not None else 1,
        spine.series_position if spine.series_position is not None else 0.0,
        spine.sort_key.lower(),
        spine.title.lower(),
    )


def build_shelf(rows: list[dict[str, Any]], zoom: str) -> list[ShelfSection]:
    """Turn query rows into ordered genre sections.

    Rows come from `shelf_store.SHELF_SELECT`: one per book, already carrying
    the display author, its sort key and the genre label.
    """
    sections: dict[str, ShelfSection] = {}
    for row in rows:
        width = spine_px(row["thickness_mm"], zoom)
        spine = Spine(
            book_id=int(row["book_id"]),
            title=row["title"],
            author_label=row["author_label"],
            sort_key=row["author_sort_key"],
            series_name=row["series_name"],
            series_position=row["series_position"],
            primary_genre_notation=row["genre_notation"],
            genre_label=row["genre_label"],
            height_mm=row["height_mm"],
            thickness_mm=row["thickness_mm"],
            has_provider_cover=bool(row["has_provider_cover"]),
            height_px=height_px(row["height_mm"], zoom),
            width_px=width,
            lod=level_of_detail(width),
            cloth_hue=(
                row["cloth_hue"]
                if row.get("cloth_hue") is not None
                else cloth_hue(row["author_sort_key"])
            ),
        )
        key = spine.primary_genre_notation or "??"
        sections.setdefault(
            key, ShelfSection(key, section_label(spine.genre_label), int(row["genre_sort_order"]))
        ).spines.append(spine)

    # Shelves are shown alphabetically from A in the first row (case-insensitive
    # on the shelf label), not in curated genre order.
    ordered = sorted(sections.values(), key=lambda s: s.label.casefold())
    floor = ZOOM_ROW_PX[normalise_zoom(zoom)]
    for section in ordered:
        section.spines = order_spines(section.spines)
        # A 300 mm folio at zoom L is 375 px tall (A1 is strict), so the row has
        # to grow past the preset or the tallest book on it gets clipped. A book
        # that defines the whole shelf height also earns a little air above it,
        # so its top does not sit flush on the back-panel edge.
        tallest = max((sp.height_px for sp in section.spines), default=0.0)
        section.row_px = floor if tallest <= floor else tallest + TALL_BOOK_MARGIN_PX
    return ordered


def section_label(bisac_label: str) -> str:
    """Short label for a wooden row: the leaf of the BISAC path.

    "Fiction / Fantasy / General" as a shelf label is unreadable, and the row
    already sits under a Fiction neighbourhood, so the leaf is what a person
    reads. A leaf of "General" collapses to its parent ("Fantasy").
    """
    parts = [part.strip() for part in bisac_label.split("/") if part.strip()]
    if not parts:
        return "Uncategorised"
    if parts[-1].lower() == "general" and len(parts) > 1:
        return parts[-2]
    return parts[-1]


def _trim_number(value: float | None) -> str:
    if value is None:
        return ""
    return str(int(value)) if float(value).is_integer() else str(value)
