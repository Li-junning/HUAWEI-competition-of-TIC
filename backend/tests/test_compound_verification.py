"""Mixed facts must retain their subjects, search targets, and evidence coverage."""

import json

import httpx
import pytest

from app.extract import extract_claims
from app.judge import judge_claim
from app.providers.base import SearchResult
from app.providers.mimo import MiMoEvidenceJudge
from app.providers.segmentation import SemanticSegment, validated_spans
from app.retrieve import SearchBackedRetriever, _relevant_excerpt, build_queries
from app.schemas import Claim, ClaimLabel, ClaimState, EvidenceCluster, EvidenceItem, EvidenceRelation


@pytest.mark.parametrize("text,expected", [
    ("黄河因为流经黄土高原并携带大量泥沙而得名，它最终流入南海。",
     ["黄河因为流经黄土高原并携带大量泥沙而得名", "黄河最终流入南海。"]),
    ("甲公司因为技术创新而获奖，它成立于2010年。",
     ["甲公司因为技术创新而获奖", "甲公司成立于2010年。"]),
    ("张三出生于北京，他毕业于甲大学。", ["张三出生于北京", "张三毕业于甲大学。"]),
    ("长江发源于青藏高原，它最终流入东海。", ["长江发源于青藏高原", "长江最终流入东海。"]),
])
@pytest.mark.parametrize("mode", ["rules", "coarse_model"])
def test_independent_facts_keep_exact_highlights_and_resolve_pronouns(text, expected, mode):
    spans = None if mode == "rules" else validated_spans(text, [text])
    claims, _ = extract_claims(text, "t", spans=spans)
    assert [c.normalized_claim for c in claims] == expected
    raw = text.encode("utf-16-le")
    assert "".join(c.source_text for c in claims) == text
    for claim in claims:
        assert raw[claim.char_start * 2:claim.char_end * 2].decode("utf-16-le") == claim.source_text


def test_semantic_split_keeps_cause_together_and_copies_only_subject():
    first = "甲公司因为技术创新而获奖，"
    last = "它成立于2010年。"
    spans = validated_spans(first + last, [first, SemanticSegment(source=last, context="甲公司")])
    assert spans[-1].normalized == "甲公司成立于2010年。"


@pytest.mark.parametrize("text", [
    "如果甲公司发布产品，它将获得奖励。",
    "报告称甲公司因为技术创新而获奖，它成立于2010年。",
    "甲公司收购乙公司，它位于上海。",
    "甲公司发布A产品，其支持中文输入。",
    "甲公司因为发布产品，所以获得奖励。",
    "甲公司不支持该产品，它位于上海。",
])
def test_ambiguous_or_scoped_pronouns_stay_joint(text):
    assert len(extract_claims(text, "t")[0]) == 1


def test_cross_sentence_subject_is_resolved_without_crossing_paragraphs():
    first, second = "张三出生于北京。", "他毕业于甲大学。"
    claims, _ = extract_claims(first + second, "t")
    assert claims[-1].normalized_claim == "张三毕业于甲大学。"
    claims, _ = extract_claims(first + "\n\n" + second, "t")
    assert claims[-1].state == ClaimState.UNCHECKED


@pytest.mark.parametrize("text,answer,property_word", [
    ("黄河最终流入南海。", "南海", "入海口"),
    ("长江最终流入北冰洋。", "北冰洋", "入海口"),
    ("北京大学位于上海。", "上海", "位置"),
    ("甲公司成立于2010年。", "2010", "成立时间"),
    ("张三毕业于乙大学。", "乙大学", "毕业院校"),
])
def test_search_queries_do_not_presuppose_the_proposed_answer(text, answer, property_word):
    queries = [q.query for q in build_queries("c", action=text)]
    assert queries[0] == text
    assert any(answer not in q and property_word in q for q in queries[1:])


@pytest.mark.parametrize("text,subject,answer", [
    ("北京大学创立于1898年。", "北京大学", "1898"),
    ("北京大学位于上海。", "北京大学", "上海"),
    ("长江发源于青藏高原。", "长江", "青藏高原"),
])
def test_extracted_entities_and_conditions_do_not_reintroduce_the_answer(text, subject, answer):
    claim = extract_claims(text, "t")[0][0]
    assert claim.entities == [subject]
    queries = build_queries("c", action=claim.normalized_claim, entities=claim.entities, conditions=claim.conditions)
    assert len(queries) > 1 and all(answer not in q.query for q in queries[1:])


def test_causal_query_searches_the_reason_without_presupposing_it():
    claim = extract_claims("甲公司因为技术创新而获奖。", "t")[0][0]
    assert claim.entities == ["甲公司"]
    queries = build_queries("c", action=claim.normalized_claim, entities=claim.entities)
    assert any("原因" in q.query and "技术创新" not in q.query for q in queries[1:])


class SearchFixture:
    name = "fixture"
    def __init__(self, rows):
        self.rows, self.queries = rows, []
    def search(self, query, *, limit=5):
        self.queries.append(query)
        return self.rows[:limit]


