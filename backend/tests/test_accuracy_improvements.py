"""Grounded coverage must improve without making missing facts into answers."""

import json

import httpx
import pytest

from app.extract import extract_claims
from app.judge import judge_claim
from app.providers.base import SearchResult
from app.providers.mimo import MiMoEvidenceJudge, _claim_parts, _DecisionPayload
from app.providers.tavily import TavilySearchProvider
from app.retrieve import SearchBackedRetriever, _multi_target_excerpt, build_queries
from app.schemas import Claim, ClaimLabel, EvidenceCluster, EvidenceItem, EvidenceRelation
from app.scoring import support_score
from app.source_policy import source_profile
from app.storage import Storage
from app.security import ground_evidence_quote


MATCH = dict(entity="match", predicate="match", scope="match", value="match")


def verified(statement, sources):
    claim = Claim(claim_id="c", task_id="t", source_text=statement, normalized_claim=statement,
                  char_start=0, char_end=len(statement))
    parts = _claim_parts(statement)
    clusters, decisions = [], []
    for index, (url, body, checks, snippet) in enumerate(sources):
        eid = f"e{index}"
        clusters.append(EvidenceCluster(cluster_id=f"ec{index}", items=[EvidenceItem(
            evidence_id=eid, url=url, excerpt=body, authority=source_profile(url).authority,
            quality_reason="搜索结果摘要" if snippet else "网页正文")]))
        decisions.append(dict(evidence_id=eid, relation="partially_supports", excerpt=body,
                              reason="核对。", checks=[dict(
            part_id=pid, relation=relation, excerpt=quote,
            alignment={**MATCH, "value": "conflict" if relation == "refutes" else "match"},
        ) for pid, relation, quote in checks]))
    MiMoEvidenceJudge._apply_validated_decisions(_DecisionPayload(decisions=decisions),
        {item.evidence_id: item.excerpt for cluster in clusters for item in cluster.items},
        clusters, parts=parts, whole_claim=statement)
    return judge_claim(claim, clusters)


def test_separate_pages_cover_each_fact_and_score_survives_storage(tmp_path):
    first, second = "甲公司成立于2010年。", "甲公司位于上海。"
    result = verified("甲公司成立于2010年，它位于上海。", [
        ("https://a.gov.cn/history", first, [(1, "supports", first)], False),
        ("https://b.edu.cn/location", second, [(2, "supports", second)], False),
    ])
    assert result.label == ClaimLabel.CREDIBLE
    assert all(item.relation == EvidenceRelation.PARTIALLY_SUPPORTS
               for cluster in result.evidence_clusters for item in cluster.items)
    assert "分别证明" in result.reason
    assert support_score(result) == 88
    storage = Storage(tmp_path / "checks.sqlite3")
    try:
        storage.save_claim(result)
        restored = storage.get_claim(result.claim_id)
        assert restored.evidence_clusters[1].items[0].checks[0].part_text == second
        assert support_score(restored) == support_score(result)
    finally:
        storage.close()


def test_missing_second_fact_still_cannot_be_covered_by_many_first_fact_sources():
    body = "甲公司成立于2010年。"
    result = verified("甲公司成立于2010年，它位于上海。", [
        (f"https://source{i}.gov.cn/fact", body, [(1, "supports", body)], False) for i in range(3)
    ])
    assert result.label == ClaimLabel.EVIDENCE_INSUFFICIENT
    assert support_score(result) is None


def test_checks_for_an_edited_fact_are_not_reused():
    first, second = "甲公司成立于2010年。", "甲公司位于上海。"
    result = verified("甲公司成立于2010年，它位于上海。", [
        ("https://a.gov.cn/a", first, [(1, "supports", first)], False),
        ("https://b.gov.cn/b", second, [(2, "supports", second)], False),
    ])
    result.normalized_claim = "甲公司成立于2010年，它位于北京。"
    assert judge_claim(result, result.evidence_clusters).label == ClaimLabel.EVIDENCE_INSUFFICIENT


@pytest.mark.parametrize("relation,label", [("supports", ClaimLabel.CREDIBLE), ("refutes", ClaimLabel.INCORRECT)])
@pytest.mark.parametrize("domain", ["source.gov.cn", "source.edu.cn", "kepu.gmw.cn"])
def test_one_institutional_snippet_with_validated_direct_alignment_can_decide(domain, relation, label):
    statement = "甲公司成立于2010年。"
    body = statement if relation == "supports" else "甲公司成立于1998年。"
    result = verified(statement, [(f"https://{domain}/history", body, [(1, relation, body)], True)])
    assert result.label == label
    assert "尚未取得可核对的网页全文" in result.reason


