import asyncio
import json
import time
import unittest
from threading import Event
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from unittest.mock import patch

import httpx

from app.providers.base import LiveProviderNotConfigured, ProviderError
from app.providers.tavily import TavilySearchProvider, _retry_delay


class TavilyProviderTests(unittest.TestCase):
    def test_missing_key_fails_closed(self):
        with patch.dict("os.environ", {"SEARCH_API_KEY": ""}), self.assertRaises(LiveProviderNotConfigured):
            TavilySearchProvider(api_key="")

    def test_maps_only_expected_result_fields(self):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["authorization"] = request.headers.get("Authorization")
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json={"results": [{
                "title": "Official page",
                "url": "https://example.org/fact",
                "content": "short snippet",
                "raw_content": "full plain text",
                "score": 0.9,
            }]})

        provider = TavilySearchProvider(api_key="tvly-test", transport=httpx.MockTransport(handler))
        result = provider.search("  test query  ", limit=99)
        self.assertEqual(captured["authorization"], "Bearer tvly-test")
        self.assertEqual(captured["body"]["max_results"], 5)
        self.assertEqual(captured["body"]["include_raw_content"], "text")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].content, "full plain text")
        self.assertEqual(result[0].metadata["content_kind"], "raw_text")

    def test_rejected_response_has_sanitized_error(self):
        provider = TavilySearchProvider(
            api_key="tvly-test",
            transport=httpx.MockTransport(lambda request: httpx.Response(401, text="do not expose this")),
        )
        with self.assertRaises(ProviderError) as raised:
            provider.search("test")
        self.assertNotIn("do not expose", str(raised.exception))

    def test_rate_limit_waits_for_provider_retry_after(self):
        calls = []
        sleeps = []
        def handler(request):
            calls.append(request)
            if len(calls) == 1:
                return httpx.Response(429, headers={"Retry-After": "2"})
            return httpx.Response(200, json={"results": []})
        provider = TavilySearchProvider(
            api_key="tvly-test", transport=httpx.MockTransport(handler), sleep=sleeps.append)
        self.assertEqual(provider.search("test"), [])
        self.assertEqual(len(calls), 2)
        self.assertEqual(sleeps, [2.0])

    def test_rate_limit_does_not_retry_before_retry_after(self):
        calls = []
        provider = TavilySearchProvider(
            api_key="tvly-test",
            transport=httpx.MockTransport(
                lambda request: (calls.append(request) or httpx.Response(
                    429, headers={"Retry-After": "600"}))),
            sleep=lambda _: self.fail("must not sleep beyond the budget"))
        with self.assertRaises(ProviderError) as raised:
            provider.search("test")
        self.assertEqual(raised.exception.code, "SEARCH_RATE_LIMIT")
        self.assertEqual(len(calls), 1)

    def test_retry_after_date_and_invalid_values(self):
        when = datetime.now(timezone.utc) + timedelta(seconds=30)
        delay = _retry_delay(httpx.Headers({"Retry-After": format_datetime(when, usegmt=True)}), 0)
        self.assertGreater(delay, 28)
        self.assertLessEqual(delay, 30)
        for value in ("NaN", "inf", "bad date"):
            self.assertGreaterEqual(_retry_delay(httpx.Headers({"Retry-After": value}), 0), 0.5)

    def test_absolute_deadline_bounds_slow_trickle_request(self):
        cancelled = Event()

        class SlowBody(httpx.AsyncByteStream):
            async def __aiter__(self):
                try:
                    await asyncio.sleep(0.15)
                    yield b'{"results": []}'
                finally:
                    cancelled.set()

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, stream=SlowBody())

        provider = TavilySearchProvider(api_key="tvly-test", transport=httpx.MockTransport(handler))
        started = time.monotonic()
        with self.assertRaises(ProviderError) as raised:
            provider.search("test", deadline=started + 0.03)
        self.assertEqual(getattr(raised.exception, "code", None), "SEARCH_TIMEOUT")
        self.assertLess(time.monotonic() - started, 0.12)
        self.assertTrue(cancelled.wait(0.1), "timed out response body should be cancelled and closed")


if __name__ == "__main__":
    unittest.main()
