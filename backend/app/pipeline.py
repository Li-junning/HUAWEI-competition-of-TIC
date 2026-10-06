"""Bounded, mock-first task pipeline with explicit partial-failure semantics."""

from __future__ import annotations

import asyncio
import json
import inspect
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

from .config import Settings, get_settings
from .extract import extract_claims
from .claim_segmentation import atomic_spans
from .providers.segmentation import get_segmenter
from .judge import judge_claim
from .providers import get_evidence_judge, get_search_provider
from .providers.base import SearchProviderError, JudgmentProviderError
from .diagnostics import audit
from .retrieve import SearchBackedRetriever
from .knowledge import KnowledgeBase
from .knowledge_retrieval import KnowledgeBackedRetriever
from .schemas import ClaimState, EvidenceCluster, TaskStatus, TaskSummary, now_utc, new_id
from .summaries import build_task_summary
from .storage import Storage


logger = logging.getLogger("verifier")


def _retrieve_with_deadline(retriever, claim, deadline: float):
    retrieve = retriever.retrieve
    try:
        parameters = inspect.signature(retrieve).parameters.values()
        supports_deadline = any(
            parameter.name == "deadline" or parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in parameters
        )
    except (TypeError, ValueError):
        supports_deadline = False
    return retrieve(claim, deadline=deadline) if supports_deadline else retrieve(claim)


def _retrieval_failure_reason(exc: Exception, fallback: str) -> str:
    # Never log raw exception text, response bodies, query text or credentials.
    code = exc.code if isinstance(exc, SearchProviderError) else "SEARCH_UNKNOWN"
    logger.warning("retrieval failed: code=%s exception_type=%s", code, type(exc).__name__)
    if isinstance(exc, SearchProviderError):
        return exc.public_message + " 未将技术失败当作反证。"
    return fallback


def _judgment_failure_reason(exc: Exception, claim) -> str:
    diagnostic_id = getattr(exc, "diagnostic_id", None) or f"llm_{uuid4().hex[:12]}"
    code = exc.code if isinstance(exc, JudgmentProviderError) else "LLM_INTERNAL"
    frame = exc.__traceback__
    location = None
    while frame is not None:
        location = f"{Path(frame.tb_frame.f_code.co_filename).name}:{frame.tb_lineno}"
        frame = frame.tb_next
    audit("judgment_failed", diagnostic_id=diagnostic_id, task_id=claim.task_id, claim_id=claim.claim_id,
          code=code, exception_type=type(exc).__name__, location=location, stage="judging")
    if isinstance(exc, JudgmentProviderError):
        exc.diagnostic_id = diagnostic_id
        return exc.public_message + " 未将技术失败当作反证。"
    return f"证据判断程序发生内部错误，请查看后端诊断日志。（LLM_INTERNAL；诊断编号 {diagnostic_id}）未将技术失败当作反证。"


class DeterministicMockRetriever:
    provider = "mock"

    def retrieve(self, claim) -> list[EvidenceCluster]:
        # Empty evidence is deliberate: live providers must be explicitly wired in.
        return []


