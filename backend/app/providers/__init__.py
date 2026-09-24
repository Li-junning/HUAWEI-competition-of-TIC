"""Provider selection is fail-closed for live mode."""

from __future__ import annotations

import os

from .base import EvidenceJudge, LiveProviderNotConfigured, PaperProvider, ProviderError, SearchProvider, SearchResult
from .mock import DeterministicMockSearchProvider, default_mock_provider


def get_search_provider(mode: str | None = None) -> SearchProvider:
    selected = (mode or os.getenv("VERIFIER_PROVIDER_MODE", "mock")).strip().lower()
    if selected in {"", "mock", "offline"}:
        return default_mock_provider()
    if selected == "tavily":
        from .tavily import TavilySearchProvider
        return TavilySearchProvider()
    raise LiveProviderNotConfigured("live search provider is not configured")


def get_evidence_judge(mode: str | None = None) -> EvidenceJudge | None:
    selected = (mode or os.getenv("VERIFIER_JUDGE_MODE", "off")).strip().lower()
    if selected in {"", "off", "mock", "offline"}:
        return None
    if selected == "mimo":
        from .mimo import MiMoEvidenceJudge
        return MiMoEvidenceJudge()
    raise LiveProviderNotConfigured("requested evidence judge is not configured")


__all__ = [
    "DeterministicMockSearchProvider",
    "EvidenceJudge",
    "LiveProviderNotConfigured",
    "PaperProvider",
    "ProviderError",
    "SearchProvider",
    "SearchResult",
    "get_search_provider",
    "get_evidence_judge",
]
