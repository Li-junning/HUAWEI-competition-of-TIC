"""Restart recovery leaves interrupted claims visible and safe to retry."""

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.application import create_app
from app.config import Settings
from app.extract import extract_claims
from app.pipeline import DeterministicMockRetriever, Pipeline
from app.schemas import ClaimLabel, ClaimState, EvidenceCluster, EvidenceItem, TaskStatus
from app.storage import Storage


def offline_pipeline(storage, settings):
    return Pipeline(storage, settings, retriever=DeterministicMockRetriever(),
                    evidence_judge=False, segmenter=False)


@pytest.mark.parametrize("active_state", [
    ClaimState.PENDING, ClaimState.EXTRACTING, ClaimState.RETRIEVING, ClaimState.JUDGING,
])
def test_restart_finishes_active_claim_and_allows_retry(tmp_path, active_state):
    app = create_app(Settings(database_path=tmp_path / "recovery.sqlite3"),
                     pipeline_factory=offline_pipeline)
    with TestClient(app):
        pipeline = app.state.pipeline
        task = pipeline.create("某机构发布报告。", 1)
        claim = extract_claims("某机构发布报告。", task.task_id, 1)[0][0]
        claim.state = active_state
        claim.label = ClaimLabel.CREDIBLE
        claim.support_score = 90
        claim.retry_count = 1
        claim.evidence_clusters = [EvidenceCluster(cluster_id="ec_saved", items=[
            EvidenceItem(evidence_id="e_saved", excerpt="已收集的证据")
        ])]
        claim.evidence_cluster_ids = ["ec_saved"]
        pipeline.storage.save_claim(claim)
        pipeline.storage.set_task_status(task.task_id, TaskStatus.RUNNING)

    with TestClient(app) as client:
        recovered = client.get(f"/api/claims/{claim.claim_id}").json()
        summary = client.get(f"/api/tasks/{task.task_id}").json()
        assert summary["status"] == "interrupted"
        assert summary["claims_unchecked"] == 1
        assert summary["claims_processed"] == 0
        assert summary["score"] is None
        assert recovered["state"] == "failed"
        assert recovered["label"] is None and recovered["support_score"] is None
        assert "中断" in recovered["reason"] and "重试" in recovered["reason"]
        assert recovered["retry_count"] == 1
        assert recovered["evidence_clusters"][0]["cluster_id"] == "ec_saved"
        assert recovered["evidence_cluster_ids"] == ["ec_saved"]
        retried = client.post(f"/api/claims/{claim.claim_id}/retry")
        assert retried.status_code == 202
        assert retried.json()["retry_count"] == 2
        assert client.get(f"/api/claims/{claim.claim_id}").json()["state"] == "done"
        assert client.get(f"/api/tasks/{task.task_id}").json()["status"] == "succeeded"


def test_restart_preserves_terminal_claims_and_other_tasks():
    storage = Storage(":memory:")
    try:
        pipeline = offline_pipeline(storage, Settings(database_path="unused.sqlite3"))
        task = pipeline.create("某机构发布报告。" * 3, 3)
        claims = extract_claims("某机构发布报告。" * 3, task.task_id, 3)[0]
        for claim, state in zip(claims, [ClaimState.DONE, ClaimState.FAILED, ClaimState.UNCHECKED]):
            claim.state = state
            claim.reason = f"原有结果：{state.value}"
            if state == ClaimState.DONE:
                claim.label = ClaimLabel.EVIDENCE_INSUFFICIENT
            storage.save_claim(claim)
        storage.set_task_status(task.task_id, TaskStatus.RUNNING)
        before = [claim.model_dump() for claim in claims]
        other = pipeline.create("另一个机构发布报告。", 1)
        pipeline.run_sync(other.task_id)
        other_before = pipeline.get_summary(other.task_id)
        assert storage.mark_running_interrupted() == 1
        assert [claim.model_dump() for claim in storage.list_claims(task.task_id)[0]] == before
        assert pipeline.get_summary(other.task_id) == other_before
        assert storage.mark_running_interrupted() == 0
    finally:
        storage.close()


def test_restart_recovery_rolls_back_claims_and_tasks_on_invalid_saved_data():
    storage = Storage(":memory:")
    try:
        pipeline = offline_pipeline(storage, Settings(database_path="unused.sqlite3"))
        task = pipeline.create("某机构发布报告。" * 2, 2)
        claims = extract_claims("某机构发布报告。" * 2, task.task_id, 2)[0]
        for claim in claims:
            claim.state = ClaimState.RETRIEVING
            storage.save_claim(claim)
        storage.set_task_status(task.task_id, TaskStatus.RUNNING)
        storage.conn.execute("UPDATE claims SET data = ? WHERE claim_id = ?", ("{}", claims[1].claim_id))
        storage.conn.commit()
        with pytest.raises(ValidationError):
            storage.mark_running_interrupted()
        assert storage.get_claim(claims[0].claim_id).state == ClaimState.RETRIEVING
        assert storage.conn.execute("SELECT status FROM tasks WHERE task_id = ?", (task.task_id,)).fetchone()[0] == "running"
        assert not storage.conn.in_transaction
    finally:
        storage.close()


def test_successful_retry_clears_old_task_error_after_completion():
    storage = Storage(":memory:")
    try:
        pipeline = offline_pipeline(storage, Settings(database_path="unused.sqlite3"))
        task = pipeline.create("某机构发布报告。", 1)
        pipeline.run_sync(task.task_id)
        claim = storage.list_claims(task.task_id)[0][0]
        storage.set_task_status(task.task_id, TaskStatus.FAILED, error_code="RETRY_INTERNAL")
        _, error = storage.begin_claim_retry(claim.claim_id, 2)
        assert error is None
        running = pipeline.get_summary(task.task_id)
        assert running.status == TaskStatus.RUNNING
        assert running.error_code == "RETRY_INTERNAL"
        completed = pipeline.retry_sync(claim.claim_id)
        assert completed.status == TaskStatus.SUCCEEDED
        assert completed.error_code is None
        assert pipeline.get_summary(task.task_id).error_code is None
    finally:
        storage.close()


def test_failed_retry_keeps_existing_internal_error():
    class BrokenRetriever:
        provider = "broken_search"

        def retrieve(self, claim):
            raise TimeoutError("simulated provider failure")

    storage = Storage(":memory:")
    try:
        pipeline = offline_pipeline(storage, Settings(database_path="unused.sqlite3"))
        task = pipeline.create("某机构发布报告。", 1)
        pipeline.run_sync(task.task_id)
        claim = storage.list_claims(task.task_id)[0][0]
        storage.set_task_status(task.task_id, TaskStatus.FAILED, error_code="RETRY_INTERNAL")
        storage.begin_claim_retry(claim.claim_id, 2)
        pipeline.retriever = BrokenRetriever()
        completed = pipeline.retry_sync(claim.claim_id)
        assert completed.status == TaskStatus.PARTIAL
        assert completed.error_code == "RETRY_INTERNAL"
    finally:
        storage.close()
