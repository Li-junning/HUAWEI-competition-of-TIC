import json
import unittest
from unittest.mock import patch

import httpx

from app.providers.base import LiveProviderNotConfigured, ProviderError
from app.providers.mimo import MiMoEvidenceJudge
from app.schemas import Claim, EvidenceCluster, EvidenceItem, EvidenceRelation


def _claim_and_clusters():
    claim = Claim(claim_id="c_1", task_id="t_1", source_text="X", char_start=0, char_end=1, normalized_claim="中国的首都是上海")
    item = EvidenceItem(evidence_id="e_1", url="https://example.com/source", excerpt="中华人民共和国首都是北京。")
    return claim, [EvidenceCluster(cluster_id="ec_1", items=[item])]


class MiMoJudgeTests(unittest.TestCase):
    def test_missing_key_fails_closed(self):
        with patch.dict("os.environ", {"MIMO_API_KEY": "", "MODEL_API_KEY": ""}), self.assertRaises(LiveProviderNotConfigured):
            MiMoEvidenceJudge(api_key="")

    def test_valid_structured_refutation_is_applied(self):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["headers"] = request.headers
            captured["body"] = json.loads(request.content)
            content = json.dumps({"decisions": [{
                "evidence_id": "e_1", "relation": "refutes", "excerpt": "首都是北京", "reason": "证据明确给出不同首都。"
            }]})
            return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

        claim, clusters = _claim_and_clusters()
        judge = MiMoEvidenceJudge(api_key="sk-test", transport=httpx.MockTransport(handler))
        judge.judge(claim, clusters)
        item = clusters[0].items[0]
        self.assertEqual(item.relation, EvidenceRelation.REFUTES)
        self.assertEqual(item.relevance, 0.9)
        self.assertEqual(captured["headers"]["api-key"], "sk-test")
        self.assertEqual(captured["body"]["response_format"], {"type": "json_object"})
        self.assertEqual(captured["body"]["thinking"], {"type": "disabled"})

    def test_invented_excerpt_is_rejected(self):
        def handler(request: httpx.Request) -> httpx.Response:
            content = json.dumps({"decisions": [{
                "evidence_id": "e_1", "relation": "refutes", "excerpt": "虚构句子", "reason": "不应采纳。"
            }]})
            return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

        claim, clusters = _claim_and_clusters()
        MiMoEvidenceJudge(api_key="sk-test", transport=httpx.MockTransport(handler)).judge(claim, clusters)
        self.assertEqual(clusters[0].items[0].relation, EvidenceRelation.UNKNOWN)

    def test_invalid_base_url_is_rejected(self):
        with self.assertRaises(LiveProviderNotConfigured):
            MiMoEvidenceJudge(api_key="sk-test", base_url="https://example.com/v1")


if __name__ == "__main__":
    unittest.main()
