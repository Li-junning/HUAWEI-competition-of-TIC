"""Regression coverage for provenance, fusion, budgets and concurrent recovery."""
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from app.config import Settings
from app.export import to_json
from app.extract import extract_claims
from app.judge import judge_claim
from app.knowledge_retrieval import KnowledgeBackedRetriever, merge_evidence
from app.knowledge_schemas import KnowledgeImport
from app.pipeline import Pipeline
from app.providers.base import ProviderError, SearchProviderError, SearchResult
from app.retrieve import SearchBackedRetriever
from app.schemas import ClaimLabel, ClaimState, EvidenceCheck, EvidenceCluster, EvidenceItem, EvidenceRelation, TaskStatus
from app.storage import Storage


def strong_item(eid, *, source_type="web", url=None, excerpt="甲公司成立于2001年。", quality=.9):
    return EvidenceItem(evidence_id=eid, source_type=source_type, url=url, excerpt=excerpt,
                        relation=EvidenceRelation.SUPPORTS, authority=quality, relevance=quality, time_fit=quality)


class SupportJudge:
    provider = "fixture_judge"

    def judge(self, claim, clusters):
        for cluster in clusters:
            for item in cluster.items:
                item.relation = EvidenceRelation.SUPPORTS
                item.relevance, item.time_fit = .9, .5


class CountingSearch:
    name = "tavily"

    def __init__(self, *, fail_after=None, error=None):
        self.calls = 0
        self.fail_after = fail_after
        self.error = error or SearchProviderError("SEARCH_TIMEOUT")

    def search(self, query, *, limit=5, deadline=None):
        self.calls += 1
        if self.fail_after is not None and self.calls > self.fail_after:
            raise self.error
        return [SearchResult(url="https://example.gov/report", title="甲公司成立日期",
                             content="甲公司成立于2001年。", metadata={"content_kind": "raw_text"})]


@pytest.mark.parametrize("relation", [EvidenceRelation.SUPPORTS, EvidenceRelation.REFUTES])
def test_self_declared_sources_cannot_decide_even_at_max_quality_or_in_old_clusters(relation):
    claim = extract_claims("甲公司成立于2001年。", "t")[0][0]
    items = [strong_item(f"kc_{i}", source_type="knowledge", url=f"https://source{i}.gov/doc", quality=1)
             for i in range(3)]
    for item in items:
        item.relation = relation
    clusters = [EvidenceCluster(cluster_id=f"old_{i}", items=[item]) for i, item in enumerate(items)]
    judge_claim(claim, clusters)
    assert claim.label == ClaimLabel.EVIDENCE_INSUFFICIENT
    assert "出处尚未核实" in claim.reason
    assert all(item.relation == relation for item in items)  # Visible for review.
    assert len(merge_evidence(clusters)) == 1


def test_upload_cannot_supply_independence_bonus_to_one_weak_web_source():
    claim = extract_claims("甲公司成立于2001年。", "t")[0][0]
    judge_claim(claim, [EvidenceCluster(cluster_id="web", items=[strong_item("e", quality=.72)]),
                       EvidenceCluster(cluster_id="local", items=[strong_item("kc", source_type="knowledge", quality=1)])])
    assert claim.label == ClaimLabel.EVIDENCE_INSUFFICIENT


def test_duplicate_upload_does_not_replace_provider_returned_provenance():
    url = "https://example.gov/report"
    upload = strong_item("kc", source_type="knowledge", url=url)
    web = strong_item("e", url=url)
    merged = merge_evidence([EvidenceCluster(cluster_id="local", items=[upload]),
                             EvidenceCluster(cluster_id="web", items=[web])], limit=1)
    assert merged[0].items[0].source_type == "web"
    claim = extract_claims("甲公司成立于2001年。", "t")[0][0]
    judge_claim(claim, merged)
    assert claim.label == ClaimLabel.CREDIBLE


