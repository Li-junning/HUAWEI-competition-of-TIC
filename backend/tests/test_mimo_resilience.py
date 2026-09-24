import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import httpx
import pytest

from app.providers import mimo
from app.providers.base import JudgmentProviderError
from app.schemas import Claim, EvidenceCluster, EvidenceItem, EvidenceRelation


@pytest.fixture(autouse=True)
def isolated_mimo_settings(monkeypatch):
    for name in ("MIMO_REQUEST_TIMEOUT_SECONDS", "MIMO_TOTAL_TIMEOUT_SECONDS", "MIMO_MAX_COMPLETION_TOKENS", "MIMO_MAX_CONCURRENCY"):
        monkeypatch.delenv(name, raising=False)


def completion(content=None, *, finish="stop"):
    if content is None:
        content = json.dumps({"decisions": [{
            "evidence_id": "e_1", "relation": "supports", "excerpt": "Paris is in France.", "reason": "Evidence agrees."
        }]})
    return httpx.Response(200, json={"choices": [{"finish_reason": finish, "message": {"content": content}}]})


def sample():
    claim = Claim(claim_id="c_test", task_id="t_test", source_text="Paris is in France.", normalized_claim="Paris is in France.", char_start=0, char_end=19)
    return claim, [EvidenceCluster(cluster_id="ec_1", items=[EvidenceItem(evidence_id="e_1", excerpt="Paris is in France.")])]


@pytest.mark.parametrize("failure", [503, 429, "timeout", "connection"])
def test_transient_failure_recovers_and_applies_only_valid_evidence(failure):
    requests, sleeps = [], []
    def handler(request):
        requests.append(request)
        if len(requests) == 1:
            if failure == "timeout":
                raise httpx.ReadTimeout("private detail")
            if failure == "connection":
                raise httpx.ConnectError("private detail")
            return httpx.Response(failure, headers={"Retry-After": "0"})
        return completion()
    judge = mimo.MiMoEvidenceJudge(api_key="test", transport=httpx.MockTransport(handler), sleep=sleeps.append)
    claim, clusters = sample()
    judge.judge(claim, clusters)
    assert len(requests) == 2 and len(sleeps) == 1
    assert clusters[0].items[0].relation == EvidenceRelation.SUPPORTS


@pytest.mark.parametrize("bad_response,expected_code", [
    (lambda: completion('not-json-private-content'), "LLM_JSON"),
    (lambda: completion('{"decisions":[{"evidence_id":"e_1"}]}'), "LLM_SCHEMA"),
    (lambda: completion(""), "LLM_EMPTY"),
    (lambda: httpx.Response(200, json={"choices": []}), "LLM_RESPONSE"),
    (lambda: completion('{"decisions":[', finish="length"), "LLM_TRUNCATED"),
    (lambda: completion(finish="repetition_truncation"), "LLM_REPETITION"),
])
def test_output_failure_gets_one_clean_regeneration(bad_response, expected_code, caplog):
    requests = []
    def handler(request):
        requests.append(json.loads(request.content))
        return bad_response() if len(requests) == 1 else completion()
    judge = mimo.MiMoEvidenceJudge(api_key="test", transport=httpx.MockTransport(handler), sleep=lambda _: None)
    original_messages = [{"role": "user", "content": "original input"}]
    result = judge._request(original_messages)
    assert len(result.decisions) == 1 and len(requests) == 2
    assert len(original_messages) == 1
    assert "not-json-private-content" not in json.dumps(requests) + caplog.text
    assert expected_code in caplog.text
    if expected_code == "LLM_TRUNCATED":
        assert requests[0]["max_completion_tokens"] < requests[1]["max_completion_tokens"] <= mimo.MAX_RECOVERY_TOKENS


def test_repeated_invalid_output_stops_after_one_regeneration():
    requests = []
    def handler(request):
        requests.append(request)
        return completion("invalid")
    judge = mimo.MiMoEvidenceJudge(api_key="test", transport=httpx.MockTransport(handler), sleep=lambda _: None)
    with pytest.raises(JudgmentProviderError) as caught:
        judge._request([])
    assert caught.value.code == "LLM_JSON"
    assert len(requests) == 2


def test_mixed_failures_still_have_at_most_three_attempts():
    requests = []
    def handler(request):
        requests.append(request)
        return completion("invalid") if len(requests) == 2 else httpx.Response(503)
    judge = mimo.MiMoEvidenceJudge(api_key="test", transport=httpx.MockTransport(handler), sleep=lambda _: None)
    with pytest.raises(JudgmentProviderError):
        judge._request([])
    assert len(requests) == 3


@pytest.mark.parametrize("refusal", [False, True])
def test_content_filter_is_never_retried_or_accepted(refusal):
    calls = []
    def handler(request):
        calls.append(request)
        response = completion(finish="stop" if refusal else "content_filter")
        if refusal:
            body = response.json()
            body["choices"][0]["message"]["refusal"] = "provider refusal"
            return httpx.Response(200, json=body)
        return response
    judge = mimo.MiMoEvidenceJudge(api_key="test", transport=httpx.MockTransport(handler), sleep=lambda _: pytest.fail("must not retry"))
    claim, clusters = sample()
    with pytest.raises(JudgmentProviderError) as caught:
        judge.judge(claim, clusters)
    assert caught.value.code == "LLM_CONTENT_FILTER"
    assert len(calls) == 1
    assert clusters[0].items[0].relation == EvidenceRelation.UNKNOWN


