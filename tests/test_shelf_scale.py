# The scale model A1-A5 asserted directly. These are the numbers the shelf is
# judged by in a browser, so they should fail here before they look wrong there.
from __future__ import annotations

import pytest

from app.shelf import (
    CLOTH_HUES,
    LOD_FULL_PX,
    LOD_TITLE_PX,
    MAX_SPINE_PX,
    MIN_SPINE_PX,
    REFERENCE_HEIGHT_MM,
    Spine,
    build_shelf,
    cloth_hue,
    height_px,
    level_of_detail,
    normalise_zoom,
    order_spines,
    scale_for,
    spine_px,
)


def test_scale_is_row_height_over_the_reference_book() -> None:
    """A1: scale = shelf_row_px / 240."""
    assert scale_for("L") == pytest.approx(300.0 / REFERENCE_HEIGHT_MM)
    assert scale_for("S") == pytest.approx(160.0 / REFERENCE_HEIGHT_MM)


def test_proportions_are_exactly_true_at_zoom_L() -> None:
    """A2's promise: at L a 240 mm book is 300 px and a 120 mm book is 150 px."""
    assert height_px(240, "L") == pytest.approx(300.0)
    assert height_px(120, "L") == pytest.approx(150.0)
    assert height_px(120, "L") == pytest.approx(height_px(240, "L") / 2)


def test_height_is_strictly_linear_at_every_zoom() -> None:
    """A1: height never gets clamped, at any preset, for any sane book."""
    for zoom in ("S", "M", "L"):
        # Rendered pixels are rounded to 2 dp, so linearity is asserted with a
        # tolerance, not as exact equality on rounded numbers.
        assert height_px(100, zoom) == pytest.approx(height_px(200, zoom) / 2, abs=0.01)
        assert height_px(310, zoom) > height_px(90, zoom)


@pytest.mark.parametrize("zoom", ["S", "M", "L"])
def test_thin_spines_are_clamped_to_the_clickable_floor(zoom: str) -> None:
    """A3: an 8 mm thin book at zoom S would be 5 px without the clamp."""
    assert spine_px(8, zoom) >= MIN_SPINE_PX[zoom]
    assert spine_px(1, zoom) == MIN_SPINE_PX[zoom]


@pytest.mark.parametrize("zoom", ["S", "M", "L"])
def test_thick_spines_are_capped(zoom: str) -> None:
    assert spine_px(400, zoom) == MAX_SPINE_PX


def test_spine_width_is_monotonic_in_thickness_above_the_floor() -> None:
    """A fatter book is never thinner on the shelf."""
    widths = [spine_px(t, "L") for t in (25, 40, 60, 80)]
    assert widths == sorted(widths)


def test_lod_tiers_match_the_decided_thresholds() -> None:
    """A4: >=26 full, 14-25 title only, <14 sliver."""
    assert level_of_detail(LOD_FULL_PX) == "full"
    assert level_of_detail(LOD_FULL_PX - 0.01) == "title"
    assert level_of_detail(LOD_TITLE_PX) == "title"
    assert level_of_detail(LOD_TITLE_PX - 0.01) == "sliver"


def test_every_zoom_produces_more_than_one_lod_tier() -> None:
    """If a preset collapsed every book into one tier, LOD would be decoration."""
    for zoom in ("S", "M", "L"):
        tiers = {level_of_detail(spine_px(t, zoom)) for t in (8, 25, 60, 120)}
        assert len(tiers) >= 2


def test_cloth_colour_is_stable_and_on_palette() -> None:
    """A5: same author, same colour — across restarts, so not Python's hash()."""
    assert cloth_hue("Herbert, Frank") == cloth_hue("Herbert, Frank")
    assert cloth_hue("Herbert, Frank") in CLOTH_HUES


def test_cloth_colour_varies_across_authors() -> None:
    hues = {cloth_hue(name) for name in ("Bankes", "Abnett", "Herbert", "Abercrombie", "Butcher")}
    assert len(hues) >= 2


def test_zoom_falls_back_instead_of_raising() -> None:
    assert normalise_zoom(None) == "M"
    assert normalise_zoom("l") == "L"
    assert normalise_zoom("XL") == "M"
    assert normalise_zoom("  ") == "M"


