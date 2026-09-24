"""Deterministic offline providers used by default and in tests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

from .base import SearchResult


@dataclass
class DeterministicMockSearchProvider:
    """Exact normalized-query fixture lookup; it never performs network I/O."""

    fixtures: dict[str, Sequence[SearchResult]] | None = None
    name: str = "mock"

    def search(self, query: str, *, limit: int = 5) -> Sequence[SearchResult]:
        if limit < 0:
            return []
        key = " ".join(query.casefold().split())
        rows = (self.fixtures or {}).get(key, ())
        return list(rows[:limit])


def default_mock_provider() -> DeterministicMockSearchProvider:
    return DeterministicMockSearchProvider(fixtures={})
