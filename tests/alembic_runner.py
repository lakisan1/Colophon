# Shared helper for tests that need the real Alembic migration run against a
# throwaway SQLite file. The DB path travels via COLOPHON_DB_PATH because that
# is the single source app.settings — and therefore alembic/env.py — reads.
from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def run_alembic(db_path: Path, revision: str) -> None:
    """Upgrade the given file to `revision`, or downgrade it fully when 'base'."""
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("COLOPHON_DB_PATH", str(db_path))
    try:
        config = Config(str(PROJECT_ROOT / "alembic.ini"))
        if revision == "base":
            command.downgrade(config, "base")
        else:
            command.upgrade(config, revision)
    finally:
        monkeypatch.undo()


def engine_for(db_path: Path) -> Engine:
    """Engine with FK enforcement on, matching what app/db.py does for the app."""
    engine = create_engine(f"sqlite:///{db_path}")

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine
