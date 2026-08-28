# current_user in M0: without the dev bypass every signed-in route redirects the
# browser to /login; with it, a placeholder user gets through. Real sessions are M5.
from __future__ import annotations

from fastapi.testclient import TestClient


def test_anonymous_request_redirects_to_login(client: TestClient) -> None:
    response = client.get("/", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_dev_bypass_gets_the_shelf_through(bypass_client: TestClient) -> None:
    """Asserted on structure, not on a substring: "dev" once matched the
    `width=device-width` meta tag, which proved nothing."""
    response = bypass_client.get("/", follow_redirects=False)

    assert response.status_code == 200
    assert "My library" in response.text
    assert 'class="shelf-toolbar"' in response.text
