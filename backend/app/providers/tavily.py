"""Bounded Tavily Search adapter.

The API key stays in the server process environment.  Search content is still
untrusted data and is validated again by the retrieval layer before storage.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import random
import re
import time
from collections.abc import Callable
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlsplit

import httpx

from .base import LiveProviderNotConfigured, ProviderError, SearchProviderError, SearchResult
from .network import network_permission_denied


MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_RETRIES = 2
REQUEST_TIMEOUT_SECONDS = 20.0
TOTAL_TIMEOUT_SECONDS = 60.0


class _DeadlineExceeded(TimeoutError):
    pass


class TavilySearchProvider:
    """Search provider using only Tavily's fixed HTTPS endpoint.

    It deliberately does not fetch result URLs itself.  Tavily's returned text
    flows into the existing evidence validation and is not treated as a command.
    """

    name = "tavily"
    endpoint = "https://api.tavily.com/search"

    def __init__(
        self,
        api_key: str | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        search_depth: str | None = None,
    ) -> None:
        self._api_key = api_key or os.getenv("SEARCH_API_KEY")
        if not self._api_key:
            raise LiveProviderNotConfigured("Tavily search key is not configured")
        self._transport = transport
        self._sleep = sleep
        depth = search_depth or os.getenv("TAVILY_SEARCH_DEPTH", "advanced")
        self._search_depth = depth if depth in {"basic", "advanced"} else "advanced"

    def search(self, query: str, *, limit: int = 5, deadline: float | None = None) -> list[SearchResult]:
        return asyncio.run(self._search_async(query, limit=limit, deadline=deadline))

    async def _search_async(self, query: str, *, limit: int, deadline: float | None) -> list[SearchResult]:
        cleaned_query = " ".join(query.split())
        if not cleaned_query:
            return []
        domains = []
        # Translate our source-discovery suffix into native API filters.
        # Boolean site operators in query text are not reliably enforced.
        scoped = re.fullmatch(r"(.+?)\s+\((site:[A-Za-z0-9.-]+(?: OR site:[A-Za-z0-9.-]+)*)\)", cleaned_query)
        if scoped:
            cleaned_query = scoped.group(1)
            domains = re.findall(r"site:([A-Za-z0-9.-]+)", scoped.group(2))[:5]
        # Bound each query so it leaves time for other searches and judgment.
        deadline = min(deadline if deadline is not None else math.inf,
                       time.monotonic() + TOTAL_TIMEOUT_SECONDS)
        payload = {
            "query": cleaned_query,
            "search_depth": self._search_depth,
            "max_results": min(max(limit, 1), 5),
            "include_answer": False,
            "include_images": False,
            "include_raw_content": "text",
        }
        if self._search_depth == "advanced":
            payload["chunks_per_source"] = 3
        if domains:
            payload["include_domains"] = domains
        headers = {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}

        for attempt in range(MAX_RETRIES + 1):
            remaining = _remaining(deadline)
            if remaining is not None and remaining <= 0:
                raise SearchProviderError("SEARCH_TIMEOUT")
            try:
                remaining = _remaining(deadline)
                if remaining is None or remaining <= 0:
                    raise _DeadlineExceeded()
                status, response_headers, raw_body = await asyncio.wait_for(
                    self._post(headers, payload), timeout=min(REQUEST_TIMEOUT_SECONDS, remaining))
            except (asyncio.TimeoutError, _DeadlineExceeded, httpx.TimeoutException) as exc:
                if isinstance(exc, (_DeadlineExceeded, asyncio.TimeoutError)) or (_remaining(deadline) is not None and _remaining(deadline) <= 0):
                    raise SearchProviderError("SEARCH_TIMEOUT") from exc
                if attempt < MAX_RETRIES:
                    if not await self._sleep_before_deadline(_backoff(attempt), deadline):
                        raise SearchProviderError("SEARCH_TIMEOUT") from exc
                    continue
                raise SearchProviderError("SEARCH_TIMEOUT") from exc
            except httpx.HTTPError as exc:
                if network_permission_denied(exc):
                    raise SearchProviderError("SEARCH_NETWORK_PERMISSION") from exc
                if attempt < MAX_RETRIES:
                    if not await self._sleep_before_deadline(_backoff(attempt), deadline):
                        raise SearchProviderError("SEARCH_CONNECTION") from exc
                    continue
                raise SearchProviderError("SEARCH_CONNECTION") from exc

            if 200 <= status < 300:
                return self._parse_results(raw_body)
            if status == 429 or 500 <= status < 600:
                if attempt < MAX_RETRIES:
                    if await self._sleep_before_deadline(_retry_delay(response_headers, attempt), deadline):
                        continue
                raise SearchProviderError("SEARCH_RATE_LIMIT" if status == 429 else "SEARCH_UNAVAILABLE")
            # Do not reveal provider response text, key material, or request details.
            code = {401: "SEARCH_AUTH", 403: "SEARCH_FORBIDDEN", 432: "SEARCH_QUOTA", 433: "SEARCH_QUOTA"}.get(status, "SEARCH_REJECTED")
            raise SearchProviderError(code)

        raise ProviderError("Tavily search request failed")

    async def _post(self, headers: dict[str, str], payload: dict[str, Any]) -> tuple[int, httpx.Headers, bytes]:
        timeout = httpx.Timeout(REQUEST_TIMEOUT_SECONDS)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False, transport=self._transport) as client:
            async with client.stream("POST", self.endpoint, headers=headers, json=payload) as response:
                declared_length = response.headers.get("content-length")
                if declared_length and declared_length.isdigit() and int(declared_length) > MAX_RESPONSE_BYTES:
                    raise ProviderError("Tavily response exceeded the configured size limit")
                chunks = bytearray()
                async for chunk in response.aiter_bytes():
                    chunks.extend(chunk)
                    if len(chunks) > MAX_RESPONSE_BYTES:
                        raise ProviderError("Tavily response exceeded the configured size limit")
                return response.status_code, response.headers, bytes(chunks)

    async def _sleep_before_deadline(self, delay: float, deadline: float | None) -> bool:
        remaining = _remaining(deadline)
        if remaining is not None:
            # Never shorten a provider's Retry-After to force an early retry.
            if remaining <= delay:
                return False
        if self._sleep is time.sleep:
            await asyncio.sleep(delay)
        else:
            self._sleep(delay)
        return True

    @staticmethod
    def _parse_results(raw_body: bytes) -> list[SearchResult]:
        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProviderError("Tavily returned an invalid response") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
            raise ProviderError("Tavily returned an unexpected response")

        results: list[SearchResult] = []
        for item in payload["results"]:
            if not isinstance(item, dict):
                continue
            url = item.get("url")
            if not isinstance(url, str) or not url.strip():
                continue
            raw_content = item.get("raw_content")
            snippet = item.get("content") if isinstance(item.get("content"), str) else ""
            content = raw_content if isinstance(raw_content, str) and raw_content.strip() else snippet
            results.append(
                SearchResult(
                    url=url,
                    title=item.get("title") if isinstance(item.get("title"), str) else "",
                    snippet=snippet,
                    content=content or None,
                    publisher=urlsplit(url).hostname,
                    published_at=item.get("published_date") if isinstance(item.get("published_date"), str) else None,
                    metadata={
                        "provider_score": item.get("score"),
                        "content_kind": "raw_text" if content and content == raw_content else "search_snippet",
                    },
                )
            )
        return results


def _remaining(deadline: float | None) -> float | None:
    return None if deadline is None else deadline - time.monotonic()


def _retry_delay(headers: httpx.Headers, attempt: int) -> float:
    retry_after = headers.get("retry-after")
    try:
        if retry_after is not None:
            seconds = float(retry_after)
            if math.isfinite(seconds):
                return max(seconds, 0.0)
    except ValueError:
        try:
            when = parsedate_to_datetime(retry_after)
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())
        except (ValueError, TypeError, OverflowError):
            pass
    return _backoff(attempt)


def _backoff(attempt: int) -> float:
    return min(0.5 * (2**attempt), 4.0) + random.uniform(0.0, 0.25)
