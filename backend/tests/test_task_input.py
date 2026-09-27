from fastapi.testclient import TestClient

from app.application import create_app
from app.config import Settings


def test_input_endpoint_preserves_stored_text_and_offsets(tmp_path):
    application = create_app(Settings(database_path=tmp_path / "input.sqlite3"))
    original = "  😀某机构于2024年发布报告。\n另一机构成立了。  "
    with TestClient(application) as client:
        task_id = client.post("/api/tasks", json={"input_text": original}).json()["task_id"]
        response = client.get(f"/api/tasks/{task_id}/input")
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert response.json() == {"task_id": task_id, "input_text": original.strip()}
        encoded = response.json()["input_text"].encode("utf-16-le")
        for claim in client.get(f"/api/tasks/{task_id}/claims").json()["items"]:
            span = encoded[claim["char_start"] * 2:claim["char_end"] * 2].decode("utf-16-le")
            assert span == claim["source_text"]


def test_missing_input_uses_safe_error_envelope(tmp_path):
    application = create_app(Settings(database_path=tmp_path / "missing.sqlite3"))
    with TestClient(application) as client:
        for task_id in ("invalid", "t_00000000-0000-0000-0000-000000000000"):
            response = client.get(f"/api/tasks/{task_id}/input")
            assert response.status_code == 404
            assert "input_text" not in response.json()
            assert set(response.json()["error"]) == {"code", "message", "request_id"}