def test_unknown_snippet_stays_below_single_source_threshold():
    body = "甲公司成立于2010年。"
    result = verified(body, [("https://unknown.example/fact", body, [(1, "supports", body)], True)])
    assert result.label == ClaimLabel.EVIDENCE_INSUFFICIENT


def test_refuting_one_part_still_defeats_several_other_supported_parts():
    first, second = "甲公司成立于2010年。", "甲公司位于北京，而非上海。"
    result = verified("甲公司成立于2010年，它位于上海。", [
        ("https://a.gov.cn/a", first, [(1, "supports", first)], False),
        ("https://b.gov.cn/b", second, [(2, "refutes", second)], False),
    ])
    assert result.label == ClaimLabel.INCORRECT


def test_direct_checks_are_used_when_model_whole_relation_is_unknown():
    body = "甲公司成立于2010年。"
    item = EvidenceItem(evidence_id="e", excerpt=body, authority=.9)
    clusters = [EvidenceCluster(cluster_id="ec", items=[item])]
    payload = _DecisionPayload(decisions=[dict(evidence_id="e", relation="unknown", reason="整句未确认。", checks=[
        dict(part_id=1, relation="supports", excerpt=body, alignment=MATCH)])])
    MiMoEvidenceJudge._apply_validated_decisions(payload, {"e": body}, clusters, parts=[body])
    assert item.relation == EvidenceRelation.SUPPORTS


@pytest.mark.parametrize("statement,body,quote", [
    ("甲公司成立于2010年。", "甲公司于2010年5月8日成立。", "甲公司于2010年5月8日成立"),
    ("声音可以在空气中传播。", "声音不能在真空中传播，但声音可以在空气中传播。", "声音可以在空气中传播"),
    ("在标准大气压下，水的沸点是100摄氏度。", "在1个标准大气压下，水的沸点是100摄氏度。", "在1个标准大气压下，水的沸点是100摄氏度。"),
])
def test_equivalent_wording_and_unrelated_clause_polarity_do_not_trigger_abstention(statement, body, quote):
    parts = _claim_parts(statement)
    result = verified(statement, [("https://a.gov.cn/fact", body,
        [(index + 1, "supports", quote) for index in range(len(parts))], False)])
    assert result.label == ClaimLabel.CREDIBLE


def test_clipped_negation_still_blocks_a_false_support():
    body = "声音不能在真空中传播，但声音可以在空气中传播。"
    result = verified("声音能在真空中传播。", [("https://a.gov.cn/fact", body,
        [(1, "supports", "能在真空中传播")], False)])
    assert result.label == ClaimLabel.EVIDENCE_INSUFFICIENT


def test_distant_properties_are_both_retained_as_original_passages():
    first, second = "甲公司成立于2010年。", "甲公司位于上海。"
    body = first + ("甲公司举办产品活动。" * 300) + second
    excerpt = _multi_target_excerpt(body, ["甲公司 成立时间", "甲公司 地址 地理位置"])
    assert first in excerpt and second in excerpt
    assert len(excerpt) < 3100
    assert all(passage in body for passage in excerpt.split("\n\n[另一处原文片段]\n\n"))


def test_compound_retrieval_covers_different_pages_from_one_publisher():
    first, second = "甲公司成立于2010年。", "甲公司位于上海。"
    class Fixture:
        name = "fixture"
        def search(self, query, *, limit=5):
            return [SearchResult("https://a.gov.cn/history", content=first),
                    SearchResult("https://a.gov.cn/location", content=second)]
    statement = "甲公司成立于2010年，它位于上海。"
    claim = Claim(claim_id="c", task_id="t", source_text=statement, normalized_claim=statement,
                  char_start=0, char_end=len(statement))
    clusters = SearchBackedRetriever(Fixture(), max_evidence=2).retrieve(claim)
    assert len(clusters) == 1
    assert {item.url for item in clusters[0].items} == {"https://a.gov.cn/history", "https://a.gov.cn/location"}


def test_category_query_does_not_presuppose_answer_and_keeps_subject():
    claim = extract_claims("鲸鱼是鱼类。", "t")[0][0]
    queries = build_queries("c", entities=claim.entities, action=claim.normalized_claim)
    assert queries[0].query == "鲸鱼是鱼类。"
    assert any("鲸鱼" in q.query and "鱼类" not in q.query for q in queries[1:])


@pytest.mark.parametrize("depth", ["advanced", "basic"])
def test_search_depth_payload_is_configurable(depth):
    bodies = []
    def respond(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"results": []})
    TavilySearchProvider("test", search_depth=depth, transport=httpx.MockTransport(respond)).search("目标属性")
    assert bodies[0]["search_depth"] == depth
    assert ("chunks_per_source" in bodies[0]) == (depth == "advanced")