class Pipeline:
    def __init__(self, storage: Storage, settings: Settings | None = None, retriever=None, evidence_judge=None, segmenter=None) -> None:
        self.storage = storage
        self.settings = settings or get_settings()
        self.knowledge = KnowledgeBase(storage)
        self.retriever = retriever or KnowledgeBackedRetriever(
            self.knowledge,
            SearchBackedRetriever(get_search_provider(), max_evidence=self.settings.max_evidence_per_claim,
                                  max_queries=self.settings.max_queries_per_claim),
            max_evidence=self.settings.max_evidence_per_claim,
            max_queries=self.settings.max_queries_per_claim,
        )
        # Apply the application budget to built-in injected retrievers too.
        # Custom provider protocols remain free to implement their own plans.
        if isinstance(self.retriever, (SearchBackedRetriever, KnowledgeBackedRetriever)):
            self.retriever.max_queries = min(self.retriever.max_queries, self.settings.max_queries_per_claim)
            if isinstance(self.retriever, KnowledgeBackedRetriever) and isinstance(self.retriever.web_retriever, SearchBackedRetriever):
                self.retriever.web_retriever.max_queries = min(self.retriever.web_retriever.max_queries, self.retriever.max_queries)
        self.evidence_judge = evidence_judge if evidence_judge is not None else get_evidence_judge()
        self.segmenter = segmenter if segmenter is not None else get_segmenter()
        self._external_slots = threading.BoundedSemaphore(self.settings.max_concurrency)

    def create(self, input_text: str, claim_limit: int) -> TaskSummary:
        input_text = input_text.strip()
        if not input_text or len(input_text) > self.settings.max_input_chars:
            raise ValueError("input text is blank or exceeds the configured limit")
        effective_limit = min(max(1, claim_limit), self.settings.max_claims)
        task_id = new_id("t")
        now = now_utc()
        summary = TaskSummary(task_id=task_id, status=TaskStatus.CREATED, created_at=now, updated_at=now,
                              input_char_count=len(input_text), claim_limit=effective_limit)
        self.storage.create_task(summary, input_text)
        return summary

    def run_sync(self, task_id: str) -> TaskSummary:
        if self.storage.get_task(task_id) is None:
            raise KeyError(task_id)
        try:
            return self._run_sync(task_id)
        except Exception as exc:
            # BackgroundTasks has already returned 202. Persist a terminal state
            # so clients do not poll a permanently running task.
            logger.error("task failed unexpectedly: task_id=%s exception_type=%s", task_id, type(exc).__name__)
            self.storage.set_task_status(task_id, TaskStatus.FAILED, error_code="PIPELINE_INTERNAL")
            return self.get_summary(task_id)

    def _run_sync(self, task_id: str) -> TaskSummary:
        record = self.storage.get_task(task_id)
        if not record:
            raise KeyError(task_id)
        row, _ = record
        self.storage.set_task_status(task_id, TaskStatus.RUNNING)
        started = time.monotonic()
        spans = atomic_spans(row["input_text"])
        segmentation_method = "rules"
        if self.segmenter and spans:
            try:
                spans = self.segmenter.split(row["input_text"], deadline=started + self.settings.task_timeout_seconds)
                segmentation_method = "mimo"
            except Exception as exc:
                segmentation_method = "rules_fallback"
                # A failed boundary proposal does not invalidate local extraction.
                audit("segmentation_fallback", task_id=task_id,
                      code=getattr(exc, "code", "LLM_INTERNAL"), exception_type=type(exc).__name__)
        candidate_count = len(spans)
        claims, truncated = extract_claims(row["input_text"], task_id, min(row["claim_limit"], self.settings.max_claims), spans=spans)
        limit_unchecked = max(0, candidate_count - len(claims))
        self.storage.set_task_status(task_id, TaskStatus.RUNNING, truncated=truncated, claims_unchecked=limit_unchecked,
                                     segmentation_method=segmentation_method)
        for claim in claims:
            self.storage.save_claim(claim)
        failures: list[str] = []
        deadline = started + self.settings.task_timeout_seconds
        # Only the bounded worker set runs; waiting claims share the task's
        # original deadline. Results are collected before the task is terminal.
        with ThreadPoolExecutor(max_workers=max(1, self.settings.max_concurrency)) as workers:
            for claim_failures in workers.map(lambda claim: self._process_claim(claim, deadline), claims):
                failures.extend(claim_failures)
        all_claims, _ = self.storage.list_claims(task_id, 0, 1000)
        status = TaskStatus.FAILED if not all_claims and failures else (TaskStatus.PARTIAL if truncated or failures or any(c.state in {ClaimState.FAILED, ClaimState.UNCHECKED} for c in all_claims) else TaskStatus.SUCCEEDED)
        self.storage.set_task_status(task_id, status, truncated=truncated, claims_unchecked=limit_unchecked,
                                     failed_providers=failures)
        row2, _ = self.storage.get_task(task_id)
        return build_task_summary(row2, all_claims, technical_failure=bool(failures))

    async def run(self, task_id: str) -> TaskSummary:
        return await asyncio.to_thread(self.run_sync, task_id)

    def _process_claim(self, claim, deadline: float) -> list[str]:
        if claim.state == ClaimState.UNCHECKED:
            return []
        if claim.label is not None:
            claim.state = ClaimState.DONE
            claim.reason = "该内容不适用于事实核验。"
            self.storage.save_claim(claim)
            return []
        remaining = max(0.0, deadline - time.monotonic())
        # Reserve judgment time when using a model; include search-slot queue
        # time in this budget rather than waiting indefinitely on a semaphore.
        reserve = min(30.0, remaining * .4) if self.evidence_judge is not None else 0.0
        retrieval_deadline = deadline - reserve
        acquired = self._external_slots.acquire(timeout=max(0.0, retrieval_deadline - time.monotonic()))
        if not acquired or time.monotonic() >= retrieval_deadline:
            if acquired:
                self._external_slots.release()
            claim.state = ClaimState.UNCHECKED
            claim.unchecked_reason = "task_budget"
            claim.reason = "任务处理预算已耗尽，该声明尚未执行检索判断，可继续核验。"
            self.storage.save_claim(claim)
            return ["task_budget"]
        claim.state = ClaimState.RETRIEVING
        claim.unchecked_reason = None
        failures = []
        try:
            self.storage.save_claim(claim)
            clusters = _retrieve_with_deadline(self.retriever, claim, retrieval_deadline)
            if claim.retrieval_warnings:
                failures.append(getattr(self.retriever, "provider", "retrieval"))
        except Exception as exc:
            claim.state = ClaimState.FAILED
            claim.reason = _retrieval_failure_reason(exc, "检索服务暂时不可用，未将技术失败当作反证。")
            claim.label = None
            failures.append(getattr(self.retriever, "provider", "retrieval"))
            self.storage.save_claim(claim)
            return failures
        finally:
            self._external_slots.release()
        try:
            claim.state = ClaimState.JUDGING
            claim.evidence_clusters = clusters
            claim.evidence_cluster_ids = [cluster.cluster_id for cluster in clusters]
            self.storage.save_claim(claim)
            if clusters and self.evidence_judge is not None and time.monotonic() >= deadline:
                raise JudgmentProviderError("LLM_TIMEOUT")
            judge_claim(claim, clusters, self.evidence_judge, deadline=deadline)
        except Exception as exc:
            claim.state = ClaimState.FAILED
            claim.reason = _judgment_failure_reason(exc, claim)
            claim.label = None
            failures.append(getattr(self.evidence_judge, "provider", "judgment"))
        self.storage.save_claim(claim)
        return failures

    def retry_sync(self, claim_id: str) -> TaskSummary:
        try:
            return self._retry_sync(claim_id)
        except Exception as exc:
            logger.error("claim retry failed unexpectedly: claim_id=%s exception_type=%s", claim_id, type(exc).__name__)
            claim = self.storage.get_claim(claim_id)
            if claim is not None:
                claim.state = ClaimState.FAILED
                claim.reason = "重试程序发生内部错误，未能完成本次核验。"
                claim.label = None
                self.storage.save_claim(claim)
                self.storage.set_task_status(claim.task_id, TaskStatus.FAILED, error_code="RETRY_INTERNAL")
                return self.get_summary(claim.task_id)
            raise

    def _retry_sync(self, claim_id: str) -> TaskSummary:
        deadline = time.monotonic() + self.settings.task_timeout_seconds
        claim = self.storage.get_claim(claim_id)
        if not claim:
            raise KeyError(claim_id)
        self.storage.set_task_status(claim.task_id, TaskStatus.RUNNING)
        claim.label = None
        claim.support_score = None
        claim.reason = None
        claim.evidence_cluster_ids = []
        claim.evidence_clusters = []
        claim.paper_check = None
        claim.retrieval_warnings = []
        claim.state = ClaimState.PENDING
        claim.unchecked_reason = None
        self._process_claim(claim, deadline)
        record = self.storage.get_task(claim.task_id)
        row, claims = record
        has_failed_claim = any(c.state == ClaimState.FAILED or c.retrieval_warnings for c in claims)
        has_unchecked_claim = any(c.state == ClaimState.UNCHECKED for c in claims)
        remaining_failures = json.loads(row["failed_providers"]) if has_failed_claim else []
        if has_failed_claim and not remaining_failures:
            remaining_failures = [getattr(self.retriever, "provider", "retrieval")]
        final_status = TaskStatus.PARTIAL if row["truncated"] or remaining_failures or has_unchecked_claim else TaskStatus.SUCCEEDED
        self.storage.set_task_status(
            claim.task_id, final_status, failed_providers=remaining_failures,
            clear_error_code=final_status == TaskStatus.SUCCEEDED,
        )
        return build_task_summary(
            self.storage.get_task(claim.task_id)[0], claims,
            technical_failure=has_failed_claim or has_unchecked_claim,
        )

    def get_summary(self, task_id: str) -> TaskSummary | None:
        record = self.storage.get_task(task_id)
        if record is None:
            return None
        row, claims = record
        return build_task_summary(row, claims)
