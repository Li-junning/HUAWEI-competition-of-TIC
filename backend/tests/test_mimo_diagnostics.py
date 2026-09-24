import errno
import json
import logging

import httpx
import pytest

from app.config import Settings
from app.diagnostics import configure_provider_logging, logger
from app.pipeline import Pipeline
from app.providers.base import JudgmentProviderError
from app.providers.mimo import MiMoEvidenceJudge
from app.schemas import ClaimState, EvidenceCluster, EvidenceItem, TaskStatus
from app.storage import Storage


@pytest.mark.parametrize("status,body,code,calls", [
    (401, {"error": {"message": "secret-key"}}, "LLM_AUTH", 1),
    (403, {}, "LLM_FORBIDDEN", 1),
    (402, {}, "LLM_QUOTA", 1),
    (404, {}, "LLM_MODEL", 1),
    (400, {}, "LLM_REJECTED", 1),
    (429, {}, "LLM_RATE_LIMIT", 3),
    (429, {"error": {"code": "insufficient_quota"}}, "LLM_QUOTA", 1),
    (400, {"error": {"code": "content_filter"}}, "LLM_CONTENT_FILTER", 1),
    (503, {}, "LLM_UNAVAILABLE", 3),
])
def test_http_errors_have_safe_categories_and_bounded_retries(status, body, code, calls, caplog):
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(status, json=body)
    judge = MiMoEvidenceJudge(api_key="secret-key", transport=httpx.MockTransport(handler), sleep=lambda _: None)
    with pytest.raises(JudgmentProviderError) as caught:
        judge._request([{"role": "user", "content": "private-prompt"}])
    assert caught.value.code == code
    assert caught.value.http_status == status
    assert caught.value.diagnostic_id.startswith("llm_")
    assert len(requests) == calls
    assert code in caplog.text
    assert "secret-key" not in caught.value.public_message + caplog.text
    assert "private-prompt" not in caplog.text


@pytest.mark.parametrize("content,finish_reason,code", [
    ('{"decisions":[]}', "length", "LLM_TRUNCATED"),
    (None, "content_filter", "LLM_CONTENT_FILTER"),
    (None, "stop", "LLM_EMPTY"),
    ("invalid-private-output", "stop", "LLM_JSON"),
    ('{"decisions":[{"relation":"secret-key"}]}', "stop", "LLM_SCHEMA"),
])
def test_completion_failures_are_distinguished(content, finish_reason, code, caplog):
    response = {"choices": [{"finish_reason": finish_reason, "message": {"content": content}}]}
    judge = MiMoEvidenceJudge(api_key="secret-key", transport=httpx.MockTransport(lambda _: httpx.Response(200, json=response)), sleep=lambda _: None)
    with pytest.raises(JudgmentProviderError) as caught:
        judge._request([])
    assert caught.value.code == code
    assert caught.value.http_status == 200
    assert code in caplog.text
    assert "invalid-private-output" not in caplog.text
    assert "secret-key" not in caplog.text


@pytest.mark.parametrize("error_type,code,attempts", [
    (httpx.ReadTimeout, "LLM_TIMEOUT", 3),
    (httpx.ConnectError, "LLM_CONNECTION", 3),
    (PermissionError, "LLM_NETWORK_PERMISSION", 1),
])
def test_transport_failures(error_type, code, attempts):
    calls = []
    def handler(request):
        calls.append(request)
        if error_type is PermissionError:
            try:
                raise PermissionError(errno.EACCES, "private-os-detail")
            except PermissionError as exc:
                raise httpx.ConnectError("private-connect-detail") from exc
        raise error_type("private-network-detail")
    judge = MiMoEvidenceJudge(api_key="secret-key", transport=httpx.MockTransport(handler), sleep=lambda _: None)
    with pytest.raises(JudgmentProviderError) as caught:
        judge._request([])
    assert caught.value.code == code
    assert len(calls) == attempts


