import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from app.application import create_app
from app.config import Settings
from app.schemas import TaskStatus


def test_database_is_owned_by_lifespan_and_reopened_on_restart(tmp_path):
    database = tmp_path / "lifecycle.sqlite3"
    app = create_app(Settings(database_path=database))
    assert not database.exists()
    with TestClient(app):
        pipeline = app.state.pipeline
        task = pipeline.create("某机构发布报告。", 15)
        pipeline.storage.set_task_status(task.task_id, TaskStatus.RUNNING)
    with pytest.raises(sqlite3.ProgrammingError):
        pipeline.storage.get_task(task.task_id)
    with TestClient(app) as client:
        assert app.state.pipeline is not pipeline
        response = client.get(f"/api/tasks/{task.task_id}")
        assert response.status_code == 200
        assert response.json()["status"] == "interrupted"


def test_created_task_is_interrupted_after_restart(tmp_path):
    app = create_app(Settings(database_path=tmp_path / "created.sqlite3"))
    with TestClient(app):
        task = app.state.pipeline.create("某机构发布报告。", 15)
    with TestClient(app) as client:
        response = client.get(f"/api/tasks/{task.task_id}")
        assert response.status_code == 200
        assert response.json()["status"] == "interrupted"


def test_startup_failure_closes_database(tmp_path):
    opened = []

    def broken_factory(storage, settings):
        opened.append(storage)
        raise RuntimeError("startup failed")

    app = create_app(Settings(database_path=tmp_path / "failed.sqlite3"), pipeline_factory=broken_factory)
    with pytest.raises(RuntimeError, match="startup failed"):
        with TestClient(app):
            pytest.fail("startup should fail")
    with pytest.raises(sqlite3.ProgrammingError):
        opened[0].conn.execute("SELECT 1")


def test_app_instances_do_not_share_tasks(tmp_path):
    first = create_app(Settings(database_path=tmp_path / "first.sqlite3"))
    second = create_app(Settings(database_path=tmp_path / "second.sqlite3"))
    with TestClient(first) as a, TestClient(second) as b:
        task_id = a.post("/api/tasks", json={"input_text": "某机构发布报告。"}).json()["task_id"]
        assert a.get(f"/api/tasks/{task_id}").status_code == 200
        assert b.get(f"/api/tasks/{task_id}").status_code == 404


def test_task_claim_retry_and_export_contract(tmp_path):
    app = create_app(Settings(database_path=tmp_path / "flow.sqlite3"))
    with TestClient(app) as client:
        created = client.post("/api/tasks", json={"input_text": "某机构发布报告。另一个机构成立了。"})
        assert created.status_code == 202
        assert created.json()["status"] == "created"
        task_id = created.json()["task_id"]
        summary = client.get(f"/api/tasks/{task_id}").json()
        assert summary["status"] == "succeeded"
        assert summary["score"] is None
        assert summary["claims_processed"] == 2
        page = client.get(f"/api/tasks/{task_id}/claims?offset=1&limit=1").json()
        assert (page["total"], page["offset"], page["limit"]) == (2, 1, 1)
        claim_id = page["items"][0]["claim_id"]
        detail = client.get(f"/api/claims/{claim_id}").json()
        assert detail["label"] == "evidence_insufficient"
        assert detail["evidence_clusters"] == []
        for count in (1, 2):
            retried = client.post(f"/api/claims/{claim_id}/retry")
            assert retried.status_code == 202
            assert retried.json()["retry_count"] == count
            assert client.get(f"/api/claims/{claim_id}").json()["retry_count"] == count
        rejected = client.post(f"/api/claims/{claim_id}/retry")
        assert rejected.status_code == 409
        assert rejected.json()["error"]["code"] == "RETRY_LIMIT"
        summary = client.get(f"/api/tasks/{task_id}").json()
        report = client.get(f"/api/tasks/{task_id}/export?format=json")
        assert report.status_code == 200
        assert report.json()["task"] == summary
        markdown = client.get(f"/api/tasks/{task_id}/export?format=md")
        assert markdown.status_code == 200
        assert "text/markdown" in markdown.headers["content-type"]
        assert "某机构发布报告。" in markdown.text


@pytest.mark.parametrize("path,status", [
    ("/api/tasks/not-an-id", 404),
    ("/api/tasks/missing/claims", 404),
    ("/api/claims/missing", 404),
    ("/api/tasks/missing/export?format=html", 422),
    ("/api/tasks/missing/claims?limit=101", 422),
])
def test_router_errors_keep_the_safe_envelope(tmp_path, path, status):
    app = create_app(Settings(database_path=tmp_path / "errors.sqlite3"))
    with TestClient(app) as client:
        response = client.get(path)
        assert response.status_code == status
        assert set(response.json()["error"]) == {"code", "message", "request_id"}


def test_pipeline_summary_preserves_truncation_after_reload(tmp_path):
    app = create_app(Settings(database_path=tmp_path / "summary.sqlite3"))
    with TestClient(app):
        pipeline = app.state.pipeline
        task = pipeline.create("某机构发布报告。" * 18, 15)
        completed = pipeline.run_sync(task.task_id)
        reloaded = pipeline.get_summary(task.task_id)
        assert reloaded == completed
        assert reloaded.claims_unchecked == 3
        assert reloaded.score is None


def test_concurrent_retry_requests_reserve_claim_once(tmp_path):
    app = create_app(Settings(database_path=tmp_path / "retry-race.sqlite3"))
    with TestClient(app):
        pipeline = app.state.pipeline
        task = pipeline.create("某机构发布报告。", 15)
        pipeline.run_sync(task.task_id)
        claim = pipeline.storage.list_claims(task.task_id, 0, 1)[0][0]
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: pipeline.storage.begin_claim_retry(claim.claim_id, 2), range(2)))
        assert sum(result[0] is not None for result in results) == 1
        assert sorted(error for _, error in results if error) == ["TASK_BUSY"]
        persisted = pipeline.storage.get_claim(claim.claim_id)
        assert persisted.retry_count == 1
