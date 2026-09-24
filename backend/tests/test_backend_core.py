import json

from app.extract import extract_claims, utf16_len
from app.export import to_markdown
from app.judge import judge_claim
from app.pipeline import Pipeline
from app.schemas import ClaimLabel, ClaimState, TaskStatus
from app.scoring import summarize
from app.storage import Storage


def make_pipeline():
    return Pipeline(Storage(":memory:"))


def test_utf16_offsets_and_extraction_limit():
    text = "😀 正确事实。" + "错误事实。" * 20
    claims, truncated = extract_claims(text, "t_00000000-0000-0000-0000-000000000000", 15)
    assert utf16_len("😀") == 2
    assert claims[0].char_start == 0
    assert claims[0].char_end == utf16_len("😀 正确事实。")
    assert len(claims) == 15 and truncated is True


def test_truncation_discloses_exact_unchecked_count():
    pipeline = make_pipeline()
    task = pipeline.create("事实。" * 18, 15)
    summary = pipeline.run_sync(task.task_id)
    assert summary.status == TaskStatus.PARTIAL
    assert summary.claims_extracted == 18
    assert summary.claims_processed == 15
    assert summary.claims_unchecked == 3
    assert summary.truncated is True


def test_all_evidence_insufficient_hides_score():
    pipeline = make_pipeline()
    task = pipeline.create("某机构在2025年覆盖80%人口。", 15)
    summary = pipeline.run_sync(task.task_id)
    assert summary.status == TaskStatus.SUCCEEDED
    assert summary.score is None
    assert summary.coverage.verification_coverage == 0.0


def test_provider_failure_is_not_processed_or_evidence_insufficient():
    class BrokenRetriever:
        provider = "broken_search"

        def retrieve(self, claim):
            raise TimeoutError("secret provider detail")

    pipeline = Pipeline(Storage(":memory:"), retriever=BrokenRetriever())
    task = pipeline.create("某机构发布了报告。", 15)
    summary = pipeline.run_sync(task.task_id)
    assert summary.status == TaskStatus.PARTIAL
    assert summary.claims_processed == 0
    assert summary.coverage.processing_coverage == 0.0
    assert summary.label_counts["evidence_insufficient"] == 0
    assert summary.failed_providers == ["broken_search"]


def test_failed_retry_clears_previous_evidence_and_score():
    from app.schemas import EvidenceCluster, EvidenceItem

    class ChangingRetriever:
        provider = "changing_search"
        calls = 0

        def retrieve(self, claim):
            self.calls += 1
            if self.calls == 2:
                raise TimeoutError("temporary failure")
            return [EvidenceCluster(cluster_id="ec_old", items=[EvidenceItem(evidence_id="e_old", excerpt="旧证据")])]

    storage = Storage(":memory:")
    pipeline = Pipeline(storage, retriever=ChangingRetriever())
    task = pipeline.create("某机构发布了报告。", 1)
    pipeline.run_sync(task.task_id)
    old = storage.list_claims(task.task_id)[0][0]
    assert old.evidence_clusters
    old.support_score = 75
    storage.save_claim(old)

    pipeline.retry_sync(old.claim_id)
    retried = storage.get_claim(old.claim_id)
    assert retried.state == ClaimState.FAILED
    assert retried.evidence_clusters == []
    assert retried.evidence_cluster_ids == []
    assert retried.support_score is None


def test_unexpected_extraction_failure_finishes_task(monkeypatch):
    def broken_spans(_):
        raise RuntimeError("private input must not enter status")

    monkeypatch.setattr("app.pipeline.atomic_spans", broken_spans)
    pipeline = make_pipeline()
    task = pipeline.create("某机构发布了报告。", 1)
    summary = pipeline.run_sync(task.task_id)
    assert summary.status == TaskStatus.FAILED
    assert summary.error_code == "PIPELINE_INTERNAL"
    assert "private input" not in summary.model_dump_json()


def test_running_task_does_not_show_a_premature_overall_score():
    from app.schemas import EvidenceCluster, EvidenceItem, EvidenceRelation

    pipeline = make_pipeline()
    task = pipeline.create("某机构发布了报告。", 1)
    claim = extract_claims("某机构发布了报告。", task.task_id, 1)[0][0]
    judge_claim(claim, [EvidenceCluster(cluster_id="ec_1", items=[EvidenceItem(
        evidence_id="e_1", excerpt="该机构发布了报告", relation=EvidenceRelation.SUPPORTS,
        authority=0.9, relevance=0.9, time_fit=0.9,
    )])])
    pipeline.storage.save_claim(claim)
    pipeline.storage.set_task_status(task.task_id, TaskStatus.RUNNING)
    assert pipeline.get_summary(task.task_id).score is None


def test_api_safe_errors_and_task_flow(tmp_path):
    import pytest
    fastapi = pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from app.application import create_app
    from app.config import Settings
    app = create_app(Settings(database_path=tmp_path / "api.sqlite3"))
    with TestClient(app) as client:
        response = client.post("/api/tasks", json={"input_text": "   "})
        assert response.status_code == 422
        payload = response.json()
        assert set(payload["error"]) == {"code", "message", "request_id"}
        response = client.post("/api/tasks", json={"input_text": "一个事实。"})
        assert response.status_code == 202
        task_id = response.json()["task_id"]
        summary = client.get(f"/api/tasks/{task_id}")
        assert summary.status_code == 200


def test_markdown_escapes_untrusted_content():
    pipeline = make_pipeline()
    task = pipeline.create("标题", 1)
    claims, _ = extract_claims("标题", task.task_id, 1)
    claims[0].normalized_claim = "<script>alert(1)</script>"
    claims[0].source_text = "[x](javascript:alert(1))"
    markdown = to_markdown(task, claims)
    assert "<script>" not in markdown
    assert "javascript:" not in markdown