def test_upload_pool_cannot_link_independent_web_sources():
    web = [strong_item("e1", url="https://one.example/doc", excerpt="甲公司成立于2001年，原始公告。"),
           strong_item("e2", url="https://two.example/doc", excerpt="2001年创立甲公司，独立资料。")]
    uploads = [item.model_copy(update={"evidence_id": f"kc{i}", "source_type": "knowledge"}) for i, item in enumerate(web)]
    merged = merge_evidence([*[EvidenceCluster(cluster_id=f"kc{i}", items=[item]) for i, item in enumerate(uploads)],
                            *[EvidenceCluster(cluster_id=f"web{i}", items=[item]) for i, item in enumerate(web)]])
    assert len(merged) == 2
    assert all(sum(item.source_type == "web" for item in cluster.items) == 1 for cluster in merged)


def test_two_self_declared_uploads_are_reference_material_in_full_pipeline(monkeypatch):
    monkeypatch.setattr("app.pipeline.get_search_provider", lambda: CountingSearch(fail_after=0))
    with_storage = Storage(":memory:")
    try:
        pipeline = Pipeline(with_storage, evidence_judge=SupportJudge())
        for index, domain in enumerate(["example.gov", "example.edu"]):
            pipeline.knowledge.import_document(KnowledgeImport(title=f"资料{index}", source_url=f"https://{domain}/doc",
                content=f"甲公司成立于2001年。附加说明{index}。"))
        task = pipeline.create("甲公司成立于2001年。", 1)
        summary = pipeline.run_sync(task.task_id)
        claim = with_storage.list_claims(task.task_id)[0][0]
        assert summary.score is None
        assert claim.label == ClaimLabel.EVIDENCE_INSUFFICIENT
        assert len(claim.evidence_clusters) == 1
        assert len(claim.evidence_clusters[0].items) == 2
    finally:
        with_storage.close()


@pytest.mark.parametrize("error", [SearchProviderError("SEARCH_TIMEOUT"), SearchProviderError("SEARCH_QUOTA"),
                                   ProviderError("private key and response body")])
def test_partial_search_failure_remains_visible_and_hides_score_and_can_retry(monkeypatch, error):
    provider = CountingSearch(fail_after=1, error=error)
    monkeypatch.setattr("app.pipeline.get_search_provider", lambda: provider)
    storage = Storage(":memory:")
    try:
        pipeline = Pipeline(storage, evidence_judge=SupportJudge())
        task = pipeline.create("甲公司成立于2001年。", 1)
        summary = pipeline.run_sync(task.task_id)
        claim = storage.list_claims(task.task_id)[0][0]
        assert summary.status == TaskStatus.PARTIAL
        assert summary.failed_providers == ["tavily"] and summary.score is None
        assert claim.label == ClaimLabel.CREDIBLE and claim.evidence_clusters
        assert claim.retrieval_warnings and "第 2 组" in claim.retrieval_warnings[0]
        assert "private" not in claim.model_dump_json()
        _, retry_error = storage.begin_claim_retry(claim.claim_id, 2)
        assert retry_error is None
        assert storage.get_claim(claim.claim_id).label is None
        provider.fail_after = None
        recovered = pipeline.retry_sync(claim.claim_id)
        assert recovered.status == TaskStatus.SUCCEEDED
        assert recovered.failed_providers == [] and recovered.score is not None
        assert storage.get_claim(claim.claim_id).retrieval_warnings == []
    finally:
        storage.close()


@pytest.mark.parametrize("budget", [1, 2, 3])
def test_pipeline_query_budget_controls_both_knowledge_and_web(monkeypatch, budget):
    provider = CountingSearch()
    monkeypatch.setattr("app.pipeline.get_search_provider", lambda: provider)
    storage = Storage(":memory:")
    try:
        pipeline = Pipeline(storage, Settings(database_path=Path(":memory:"), max_queries_per_claim=budget))
        local_queries = []
        monkeypatch.setattr(pipeline.knowledge, "search", lambda query, **kwargs: local_queries.append(query) or [])
        task = pipeline.create("甲公司成立于2001年。", 1)
        pipeline.run_sync(task.task_id)
        assert provider.calls == budget and len(local_queries) == budget
    finally:
        storage.close()


def test_application_budget_also_caps_injected_builtin_retriever():
    provider = CountingSearch()
    storage = Storage(":memory:")
    try:
        pipeline = Pipeline(storage, Settings(database_path=Path(":memory:"), max_queries_per_claim=1),
                            retriever=SearchBackedRetriever(provider))
        task = pipeline.create("甲公司成立于2001年。", 1)
        pipeline.run_sync(task.task_id)
        assert provider.calls == 1
    finally:
        storage.close()


