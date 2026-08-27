# The liveness endpoint is what probes and the container healthcheck hit; it must
# answer 200 with a tiny JSON body and no dependencies.
from __future__ import annotations

from fastapi.testclient import TestClient


def test_healthz_returns_200_with_ok_body(client: TestClient) -> None:
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