def test_total_timeout_cancels_a_trickling_response(monkeypatch):
    import asyncio
    import time
    from app.providers import mimo

    closed = []
    class TricklingResponse(httpx.AsyncByteStream):
        async def __aiter__(self):
            while True:
                await asyncio.sleep(0.005)
                yield b" "
        async def aclose(self):
            closed.append(True)

    monkeypatch.setattr(mimo, "REQUEST_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(mimo, "MAX_RETRIES", 0)
    judge = MiMoEvidenceJudge(api_key="test", transport=httpx.MockTransport(
        lambda _: httpx.Response(200, stream=TricklingResponse())))
    start = time.monotonic()
    with pytest.raises(JudgmentProviderError) as caught:
        judge._request([])
    assert caught.value.code == "LLM_TIMEOUT"
    assert time.monotonic() - start < 1.0
    assert closed == [True]


def test_total_timeout_also_cancels_waiting_for_headers(monkeypatch):
    import asyncio
    from app.providers import mimo

    cancelled = []
    async def handler(request):
        try:
            await asyncio.sleep(10)
        finally:
            cancelled.append(True)
        return httpx.Response(200, json={})

    monkeypatch.setattr(mimo, "REQUEST_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(mimo, "MAX_RETRIES", 0)
    judge = MiMoEvidenceJudge(api_key="test", transport=httpx.MockTransport(handler))
    with pytest.raises(JudgmentProviderError) as caught:
        judge._request([])
    assert caught.value.code == "LLM_TIMEOUT"
    assert cancelled == [True]


def test_pipeline_persists_diagnostic_on_initial_failure_and_retry(tmp_path, caplog):
    class Retriever:
        provider = "mock"
        def retrieve(self, claim):
            return [EvidenceCluster(cluster_id="ec_1", items=[EvidenceItem(evidence_id="e_1", excerpt="Paris is in France.")])]
    judge = MiMoEvidenceJudge(api_key="secret-key", transport=httpx.MockTransport(lambda _: httpx.Response(401, text="private-response")))
    storage = Storage(":memory:")
    pipeline = Pipeline(storage, Settings(database_path=tmp_path / "unused.db"), retriever=Retriever(), evidence_judge=judge)
    task = pipeline.create("Paris is in France.", 1)
    summary = pipeline.run_sync(task.task_id)
    claim = storage.list_claims(task.task_id)[0][0]
    assert summary.status == TaskStatus.PARTIAL
    assert summary.failed_providers == ["mimo"]
    assert claim.state == ClaimState.FAILED and claim.label is None
    assert "LLM_AUTH" in claim.reason and "HTTP 401" in claim.reason
    pipeline.retry_sync(claim.claim_id)
    assert "LLM_AUTH" in storage.get_claim(claim.claim_id).reason
    assert claim.claim_id in caplog.text
    assert "private-response" not in caplog.text + claim.reason
    assert "secret-key" not in caplog.text + claim.reason
    storage.close()


def test_internal_error_is_not_misreported_as_an_api_failure(caplog):
    from app.pipeline import _judgment_failure_reason
    from types import SimpleNamespace
    try:
        raise AttributeError("private-input")
    except AttributeError as exc:
        reason = _judgment_failure_reason(exc, SimpleNamespace(claim_id="c_test", task_id="t_test"))
    assert "LLM_INTERNAL" in reason
    assert "AttributeError" in caplog.text
    assert "private-input" not in reason + caplog.text


def test_rotating_log_configuration_is_idempotent(tmp_path):
    original = list(logger.handlers)
    try:
        configure_provider_logging(tmp_path)
        configure_provider_logging(tmp_path)
        added = [handler for handler in logger.handlers if handler not in original]
        assert len(added) == 1
        assert added[0].maxBytes == 2 * 1024 * 1024
        assert added[0].backupCount == 3
    finally:
        for handler in list(logger.handlers):
            if handler not in original:
                logger.removeHandler(handler)
                handler.close()


def test_retry_publishes_running_status_before_background_work(tmp_path):
    import asyncio
    from fastapi import BackgroundTasks
    from app.api.tasks import retry_claim
    from app.schemas import Claim

    class Retriever:
        provider = "mock"
        def retrieve(self, claim):
            return []

    class Judge:
        provider = "mimo"
        def judge(self, claim, clusters):
            pass

    storage = Storage(":memory:")
    pipeline = Pipeline(storage, Settings(database_path=tmp_path / "unused.db"), retriever=Retriever(), evidence_judge=Judge())
    task = pipeline.create("Paris is in France.", 1)
    claim = Claim(claim_id="c_retry", task_id=task.task_id, source_text="test", normalized_claim="test", char_start=0, char_end=4, state=ClaimState.FAILED)
    storage.save_claim(claim)
    storage.set_task_status(task.task_id, TaskStatus.PARTIAL, failed_providers=["mimo"])
    background = BackgroundTasks()
    accepted = asyncio.run(retry_claim(claim.claim_id, background, pipeline))
    assert accepted["state"] == "retrieving"
    assert storage.get_task(task.task_id)[0]["status"] == "running"
    duplicate_background = BackgroundTasks()
    duplicate = asyncio.run(retry_claim(claim.claim_id, duplicate_background, pipeline))
    assert duplicate.status_code == 409
    assert not duplicate_background.tasks
    asyncio.run(background())
    assert storage.get_task(task.task_id)[0]["status"] == "succeeded"
    assert storage.get_claim(claim.claim_id).retry_count == 1
    storage.close()