def test_fusion_preserves_unique_compound_fact_under_five_item_limit():
    text = "甲公司成立于2001年，并位于南京。"
    first = strong_item("e_first", url="https://one.example/first")
    second = strong_item("e_second", url="https://one.example/second", excerpt="甲公司位于南京。")
    web = [EvidenceCluster(cluster_id="one", items=[first, second]), *[
        EvidenceCluster(cluster_id=f"source{i}", items=[strong_item(f"e_{i}", url=f"https://source{i}.example/first")])
        for i in range(3)
    ]]
    local = EvidenceCluster(cluster_id="local", items=[strong_item("kc", source_type="knowledge")])
    merged = merge_evidence([local, *web], limit=5, claim_text=text)
    kept = [item for cluster in merged for item in cluster.items]
    assert len(kept) == 5 and second in kept and first in kept
    assert any(first in cluster.items and second in cluster.items for cluster in merged)
    # Covered facts retain their same-site independence constraint.
    claim = extract_claims("甲公司成立于2001年。", "t")[0][0]
    claim.normalized_claim = text
    for item in kept:
        item.relation = EvidenceRelation.PARTIALLY_SUPPORTS
    first.checks = [EvidenceCheck(part_id=1, part_text="甲公司成立于2001年", relation=EvidenceRelation.SUPPORTS, excerpt=first.excerpt)]
    second.checks = [EvidenceCheck(part_id=2, part_text="甲公司位于南京。", relation=EvidenceRelation.SUPPORTS, excerpt=second.excerpt)]
    judge_claim(claim, merged)
    assert claim.label == ClaimLabel.CREDIBLE


def test_fusion_does_not_invent_missing_fact_or_exceed_smaller_limit():
    text = "甲公司成立于2001年，并位于南京。"
    clusters = [EvidenceCluster(cluster_id=str(i), items=[strong_item(str(i), url=f"https://site{i}.example/doc")])
                for i in range(5)]
    merged = merge_evidence(clusters, limit=2, claim_text=text)
    assert sum(len(cluster.items) for cluster in merged) == 2
    assert not any("南京" in item.excerpt for cluster in merged for item in cluster.items)


class ConcurrentRetriever:
    provider = "fixture"

    def __init__(self, parties):
        self.barrier = threading.Barrier(parties, timeout=5)
        self.lock = threading.Lock()
        self.active = self.peak = self.calls = 0

    def retrieve(self, claim, *, deadline=None):
        with self.lock:
            self.active += 1
            self.calls += 1
            self.peak = max(self.peak, self.active)
        try:
            self.barrier.wait()
            return []
        finally:
            with self.lock:
                self.active -= 1


def test_task_runs_multiple_claims_concurrently_and_preserves_original_order():
    storage = Storage(":memory:")
    try:
        retriever = ConcurrentRetriever(3)
        pipeline = Pipeline(storage, Settings(database_path=Path(":memory:"), max_concurrency=3), retriever=retriever)
        task = pipeline.create("".join(f"甲公司成立于{2000+i}年。" for i in range(6)), 6)
        summary = pipeline.run_sync(task.task_id)
        assert summary.status == TaskStatus.SUCCEEDED
        assert retriever.calls == 6 and retriever.peak == 3
        claims = storage.list_claims(task.task_id)[0]
        assert [claim.char_start for claim in claims] == sorted(claim.char_start for claim in claims)
    finally:
        storage.close()


def test_search_limit_is_shared_across_simultaneous_tasks():
    storage = Storage(":memory:")
    try:
        retriever = ConcurrentRetriever(2)
        pipeline = Pipeline(storage, Settings(database_path=Path(":memory:"), max_concurrency=2), retriever=retriever)
        tasks = [pipeline.create("甲公司成立于2001年。乙公司成立于2002年。丙公司成立于2003年。", 3) for _ in range(2)]
        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(lambda task: pipeline.run_sync(task.task_id), tasks))
        assert all(summary.status == TaskStatus.SUCCEEDED for summary in results)
        assert retriever.calls == 6 and retriever.peak == 2
    finally:
        storage.close()


