# Shared fixtures. Settings are read once at import, so each test pins the dev
# auth bypass explicitly instead of inheriting whatever the shell happened to have.
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import sessionmaker

from app import db as db_module
from app.main import create_app
from app.seed import seed
from app.settings import settings
from tests.alembic_runner import engine_for, run_alembic


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """App with auth bypass OFF — the behaviour every deployment gets."""
    monkeypatch.setattr(settings, "dev_auth_bypass", False)
    return TestClient(create_app())


@pytest.fixture()
def bypass_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """App with COLOPHON_DEV_AUTH_BYPASS=1 semantics."""
    monkeypatch.setattr(settings, "dev_auth_bypass", True)
    return TestClient(create_app())


@pytest.fixture()
def seeded_db(monkeypatch: pytest.MonkeyPatch, tmp_path) -> tuple[TestClient, Engine]:
    """The seed fixture database, exposed as (client, engine).

    Tests must not read the project's dev database: it changes with every
    `make seed` on the laptop, and a shelf test that quietly depends on it turns
    into a mystery failure. The app builds its engine at import, so the session
    factory is rebound here rather than the URL. The engine comes back too, so a
    test can assert against the rows rather than only against rendered HTML.
    """
    db_path = tmp_path / "shelf.db"
    run_alembic(db_path, "head")
    engine: Engine = engine_for(db_path)
    seed(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(db_module, "SessionLocal", factory)
    monkeypatch.setattr(settings, "dev_auth_bypass", True)

    yield TestClient(create_app()), engine

    engine.dispose()


@pytest.fixture()
def seeded_client(seeded_db: tuple[TestClient, Engine]) -> TestClient:
    return seeded_db[0]
