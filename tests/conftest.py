# Shared fixtures. Settings are read once at import, so each test pins the dev
# auth bypass explicitly instead of inheriting whatever the shell happened to have.
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.settings import settings


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