@pytest.mark.parametrize("statement,noise,correct", [
    ("黄河最终流入南海。", "黄河流经黄土高原，河水含有大量泥沙。", "黄河最后注入渤海。"),
    ("北京大学位于上海。", "北京大学是一所综合性大学，开设多个学科。", "北京大学坐落于北京。"),
    ("甲公司成立于2010年。", "甲公司在2010年发布了产品。", "甲公司创立于1998年。"),
])
def test_retrieval_requires_the_target_property_and_keeps_contradictory_answers(statement, noise, correct):
    provider = SearchFixture([SearchResult("https://a.edu.cn/noise", content=noise),
                              SearchResult("https://b.edu.cn/fact", content=correct)])
    claim = extract_claims(statement, "t")[0][0]
    clusters = SearchBackedRetriever(provider).retrieve(claim)
    items = [item for cluster in clusters for item in cluster.items]
    assert len(items) == 1 and items[0].url.endswith("/fact")
    assert len(provider.queries) <= 3


def test_excerpt_selection_prefers_the_requested_property_over_repeated_subject():
    body = ("黄河泥沙黄河水文黄河流域。" * 120) + "黄河最后注入渤海。"
    assert "注入渤海" in _relevant_excerpt(body, "黄河 流入地点 入海口")


def test_founding_excerpt_does_not_pick_later_department_history():
    body = "北京大学创办于1898年。" + ("中华人民共和国成立后，北京大学又成立了新的学院。" * 90)
    assert "创办于1898年" in _relevant_excerpt(body, "北京大学 成立时间")


def test_existing_compound_claim_searches_each_fact_within_the_query_budget():
    text = "甲公司因为技术创新而获奖，它成立于2010年。"
    claim = Claim(claim_id="c", task_id="t", source_text=text, normalized_claim=text,
                  char_start=0, char_end=len(text))
    provider = SearchFixture([
        SearchResult("https://a.edu.cn/award", content="甲公司因为技术创新而获奖。"),
        SearchResult("https://b.edu.cn/history", content="甲公司创立于1998年。"),
    ])
    clusters = SearchBackedRetriever(provider).retrieve(claim)
    assert any("成立时间" in q and "2010" not in q for q in provider.queries)
    assert len(provider.queries) <= 3
    assert {i.url for c in clusters for i in c.items} == {r.url for r in provider.rows}


def judged_compound(statement, excerpt, checks=None, relation="supports"):
    claim = Claim(claim_id="c", task_id="t", source_text=statement, normalized_claim=statement,
                  char_start=0, char_end=len(statement))
    item = EvidenceItem(evidence_id="e", url="https://example.edu/source", excerpt=excerpt, authority=.8)
    decision = dict(evidence_id="e", relation=relation, excerpt=excerpt, reason="核对证据。")
    if checks is not None:
        decision["checks"] = checks
    def respond(request):
        body = json.loads(request.content)
        assert "part_id" in body["messages"][1]["content"]
        return httpx.Response(200, json={"choices": [{"message": {
            "content": json.dumps({"decisions": [decision]}, ensure_ascii=False)}}]})
    judge = MiMoEvidenceJudge("test", transport=httpx.MockTransport(respond))
    return judge_claim(claim, [EvidenceCluster(cluster_id="ec", items=[item])], judge), item


@pytest.mark.parametrize("statement,excerpt", [
    ("黄河因为携带泥沙而得名，它最终流入南海。", "黄河携带大量泥沙。"),
    ("甲公司成立于2010年，它位于上海。", "甲公司成立于2010年。"),
    ("张三出生于北京，他毕业于甲大学。", "张三出生于北京。"),
])
def test_whole_claim_support_is_rejected_when_coverage_checks_are_missing(statement, excerpt):
    result, item = judged_compound(statement, excerpt)
    assert result.label == ClaimLabel.EVIDENCE_INSUFFICIENT
    assert item.relation == EvidenceRelation.PARTIALLY_SUPPORTS


def test_reusing_the_first_fact_quote_cannot_cover_a_different_property():
    quote = "甲公司成立于2010年。"
    result, item = judged_compound("甲公司成立于2010年，它位于上海。", quote,
        [dict(part_id=i, relation="supports", excerpt=quote) for i in (1, 2)])
    assert item.relation == EvidenceRelation.PARTIALLY_SUPPORTS
    assert result.label == ClaimLabel.EVIDENCE_INSUFFICIENT


def test_complete_grounded_coverage_can_support_a_compound_claim():
    first, second = "甲公司成立于2010年。", "甲公司位于上海。"
    result, _ = judged_compound("甲公司成立于2010年，它位于上海。", first + second,
        [dict(part_id=1, relation="supports", excerpt=first), dict(part_id=2, relation="supports", excerpt=second)])
    assert result.label == ClaimLabel.CREDIBLE


def test_a_direct_refutation_of_one_fact_invalidates_the_compound_claim():
    result, _ = judged_compound("甲公司成立于2010年，它位于上海。", "甲公司位于北京，而非上海。", relation="refutes")
    assert result.label == ClaimLabel.INCORRECT
