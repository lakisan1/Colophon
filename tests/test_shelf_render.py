# Rendered-HTML tests for the shelf page. These
# assert structure and geometry as the browser receives it; how it *looks* only
# a human can judge.
from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app import db as db_module
from app.main import create_app
from app.settings import settings
from tests.alembic_runner import engine_for, run_alembic

BOOK_RE = re.compile(
    r'class="book lod-(?P<lod>[a-z]+)[^"]*"[^>]*?'
    r"--book-height:\s*(?P<height>[\d.]+)px;\s*"
    r"--book-width:\s*(?P<width>[\d.]+)px;\s*"
    r"--cloth-hue:\s*(?P<hue>\d+);",
    re.DOTALL,
)


def _books(html: str) -> list[dict]:
    return [match.groupdict() for match in BOOK_RE.finditer(html)]


def test_shelf_page_renders(seeded_client: TestClient) -> None:
    response = seeded_client.get("/")

    assert response.status_code == 200
    assert "My library" in response.text
    assert 'class="shelf-row"' in response.text


def test_every_owned_book_becomes_a_spine(seeded_client: TestClient) -> None:
    """181 owned copies in the fixture; the other four statuses are absent (D2)."""
    html = seeded_client.get("/").text
    books = _books(html)

    assert len(books) == 181
    assert "Horus Rising" in html  # owned
    assert "Vengeful Spirit" not in html  # wanted
    assert "The Horus Heresy: 1-6" not in html  # sold
    assert "Where the Wild Things Are" not in html  # gifted
    assert "Mistborn: The Final Empire" not in html  # borrowed


def test_every_spine_carries_geometry_and_a_cloth_colour(seeded_client: TestClient) -> None:
    for book in _books(seeded_client.get("/").text):
        assert float(book["height"]) > 0
        assert float(book["width"]) > 0
        assert int(book["hue"]) >= 0


def test_zoom_L_is_exactly_proportional(seeded_client: TestClient) -> None:
    """A2 checked through HTTP: scale is 1.6667, so 300 mm of atlas is 500 px."""
    html = seeded_client.get("/?zoom=L").text
    heights = [float(b["height"]) for b in _books(html)]

    assert max(heights) == pytest.approx(500.0)  # 300 mm oversized folio
    assert min(heights) == pytest.approx(170.0)  # 102 mm board book


def test_a_row_grows_to_hold_the_tallest_book_on_it(seeded_client: TestClient) -> None:
    """A 375 px atlas must not be clipped by a 300 px row."""
    html = seeded_client.get("/?zoom=L").text
    chunks = html.split('class="shelf-row" style="')[1:]

    tall = [chunk for chunk in chunks if "--book-height: 500.0px" in chunk]
    assert tall, "expected a 300 mm book to render at zoom L"
    for chunk in tall:
        row_height = float(re.search(r"--row-height: ([\d.]+)px", chunk).group(1))
        assert row_height >= 500.0


def test_zoom_S_is_smaller_than_zoom_L_for_the_same_book(seeded_client: TestClient) -> None:
    small = _books(seeded_client.get("/?zoom=S").text)
    large = _books(seeded_client.get("/?zoom=L").text)

    assert len(small) == len(large)
    assert float(small[0]["height"]) < float(large[0]["height"])


def test_zoom_changes_level_of_detail(seeded_client: TestClient) -> None:
    """A4 end to end: thin books must drop out of 'full' as the shelf shrinks."""
    tiers_at_L = {b["lod"] for b in _books(seeded_client.get("/?zoom=L").text)}
    tiers_at_S = {b["lod"] for b in _books(seeded_client.get("/?zoom=S").text)}

    assert "full" in tiers_at_L
    assert tiers_at_S != tiers_at_L
    assert "sliver" in tiers_at_S or "title" in tiers_at_S


def test_author_keeps_one_cloth_colour_across_zooms(seeded_client: TestClient) -> None:
    """A5 through HTTP: resizing must not repaint the library."""
    at_S = seeded_client.get("/?zoom=S").text
    at_L = seeded_client.get("/?zoom=L").text

    assert BOOK_RE.findall(at_S) and BOOK_RE.findall(at_L)
    hues_S = [int(b["hue"]) for b in _books(at_S)]
    hues_L = [int(b["hue"]) for b in _books(at_L)]
    assert hues_S == hues_L


