"""Browser preflights cover all supported manual review operations."""

import pytest
from fastapi.testclient import TestClient

from app.application import create_app
from app.config import Settings


@pytest.mark.parametrize("method", ["GET", "POST", "PATCH", "DELETE"])
def test_configured_frontend_can_use_review_methods(tmp_path, method):
    app = create_app(Settings(database_path=tmp_path / "cors.sqlite3"))
    with TestClient(app) as client:
        response = client.options("/api/claims/c_example", headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": method,
            "Access-Control-Request-Headers": "content-type",
        })
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
        assert method in response.headers["access-control-allow-methods"].split(", ")


def test_review_preflight_still_rejects_unconfigured_origins(tmp_path):
    app = create_app(Settings(database_path=tmp_path / "cors-origin.sqlite3"))
    with TestClient(app) as client:
        response = client.options("/api/claims/c_example", headers={
            "Origin": "https://unconfigured.example",
            "Access-Control-Request-Method": "PATCH",
        })
        assert response.status_code == 400
        assert "access-control-allow-origin" not in response.headers
