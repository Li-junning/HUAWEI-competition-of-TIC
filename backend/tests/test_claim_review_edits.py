from fastapi.testclient import TestClient

from app.application import create_app
from app.config import Settings
from app.schemas import ClaimLabel, ClaimState, TaskStatus


def test_edit_invalidates_old_evidence_and_delete_updates_task_exports(tmp_path):
    app = create_app(Settings(database_path=tmp_path / "review.sqlite3"))
    original = "😀甲公司发布A产品，并收购乙公司。"
    with TestClient(app) as client:
        task_id = client.post("/api/tasks", json={"input_text": original}).json()["task_id"]
        page = client.get(f"/api/tasks/{task_id}/claims").json()
        assert page["total"] == 2
        first, second = page["items"]
        edit = client.patch(f"/api/claims/{first['claim_id']}", json={"normalized_claim": "甲公司发布B产品"})
        assert edit.status_code == 200
        changed = edit.json()
        assert changed["normalized_claim"] == "甲公司发布B产品"
        assert changed["source_text"] == first["source_text"]
        assert changed["char_start"] == first["char_start"]
        assert changed["char_end"] == first["char_end"]
        assert changed["label"] is None and changed["support_score"] is None
        assert changed["state"] == "unchecked"
        assert changed["manually_edited"] is True
        assert changed["evidence_clusters"] == [] and changed["queries"] == []
        assert client.patch(f"/api/claims/{first['claim_id']}", json={"normalized_claim": "  "}).status_code == 422
        assert client.post(f"/api/claims/{first['claim_id']}/retry").status_code == 202
        rechecked = client.get(f"/api/claims/{first['claim_id']}").json()
        assert rechecked["retry_count"] == 1
        assert rechecked["manually_edited"] is True
        assert rechecked["state"] == "done"
        verified = app.state.pipeline.storage.get_claim(first["claim_id"])
        verified.label = ClaimLabel.CREDIBLE
        verified.state = ClaimState.DONE
        app.state.pipeline.storage.save_claim(verified)
        assert client.post(f"/api/claims/{first['claim_id']}/retry").status_code == 409

        response = client.delete(f"/api/claims/{second['claim_id']}")
        assert response.status_code == 204
        remaining = client.get(f"/api/tasks/{task_id}/claims").json()
        assert remaining["total"] == 1
        assert [item["claim_id"] for item in remaining["items"]] == [first["claim_id"]]
        report = client.get(f"/api/tasks/{task_id}/export?format=json").json()
        assert len(report["claims"]) == 1
        assert report["claims"][0]["normalized_claim"] == "甲公司发布B产品"
        assert report["claims"][0]["manually_edited"] is True
        markdown = client.get(f"/api/tasks/{task_id}/export?format=md").text
        assert "人工修改；以下检索文字并非原文逐字内容" in markdown
        assert "原文片段（UTF-16" in markdown
        stored_text = client.get(f"/api/tasks/{task_id}/input").json()["input_text"]
        encoded = stored_text.encode("utf-16-le")
        start, end = changed["char_start"], changed["char_end"]
        assert encoded[start * 2:end * 2].decode("utf-16-le") == changed["source_text"]


def test_edit_and_delete_are_rejected_while_task_is_running(tmp_path):
    app = create_app(Settings(database_path=tmp_path / "review-busy.sqlite3"))
    with TestClient(app) as client:
        created = client.post("/api/tasks", json={"input_text": "甲公司发布产品。"}).json()
        # Recreate a stable running task directly through the storage API so the
        # background task timing cannot make this race dependent.
        pipeline = app.state.pipeline
        pipeline.storage.set_task_status(created["task_id"], TaskStatus.RUNNING)
        claim, _ = pipeline.storage.list_claims(created["task_id"])
        assert claim
        assert client.patch(f"/api/claims/{claim[0].claim_id}", json={"normalized_claim": "修改"}).status_code == 409
        assert client.delete(f"/api/claims/{claim[0].claim_id}").status_code == 409