def test_search_queue_has_deadline_and_does_not_start_after_budget():
    class NeverCalled:
        provider = "fixture"
        def retrieve(self, claim, **kwargs):
            pytest.fail("search must not start after queue timeout")

    storage = Storage(":memory:")
    try:
        pipeline = Pipeline(storage, Settings(database_path=Path(":memory:"), max_concurrency=1,
                                             task_timeout_seconds=.05), retriever=NeverCalled())
        pipeline._external_slots.acquire()
        try:
            task = pipeline.create("甲公司成立于2001年。", 1)
            summary = pipeline.run_sync(task.task_id)
        finally:
            pipeline._external_slots.release()
        claim = storage.list_claims(task.task_id)[0][0]
        assert summary.status == TaskStatus.PARTIAL and summary.score is None
        assert claim.state == ClaimState.UNCHECKED and claim.unchecked_reason == "task_budget"
    finally:
        storage.close()


def test_model_time_is_reserved_and_judging_state_is_persisted():
    deadlines = {}
    storage = Storage(":memory:")

    class Retriever:
        provider = "fixture"
        def retrieve(self, claim, *, deadline):
            deadlines["search"] = deadline
            return [EvidenceCluster(cluster_id="web", items=[strong_item("e")])]

    class Judge:
        def judge_with_deadline(self, claim, clusters, *, deadline):
            deadlines["judge"] = deadline
            saved = storage.get_claim(claim.claim_id)
            assert saved.state == ClaimState.JUDGING and saved.evidence_clusters

    try:
        pipeline = Pipeline(storage, retriever=Retriever(), evidence_judge=Judge())
        task = pipeline.create("甲公司成立于2001年。", 1)
        assert pipeline.run_sync(task.task_id).status == TaskStatus.SUCCEEDED
        assert deadlines["judge"] > deadlines["search"]
    finally:
        storage.close()


