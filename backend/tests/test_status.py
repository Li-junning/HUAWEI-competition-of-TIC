from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_pipeline
from app.application import create_app
from app.config import Settings


@pytest.mark.parametrize("search,judge,ready,live,phrase", [
    ("mock", "off", False, False, "未启用联网搜索或模型判断"),
    ("tavily", "off", False, True, "已启用联网搜索"),
    ("mock", "mimo", False, False, "已启用模型判断"),
    ("tavily", "mimo", True, True, "外部连通性尚未验证"),
])
def test_status_reports_pipeline_modes_without_touching_production_db(
    tmp_path, search, judge, ready, live, phrase,
):
    app = create_app(Settings(database_path=tmp_path / "status.sqlite3"))
    fake_pipeline = SimpleNamespace(
        retriever=SimpleNamespace(provider=search),
        evidence_judge=None if judge == "off" else SimpleNamespace(provider=judge),
    )
    app.dependency_overrides[get_pipeline] = lambda: fake_pipeline
    with TestClient(app) as client:
        response = client.get("/api/status")
        assert response.status_code == 200
        status = response.json()
        assert (status["ready"], status["live"]) == (ready, live)
        assert status["search_mode"] == search
        assert status["judge_mode"] == judge
        assert phrase in status["message"]
