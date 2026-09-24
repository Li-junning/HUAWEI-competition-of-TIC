import asyncio
import json
import time
import unittest
from threading import Event
from unittest.mock import patch

import httpx

from app.providers.base import LiveProviderNotConfigured, ProviderError
from app.providers.tavily import TavilySearchProvider


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