@pytest.mark.parametrize("url", ["https://www.ipcc.ch/report", "https://www.imf.org/report", "https://thedocs.worldbank.org/report"])
def test_international_primary_sources_are_recognized(url):
    assert source_profile(url).kind == "official"
    assert source_profile(url + ".evil.example").kind == "official"  # path does not alter the host
    assert source_profile("https://ipcc.ch.evil.example/report").kind == "unknown"


def test_site_operators_are_sent_as_native_domain_filters():
    bodies = []
    def respond(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"results": []})
    TavilySearchProvider("test", transport=httpx.MockTransport(respond)).search(
        "水 沸点 (site:ac.cn OR site:cas.cn OR site:edu.cn)")
    assert bodies[0]["query"] == "水 沸点"
    assert bodies[0]["include_domains"] == ["ac.cn", "cas.cn", "edu.cn"]
    assert source_profile("https://www.iop.cas.cn/article").kind == "academic"


@pytest.mark.parametrize("body,quote", [
    ("水的沸点在标准大气压下是100摄氏度。", "水的沸点在标准大气压下是100摄氏度"),
    ("在标准大气压条件下，水的沸点是100摄氏度。", "在标准大气压条件下，水的沸点是100摄氏度"),
    ("通常说的沸点，都是指在1 标准大气压下。水的沸点是100 摄氏度。", "水的沸点是100 摄氏度"),
])
def test_same_condition_with_equivalent_wording_or_explicit_metric_definition(body, quote):
    statement = "在标准大气压下，水的沸点是100摄氏度。"
    result = verified(statement, [("https://a.edu.cn/fact", body,
        [(i + 1, "supports", quote) for i in range(len(_claim_parts(statement)))], False)])
    assert result.label == ClaimLabel.CREDIBLE


def test_different_entity_condition_cannot_be_inherited():
    body = "通常酒精的沸点是指在标准大气压下。水的沸点是100摄氏度。"
    statement = "在标准大气压下，水的沸点是100摄氏度。"
    result = verified(statement, [("https://a.edu.cn/fact", body,
        [(i + 1, "supports", "水的沸点是100摄氏度") for i in range(len(_claim_parts(statement)))], False)])
    assert result.label == ClaimLabel.EVIDENCE_INSUFFICIENT


@pytest.mark.parametrize("quote,expected", [
    ("水的沸点是100℃。", "水的沸点是100 °C。"),
    ("水的沸点是90℃。", None),
    ("水的沸点不是100℃。", None),
    ("水的沸点是100摄氏度。", None),
])
def test_format_recovery_returns_original_slice_without_changing_factual_content(quote, expected):
    assert ground_evidence_quote("e", quote, {"e": "水的沸点是100 °C。"}) == expected


def test_ambiguous_normalized_quotes_are_not_repaired():
    assert ground_evidence_quote("e", "100℃", {"e": "甲100 °C；乙100 °C"}) is None


@pytest.mark.parametrize("body", [
    "在标准大气压下，纯净水的沸点为100摄氏度。",
    "水在101.325 kPa 下的沸点为100 oC。",
    "水在标准大气压下的沸点为100°C。",
])
@pytest.mark.parametrize("relation,label", [("supports", ClaimLabel.CREDIBLE), ("refutes", ClaimLabel.INCORRECT)])
def test_water_metric_with_purity_conditions_and_pdf_temperature_symbol(body, relation, label):
    statement = "在标准大气压下，水的沸点是" + ("100" if relation == "supports" else "90") + "摄氏度。"
    result = verified(statement, [("https://a.edu.cn/fact", body,
        [(i + 1, relation, body) for i in range(len(_claim_parts(statement)))], False)])
    assert result.label == label


def test_condition_stays_attached_to_the_fact_for_verification():
    text = "在标准大气压下，水的沸点是100摄氏度。"
    assert _claim_parts(text) == [text]


@pytest.mark.parametrize("relation,label", [("supports", ClaimLabel.CREDIBLE), ("refutes", ClaimLabel.INCORRECT)])
def test_inverted_temperature_definition_still_binds_value_to_water(relation, label):
    body = "在标准大气压下，以0°C作为水的凝固点，100°C作为水的沸点。"
    statement = "在标准大气压下，水的沸点是" + ("100" if relation == "supports" else "90") + "摄氏度。"
    result = verified(statement, [("https://a.edu.cn/fact", body, [(1, relation, body)], False)])
    assert result.label == label
