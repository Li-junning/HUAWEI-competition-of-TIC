"""Public API requests still have bounded payload and task admission."""

from fastapi.testclient import TestClient

from app.application import create_app
from app.config import Settings


def test_api_accepts_requests_without_access_token(tmp_path):
    app = create_app(Settings(database_path=tmp_path / "public.db"))
    with TestClient(app) as client:
        assert client.get("/api/status").status_code == 200
        assert client.post("/api/tasks", json={"input_text": "某机构发布报告。"}).status_code == 202


def test_oversized_json_body_is_rejected_before_parsing(tmp_path):
    app = create_app(Settings(database_path=tmp_path / "size.db"))
    with TestClient(app) as client:
        response = client.post("/api/tasks", json={"input_text": "a" * 300_000})
        assert response.status_code == 413
        assert response.json()["error"]["code"] == "REQUEST_TOO_LARGE"
        assert app.state.pipeline.storage.conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0


def test_task_admission_is_rate_limited(tmp_path):
    app = create_app(Settings(database_path=tmp_path / "rate.db"))
    with TestClient(app) as client:
        for _ in range(10):
            assert client.post("/api/tasks", json={"input_text": "某机构发布报告。"}).status_code == 202
        blocked = client.post("/api/tasks", json={"input_text": "某机构发布报告。"})
        assert blocked.status_code == 429
        assert blocked.json()["error"]["code"] == "TASK_RATE_LIMIT"
