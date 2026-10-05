"""Smoke tests for the package and status route."""

from fastapi.testclient import TestClient

import coinwatch


def test_package_imports() -> None:
    """The installable package loads."""
    assert coinwatch.__version__ == "0.1.0"


def test_status(api_client: TestClient) -> None:
    """GET /api/status reports the service is up."""
    response = api_client.get("/api/status")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "coinwatch"}