def test_sections_are_labelled_by_genre(seeded_client: TestClient) -> None:
    html = seeded_client.get("/").text
    labels = re.findall(r'class="shelf-section-label">([^<]+)<', html)

    assert len(labels) == html.count('class="shelf-section"') == 37
    # Short leaf labels, not raw BISAC paths: "Fantasy", not "Fiction / Fantasy / General".
    assert "Fantasy" in labels
    assert not [label for label in labels if "/" in label]


def test_series_badge_is_shown_at_every_zoom(seeded_client: TestClient) -> None:
    """D9: the series number is not gated by level of detail — a thin spine still
    shows which book in a series it is, so every zoom shows the same badges."""
    at_L = seeded_client.get("/?zoom=L").text
    at_S = seeded_client.get("/?zoom=S").text

    assert 'class="series-badge"' in at_L
    assert at_S.count('class="series-badge"') == at_L.count('class="series-badge"')


def test_horus_heresy_reads_in_order_with_its_interquels(seeded_client: TestClient) -> None:
    """D6 + D9 in the rendered row, not just in the query."""
    html = seeded_client.get("/").text
    section = html.split('data-genre="FIC002000"', 1)[1].split("</section>", 1)[0]
    titles = re.findall(r'class="spine-title">([^<]+)<', section)

    def at(title: str) -> int:
        assert title in titles, f"{title} missing from the Action & Adventure row"
        return titles.index(title)

    # Interquels land between their neighbours, and the sold-out box set is
    # nowhere; unnumbered owned books stand after the numbered ones.
    assert at("Horus Rising") < at("The First Heretic") < at("False Gods")
    assert at("False Gods") < at("Fear to Trust") < at("Galaxy in Flame")
    assert at("Galaxy in Flame") < at("The Path of Iron")


def test_thin_spines_still_carry_a_tooltip(seeded_client: TestClient) -> None:
    """A4: at sliver width there is no text, so the label must be on the element."""
    html = seeded_client.get("/?zoom=S").text

    assert re.search(r'<li class="book lod-sliver[^>]*title="[^"]{10,}"', html)


def test_no_broken_images_are_rendered(seeded_client: TestClient) -> None:
    """No page may reference an image file that does not exist."""
    html = seeded_client.get("/").text

    assert "<img" not in html


def test_zoom_control_marks_the_current_preset(seeded_client: TestClient) -> None:
    html = seeded_client.get("/?zoom=M").text

    assert 'class="zoom is-current" aria-current="true">M<' in html
    assert 'href="/?zoom=L"' in html
    assert 'href="/?zoom=S"' in html


def test_zoom_choice_is_remembered_for_the_user(seeded_client: TestClient) -> None:
    """A2: persisted per user, not per URL."""
    seeded_client.get("/?zoom=L")
    html = seeded_client.get("/").text

    assert 'class="zoom is-current" aria-current="true">L<' in html


def test_a_bogus_zoom_is_ignored_without_losing_the_stored_choice(
    seeded_client: TestClient,
) -> None:
    seeded_client.get("/?zoom=L")
    response = seeded_client.get("/?zoom=XX")

    # A typo in a shared link must not break the page nor overwrite the choice.
    assert response.status_code == 200
    assert 'aria-current="true">L<' in response.text
    assert 'aria-current="true">L<' in seeded_client.get("/").text


def test_page_is_english_with_a_theme_attribute(seeded_client: TestClient) -> None:
    html = seeded_client.get("/").text

    assert '<html lang="en"' in html
    assert 'data-theme="light"' in html


def test_styles_and_script_are_linked(seeded_client: TestClient) -> None:
    html = seeded_client.get("/").text

    assert "/static/css/theme.css" in html
    assert "/static/css/shelf.css" in html
    assert "/static/js/shelf.js" in html


def test_static_assets_are_served(seeded_client: TestClient) -> None:
    for path in ("/static/css/theme.css", "/static/css/shelf.css", "/static/js/shelf.js"):
        assert seeded_client.get(path).status_code == 200, path


def test_empty_library_renders_an_empty_state(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """An owner with no books sees a message, not a blank wall."""
    db_path = tmp_path / "empty.db"
    run_alembic(db_path, "head")
    engine = engine_for(db_path)
    monkeypatch.setattr(
        db_module, "SessionLocal", sessionmaker(bind=engine, expire_on_commit=False)
    )
    monkeypatch.setattr(settings, "dev_auth_bypass", True)

    response = TestClient(create_app()).get("/")

    assert response.status_code == 200
    assert "Nothing on the shelves yet" in response.text
    engine.dispose()
