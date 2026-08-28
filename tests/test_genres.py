# The gate that keeps the curated genre list honest: every notation in
# app/genres.py (except the one documented LOCAL/YA deviation, T12) must exist
# in the vendored data/bisac.json with exactly the label we stored. If a list
# edit invents or mistypes a code, this test fails and names every offender.
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, text

from app.genres import GENRE_BY_NOTATION, GENRES, LOCAL_YA_NOTATION
from tests.alembic_runner import run_alembic

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BISAC_PATH = PROJECT_ROOT / "data" / "bisac.json"


def _load_bisac_by_notation() -> dict[str, dict[str, Any]]:
    with BISAC_PATH.open(encoding="utf-8") as handle:
        raw: dict[str, dict[str, Any]] = json.load(handle)
    return {entry["notation"]: entry for entry in raw.values()}


def test_every_curated_notation_exists_in_bisac_json() -> None:
    bisac = _load_bisac_by_notation()
    offenders = [
        genre.notation
        for genre in GENRES
        if genre.notation != LOCAL_YA_NOTATION and genre.notation not in bisac
    ]
    assert not offenders, (
        f"{len(offenders)} curated notation(s) are NOT in data/bisac.json: {offenders}. "
        "Fix app/genres.py — codes must come from the vendored file (T12), not from memory."
    )


def test_curated_labels_match_the_bisac_file_exactly() -> None:
    bisac = _load_bisac_by_notation()
    offenders = [
        f"{genre.notation}: file={bisac[genre.notation]['pref_label']!r} code={genre.label!r}"
        for genre in GENRES
        if genre.notation != LOCAL_YA_NOTATION
        and bisac[genre.notation]["pref_label"] != genre.label
    ]
    assert not offenders, "curated labels drifted from data/bisac.json:\n" + "\n".join(offenders)


def test_local_ya_is_the_only_non_bisac_entry_and_bisac_has_no_young_adult() -> None:
    # T12: no BISAC label contains "Young Adult", which is why LOCAL/YA exists.
    bisac = _load_bisac_by_notation()
    assert any(genre.notation == LOCAL_YA_NOTATION for genre in GENRES)
    non_local = [genre for genre in GENRES if genre.notation != LOCAL_YA_NOTATION]
    assert len(non_local) == len(GENRES) - 1
    assert not [notation for notation in bisac if "young adult" in notation.lower()]


def test_curated_list_size_is_the_planned_curation() -> None:
    # "~45 entries" per SPEC §4; a range so small edits stay possible, a hard
    # bound so nobody re-adds the raw 3,737.
    assert 40 <= len(GENRES) <= 55


def test_no_duplicate_notations_or_sections() -> None:
    assert len(GENRE_BY_NOTATION) == len(GENRES)
    sections = [genre.section for genre in GENRES]
    assert len(set(sections)) == len(sections)


def test_genres_table_is_seeded_exactly_from_the_curated_list(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    # The migration holds an inline copy of the list; this test is what stops
    # the copy from drifting away from app/genres.py.
    db_path = tmp_path_factory.mktemp("m1_genres") / "genres.sqlite3"
    run_alembic(db_path, "head")
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        with engine.connect() as connection:
            rows = connection.execute(
                text("SELECT notation, label, section FROM genres ORDER BY sort_order")
            ).fetchall()
    finally:
        engine.dispose()
    assert [(row[0], row[1], row[2]) for row in rows] == [
        (genre.notation, genre.label, genre.section) for genre in GENRES
    ]