def test_markdown_fence_is_removed_but_evidence_quotes_are_still_validated():
    content = json.dumps({"decisions": [{"evidence_id": "e_1", "relation": "supports", "excerpt": "invented quote", "reason": "test"}]})
    judge = mimo.MiMoEvidenceJudge(api_key="test", transport=httpx.MockTransport(lambda _: completion("```json\n" + content + "\n```")))
    claim, clusters = sample()
    judge.judge(claim, clusters)
    assert clusters[0].items[0].relation == EvidenceRelation.UNKNOWN


def test_total_budget_is_shared_by_requests_and_backoff(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(mimo.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(mimo, "_retry_delay", lambda *_: 1.0)
    judge = mimo.MiMoEvidenceJudge(api_key="test", sleep=lambda delay: clock.__setitem__(0, clock[0] + delay))
    timeouts = []
    def post(headers, body, *, timeout_seconds):
        timeouts.append(timeout_seconds)
        clock[0] += timeout_seconds
        raise httpx.ReadTimeout("test")
    monkeypatch.setattr(judge, "_post", post)
    with pytest.raises(JudgmentProviderError) as caught:
        judge._request([])
    assert caught.value.code == "LLM_TIMEOUT"
    assert timeouts == [60.0, 59.0]
    assert clock[0] == 120.0


def test_task_remaining_budget_restricts_llm_request(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(mimo.time, "monotonic", lambda: clock[0])
    judge = mimo.MiMoEvidenceJudge(api_key="test", sleep=lambda _: pytest.fail("budget exhausted"))
    timeouts = []
    def post(headers, body, *, timeout_seconds):
        timeouts.append(timeout_seconds)
        clock[0] += timeout_seconds
        raise httpx.ReadTimeout("test")
    monkeypatch.setattr(judge, "_post", post)
    with pytest.raises(JudgmentProviderError):
        judge._request([], deadline=107.0)
    assert timeouts == [7.0]


def test_retry_after_longer_than_budget_does_not_retry_early():
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(429, headers={"Retry-After": "600"})
    judge = mimo.MiMoEvidenceJudge(api_key="test", transport=httpx.MockTransport(handler), sleep=lambda _: pytest.fail("must not sleep past budget"))
    with pytest.raises(JudgmentProviderError) as caught:
        judge._request([])
    assert caught.value.code == "LLM_RATE_LIMIT" and len(calls) == 1


def test_retry_after_supports_dates_and_invalid_values():
    when = datetime.now(timezone.utc) + timedelta(seconds=30)
    delay = mimo._retry_delay(httpx.Headers({"Retry-After": format_datetime(when, usegmt=True)}), 0)
    assert 28 < delay <= 30
    for value in ("NaN", "inf", "bad date"):
        assert 1 <= mimo._retry_delay(httpx.Headers({"Retry-After": value}), 0) <= 1.25


def test_concurrency_queue_has_a_deadline_and_releases_slot(monkeypatch):
    monkeypatch.setenv("MIMO_MAX_CONCURRENCY", "1")
    judge = mimo.MiMoEvidenceJudge(api_key="test")
    entered, release = threading.Event(), threading.Event()
    calls = []
    def request(*args, **kwargs):
        calls.append(1)
        entered.set()
        assert release.wait(2)
        return mimo._DecisionPayload(decisions=[])
    monkeypatch.setattr(judge, "_request", request)
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(judge.judge, *sample())
        try:
            assert entered.wait(1)
            with pytest.raises(JudgmentProviderError) as caught:
                judge.judge_with_deadline(*sample(), deadline=time.monotonic() + 0.03)
            assert caught.value.code == "LLM_TIMEOUT"
            assert len(calls) == 1
        finally:
            release.set()
        first.result(timeout=1)
    judge.judge(*sample())
    assert len(calls) == 2


def test_configuration_is_bounded_and_rejects_nonfinite_values(monkeypatch):
    monkeypatch.setenv("MIMO_REQUEST_TIMEOUT_SECONDS", "NaN")
    monkeypatch.setenv("MIMO_TOTAL_TIMEOUT_SECONDS", "9999")
    monkeypatch.setenv("MIMO_MAX_COMPLETION_TOKENS", "99999")
    monkeypatch.setenv("MIMO_MAX_CONCURRENCY", "0")
    judge = mimo.MiMoEvidenceJudge(api_key="test")
    assert judge._request_timeout == 60
    assert judge._total_timeout == 180
    assert judge._max_tokens == 3600


def test_pipeline_forwards_task_deadline_to_bounded_judge(tmp_path, monkeypatch):
    from app.config import Settings
    from app.pipeline import Pipeline
    from app.storage import Storage
    class Retriever:
        provider = "mock"
        def retrieve(self, claim):
            return sample()[1]
    class Judge:
        provider = "mimo"
        deadlines = []
        def judge_with_deadline(self, claim, clusters, *, deadline):
            self.deadlines.append(deadline)
        def judge(self, claim, clusters):
            pytest.fail("must use the bounded method")
    storage = Storage(":memory:")
    judge = Judge()
    pipeline = Pipeline(storage, Settings(database_path=tmp_path / "unused.db", task_timeout_seconds=10), retriever=Retriever(), evidence_judge=judge)
    started = time.monotonic()
    task = pipeline.create("Paris is in France.", 1)
    pipeline.run_sync(task.task_id)
    claim = storage.list_claims(task.task_id)[0][0]
    pipeline.retry_sync(claim.claim_id)
    assert len(judge.deadlines) == 2
    assert all(started + 10 <= deadline <= time.monotonic() + 10 for deadline in judge.deadlines)
    storage.close()