def _spine(**overrides) -> Spine:
    base = {
        "book_id": 1,
        "title": "Book",
        "author_label": "Author",
        "sort_key": "author",
        "series_name": None,
        "series_position": None,
        "primary_genre_notation": "FIC000000",
        "genre_label": "Fiction",
        "height_mm": 200,
        "thickness_mm": 25,
        "has_provider_cover": False,
        "height_px": 200.0,
        "width_px": 25.0,
        "lod": "full",
        "cloth_hue": 38,
    }
    return Spine(**{**base, **overrides})


def test_series_ordering_keeps_decimals_between_integers() -> None:
    """D9 through the shelf's own ordering (D6)."""
    ordered_titles = [
        ("Ten", 10.0),
        ("One", 1.0),
        ("Prequel", 0.5),
        ("One point five", 1.5),
        ("Two", 2.0),
    ]
    spines = [
        _spine(book_id=i, title=title, series_name="HH", series_position=position)
        for i, (title, position) in enumerate(ordered_titles)
    ]
    assert [s.title for s in order_spines(spines)] == [
        "Prequel",
        "One",
        "One point five",
        "Two",
        "Ten",
    ]


def test_unnumbered_books_stand_after_numbered_ones() -> None:
    """D6: nulls last, so a series reads as a sequence with strays at the end."""
    spines = [
        _spine(book_id=1, title="Stray", series_name="HH", series_position=None),
        _spine(book_id=2, title="Volume one", series_name="HH", series_position=1.0),
    ]
    assert [s.title for s in order_spines(spines)] == ["Volume one", "Stray"]


def test_within_a_position_books_sort_by_author_then_title() -> None:
    spines = [
        _spine(book_id=1, title="Zeta", author_label="Zzz", sort_key="zzz"),
        _spine(book_id=2, title="Alpha", author_label="Aaa", sort_key="aaa"),
        _spine(book_id=3, title="Beta", author_label="Aaa", sort_key="aaa"),
    ]
    assert [s.title for s in order_spines(spines)] == ["Alpha", "Beta", "Zeta"]


def _row(**overrides) -> dict:
    base = {
        "book_id": 1,
        "title": "Dune",
        "series_position": None,
        "height_mm": 240,
        "thickness_mm": 40,
        "series_name": None,
        "genre_notation": "FIC000000",
        "genre_label": "Fiction",
        "genre_sort_order": 1,
        "author_label": "Frank Herbert",
        "author_sort_key": "herbert, frank",
        "has_provider_cover": 0,
    }
    return {**base, **overrides}


def test_build_shelf_groups_by_genre_in_curated_order() -> None:
    sections = build_shelf(
        [
            _row(book_id=1, genre_notation="FIC000000", genre_label="Fiction", genre_sort_order=9),
            _row(book_id=2, genre_notation="HIS000000", genre_label="History", genre_sort_order=2),
            _row(book_id=3, genre_notation="FIC000000", genre_label="Fiction", genre_sort_order=9),
        ],
        "M",
    )
    assert [s.notation for s in sections] == ["HIS000000", "FIC000000"]
    assert [len(s.spines) for s in sections] == [1, 2]


def test_build_shelf_gives_every_spine_its_geometry() -> None:
    sections = build_shelf([_row(height_mm=240, thickness_mm=40)], "L")
    spine = sections[0].spines[0]
    assert spine.height_px == pytest.approx(300.0)
    assert spine.width_px == pytest.approx(40 * 1.25)
    assert spine.lod == "full"


def test_badge_trims_whole_numbers_but_keeps_decimals() -> None:
    assert _spine(series_position=2.0).badge == "2"
    assert _spine(series_position=1.5).badge == "1.5"
    assert _spine(series_position=None).badge == ""


def test_tooltip_carries_what_a_narrow_spine_cannot_show() -> None:
    spine = _spine(
        title="Old Man's War",
        author_label="John Scalzi",
        series_name="Old Man's War",
        series_position=1.0,
    )
    assert spine.label == "Old Man's War — John Scalzi · Old Man's War 1"
