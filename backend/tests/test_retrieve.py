import unittest

from app.providers import DeterministicMockSearchProvider
from app.providers.base import SearchResult
from app.retrieve import (
    SearchBackedRetriever,
    build_queries,
    candidate_evidence_text,
    cluster_evidence,
    content_hash,
    fetch_result,
)
from app.schemas import Claim
from app.security import SafeHttpClient


class RetrieveTests(unittest.TestCase):
    def test_query_is_bounded_and_mock_is_deterministic(self):
        queries = build_queries("c1", entities=["机构"], action="发布", time_range="2024", value="10%", conditions=["口径"])
        self.assertLessEqual(len(queries), 3)
        provider = DeterministicMockSearchProvider({"机构 发布 2024 10% 口径": [SearchResult("https://example.com", content="fact")]})
        self.assertEqual(len(provider.search(queries[0].query)), 1)
        self.assertEqual(provider.search(queries[0].query), provider.search(queries[0].query))

    def test_provider_content_is_bounded_plain_text_and_clustered(self):
        one = SearchResult("https://example.com/a", title="Story", content="Same article")
        two = SearchResult("https://example.com/b", title="Story copy", content="Same article")
        a = fetch_result(one, client=SafeHttpClient(), evidence_id="e1", retrieved_at="2026-01-01T00:00:00Z")
        b = fetch_result(two, client=SafeHttpClient(), evidence_id="e2", retrieved_at="2026-01-01T00:00:00Z")
        clusters = cluster_evidence([a, b])
        self.assertEqual(len(clusters), 1)
        self.assertTrue(b.is_reprint)
        self.assertEqual(content_hash("Same article"), a.content_hash)
        self.assertEqual(candidate_evidence_text([a, b])["e1"], "Same article")

    def test_search_backed_retriever_returns_untrusted_evidence_without_judging(self):
        provider = DeterministicMockSearchProvider({"示例事实": [
            SearchResult("https://example.com/fact", title="Source", content="示例事实的证据正文")
        ]})
        claim = Claim(claim_id="c_1", task_id="t_1", source_text="示例事实", char_start=0, char_end=4, normalized_claim="示例事实")
        clusters = SearchBackedRetriever(provider).retrieve(claim)
        self.assertEqual(claim.queries, ["示例事实"])
        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0].items[0].relation.value, "unknown")

    def test_source_authority_is_conservative_for_encyclopedias_and_high_for_official_sources(self):
        provider = DeterministicMockSearchProvider({"事实": [
            SearchResult("https://example.gov.cn/fact", content="事实的官方正文"),
            SearchResult("https://zh.wikipedia.org/wiki/Test", content="事实的百科正文"),
        ]})
        claim = Claim(claim_id="c_2", task_id="t_1", source_text="事实", char_start=0, char_end=2, normalized_claim="事实")
        clusters = SearchBackedRetriever(provider).retrieve(claim)
        authority = {item.url: item.authority for cluster in clusters for item in cluster.items}
        self.assertEqual(authority["https://example.gov.cn/fact"], 0.9)
        self.assertEqual(authority["https://zh.wikipedia.org/wiki/Test"], 0.45)


if __name__ == "__main__":
    unittest.main()