def test_budget_unchecked_claim_can_resume_but_unresolved_reference_cannot(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr("app.pipeline.time.monotonic", lambda: clock[0])
    storage = Storage(":memory:")

    class Retriever:
        provider = "fixture"
        def retrieve(self, claim, **kwargs):
            clock[0] += 2
            return []

    try:
        pipeline = Pipeline(storage, Settings(database_path=Path(":memory:"), max_concurrency=1, task_timeout_seconds=1),
                            retriever=Retriever())
        task = pipeline.create("甲公司成立于2001年。乙公司成立于2002年。", 2)
        assert pipeline.run_sync(task.task_id).status == TaskStatus.PARTIAL
        claim = storage.list_claims(task.task_id)[0][-1]
        assert claim.unchecked_reason == "task_budget"
        _, error = storage.begin_claim_retry(claim.claim_id, 2)
        assert error is None
        assert pipeline.retry_sync(claim.claim_id).status == TaskStatus.SUCCEEDED
        assert storage.get_claim(claim.claim_id).unchecked_reason is None
        ambiguous_task = pipeline.create("它成立于2001年。", 1)
        pipeline.run_sync(ambiguous_task.task_id)
        ambiguous = storage.list_claims(ambiguous_task.task_id)[0][0]
        assert ambiguous.unchecked_reason == "unresolved_reference"
        assert storage.begin_claim_retry(ambiguous.claim_id, 2)[1] == "RETRY_NOT_ALLOWED"
    finally:
        storage.close()


def test_legacy_budget_unchecked_remains_retryable():
    storage = Storage(":memory:")
    try:
        pipeline = Pipeline(storage)
        task = pipeline.create("甲公司成立于2001年。", 1)
        claim = extract_claims("甲公司成立于2001年。", task.task_id)[0][0]
        claim.state, claim.reason = ClaimState.UNCHECKED, "任务总预算已耗尽，该声明尚未执行检索判断。"
        storage.save_claim(claim)
        storage.set_task_status(task.task_id, TaskStatus.PARTIAL)
        assert storage.begin_claim_retry(claim.claim_id, 2)[1] is None
    finally:
        storage.close()


def test_budget_recovery_api_returns_new_result_and_rejects_ambiguous_claim(tmp_path):
    from fastapi.testclient import TestClient
    from app.application import create_app

    class Retriever:
        provider = "fixture"
        def retrieve(self, claim, **kwargs):
            return [EvidenceCluster(cluster_id="web", items=[strong_item("e")])]

    app = create_app(Settings(database_path=tmp_path / "recovery.db"),
                     pipeline_factory=lambda storage, settings: Pipeline(storage, settings, retriever=Retriever()))
    with TestClient(app) as client:
        pipeline = app.state.pipeline
        task = pipeline.create("甲公司成立于2001年。", 1)
        claim = extract_claims("甲公司成立于2001年。", task.task_id)[0][0]
        claim.state, claim.unchecked_reason = ClaimState.UNCHECKED, "task_budget"
        pipeline.storage.save_claim(claim)
        pipeline.storage.set_task_status(task.task_id, TaskStatus.PARTIAL)
        response = client.post(f"/api/claims/{claim.claim_id}/retry")
        assert response.status_code == 202
        detail = client.get(f"/api/claims/{claim.claim_id}").json()
        assert detail["state"] == "done" and detail["unchecked_reason"] is None
        assert detail["support_score"] == 95
        summary = client.get(f"/api/tasks/{task.task_id}").json()
        exported = client.get(f"/api/tasks/{task.task_id}/export?format=json").json()
        assert summary["status"] == "succeeded" and summary["score"] == 95
        assert exported["claims"][0]["support_score"] == detail["support_score"]
        ambiguous_task = pipeline.create("它成立于2001年。", 1)
        pipeline.run_sync(ambiguous_task.task_id)
        ambiguous = pipeline.storage.list_claims(ambiguous_task.task_id)[0][0]
        rejection = client.post(f"/api/claims/{ambiguous.claim_id}/retry")
        assert rejection.status_code == 409 and rejection.json()["error"]["code"] == "RETRY_NOT_ALLOWED"


@pytest.mark.parametrize("text", ["值得注意的是，甲公司成立于2001年。", "该大学2025年推荐免试录取人数为1000人。",
                                  "我认为甲公司成立于2001年。", "甲公司发布了《观点》期刊。",
                                  "推荐系统于2025年发布。", "建议书于2025年发布。", "我认为地球围绕太阳公转。"])
def test_discourse_or_named_terms_do_not_exclude_facts(text):
    claims = extract_claims(text, "t")[0]
    assert all(claim.type != "opinion" and claim.label is None for claim in claims)
    assert all(text.encode("utf-16-le")[claim.char_start*2:claim.char_end*2].decode("utf-16-le") == claim.source_text
               for claim in claims)


@pytest.mark.parametrize("text", ["我认为这个方案值得推荐。", "建议每天睡8小时。", "推荐购买这个产品。", "我认为这个方案不错。"])
def test_subjective_advice_remains_not_applicable(text):
    claims = extract_claims(text, "t")[0]
    assert all(claim.label == ClaimLabel.NOT_APPLICABLE for claim in claims)


def test_score_is_persisted_and_consistent_on_old_reads_and_export():
    class Retriever:
        provider = "fixture"
        def retrieve(self, claim, **kwargs):
            return [EvidenceCluster(cluster_id="web", items=[strong_item("e", quality=.9)])]

    storage = Storage(":memory:")
    try:
        pipeline = Pipeline(storage, retriever=Retriever())
        task = pipeline.create("甲公司成立于2001年。", 1)
        summary = pipeline.run_sync(task.task_id)
        claim = storage.list_claims(task.task_id)[0][0]
        raw = json.loads(storage.conn.execute("SELECT data FROM claims WHERE claim_id=?", (claim.claim_id,)).fetchone()[0])
        assert raw["support_score"] == claim.support_score == summary.score == 95
        raw["support_score"] = None  # A saved report from before this fix.
        storage.conn.execute("UPDATE claims SET data=? WHERE claim_id=?", (json.dumps(raw), claim.claim_id))
        storage.conn.commit()
        detail = storage.get_claim(claim.claim_id)
        claims = storage.list_claims(task.task_id)[0]
        exported = json.loads(to_json(pipeline.get_summary(task.task_id), claims))
        assert detail.support_score == claims[0].support_score == exported["claims"][0]["support_score"] == 95
    finally:
        storage.close()
