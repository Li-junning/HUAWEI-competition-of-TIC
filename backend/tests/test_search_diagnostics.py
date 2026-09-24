import errno

import httpx
import pytest

from app.config import Settings
from app.pipeline import Pipeline
from app.providers.base import SearchProviderError
from app.providers.tavily import TavilySearchProvider
from app.retrieve import SearchBackedRetriever
from app.schemas import ClaimState, TaskStatus
from app.storage import Storage


@pytest.mark.parametrize("status,code,attempts", [
    (401, "SEARCH_AUTH", 1),
    (403, "SEARCH_FORBIDDEN", 1),
    (432, "SEARCH_QUOTA", 1),
    (433, "SEARCH_QUOTA", 1),
    (429, "SEARCH_RATE_LIMIT", 3),
    (503, "SEARCH_UNAVAILABLE", 3),
    (422, "SEARCH_REJECTED", 1),
])
def test_http_failure_keeps_category_without_exposing_body(status, code, attempts):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(status, text="sensitive upstream response tvly-secret")

    provider = TavilySearchProvider(api_key="tvly-secret", transport=httpx.MockTransport(handler), sleep=lambda _: None)
    with pytest.raises(SearchProviderError) as error:
        provider.search("test")
    assert error.value.code == code
    assert "sensitive" not in error.value.public_message
    assert "tvly-secret" not in str(error.value)
    assert len(requests) == attempts


@pytest.mark.parametrize("windows", [False, True])
def test_permission_failure_survives_retrieval_pipeline_and_retry(windows, tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("VERIFIER_JUDGE_MODE", "off")
    calls = []

    def handler(request):
        calls.append(request)
        denied = PermissionError(errno.EACCES, "sensitive operating system detail")
        if windows:
            denied.winerror = 10013
        try:
            raise denied
        except PermissionError as exc:
            raise httpx.ConnectError("tvly-secret", request=request) from exc

    provider = TavilySearchProvider(api_key="tvly-secret", transport=httpx.MockTransport(handler), sleep=lambda _: None)
    storage = Storage(":memory:")
    pipeline = Pipeline(storage, Settings(database_path=tmp_path / "unused.db"), retriever=SearchBackedRetriever(provider))
    task = pipeline.create("Paris is the capital of France.", 1)
    summary = pipeline.run_sync(task.task_id)
    assert summary.status == TaskStatus.PARTIAL
    claim = storage.list_claims(task.task_id)[0][0]
    assert claim.state == ClaimState.FAILED
    assert claim.label is None
    assert "SEARCH_NETWORK_PERMISSION" in claim.reason
    assert summary.failed_providers == ["tavily"]
    pipeline.retry_sync(claim.claim_id)
    assert "SEARCH_NETWORK_PERMISSION" in storage.get_claim(claim.claim_id).reason
    assert len(calls) == 2  # Permission denials are not transient transport errors.
    assert "SEARCH_NETWORK_PERMISSION" in caplog.text
    assert "tvly-secret" not in caplog.text + claim.reason
    assert "sensitive" not in caplog.text + claim.reason
    storage.close()


@pytest.mark.parametrize("error_type,code", [
    (httpx.ReadTimeout, "SEARCH_TIMEOUT"),
    (httpx.ConnectError, "SEARCH_CONNECTION"),
])
def test_transient_network_errors_remain_bounded(error_type, code):
    calls = []

    def handler(request):
        calls.append(request)
        raise error_type("sensitive detail", request=request)

    provider = TavilySearchProvider(api_key="tvly-test", transport=httpx.MockTransport(handler), sleep=lambda _: None)
    with pytest.raises(SearchProviderError) as error:
        provider.search("test")
    assert error.value.code == code
    assert len(calls) == 3
