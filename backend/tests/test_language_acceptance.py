"""Independent product examples for sentence and evidence retrieval quality.

All sources below are synthetic fixtures, not live factual evidence.
"""

import pytest

from app.extract import count_claim_candidates, extract_claims
from app.providers.base import SearchResult
from app.retrieve import SearchBackedRetriever, _relevant_excerpt, build_queries
from app.schemas import Claim


def extracted(text, limit=15):
    return extract_claims(text, "t_acceptance", limit)


def claim_for(text):
    return Claim(
        claim_id="c_acceptance", task_id="t_acceptance", source_text=text,
        normalized_claim=text, char_start=0,
        char_end=len(text.encode("utf-16-le")) // 2,
    )


class FixtureSearch:
    name = "fixture"

    def __init__(self, results):
        self.results = results
        self.calls = []

    def search(self, query, *, limit=5):
        self.calls.append(query)
        return self.results[:limit]


@pytest.mark.parametrize("text, expected", [
    ("The rate is 3.14%. It rose in 2024.", ["The rate is 3.14%.", "It rose in 2024."]),
    ("Dr. Smith released v2.1.0. It supports Python 3.13.",
     ["Dr. Smith released v2.1.0.", "It supports Python 3.13."]),
    ("来源是 https://example.org/a?x=1.2。下一项为5%。",
     ["来源是 https://example.org/a?x=1.2。", "下一项为5%。"]),
    ("报告称：“试验完成。”下一阶段于2025年开始。",
     ["报告称：“试验完成。”", "下一阶段于2025年开始。"]),
    ("样本量为100（其中A组占3.5%；B组占96.5%）。结果尚不确定。",
     ["样本量为100（其中A组占3.5%；B组占96.5%）。", "结果尚不确定。"]),
    ("The results don't prove it. Further trials are needed.",
     ["The results don't prove it.", "Further trials are needed."]),
    ("版本3.x已发布。仍需核验。", ["版本3.x已发布。", "仍需核验。"]),
    ("See https://example.org/report. Next claim.",
     ["See https://example.org/report.", "Next claim."]),
    ("Done. U.S. teams reported growth.", ["Done.", "U.S. teams reported growth."]),
])
def test_sentence_boundaries_preserve_identifiers_and_context(text, expected):
    claims, truncated = extracted(text)
    assert [c.source_text for c in claims] == expected
    assert not truncated
    assert count_claim_candidates(text) == len(expected)


def test_normalization_is_separate_from_exact_utf16_source_spans():
    text = "😀 说明。\n1. **华为**于２０２４年发布[年度报告](https://example.org/report)。\n2. 增幅不是３．５％。"
    claims, _ = extracted(text)
    assert len(claims) == 3
    raw = text.encode("utf-16-le")
    for c in claims:
        assert raw[c.char_start * 2:c.char_end * 2].decode("utf-16-le") == c.source_text
    assert "华为" in claims[1].normalized_claim
    assert "2024年" in claims[1].normalized_claim
    assert "年度报告" in claims[1].normalized_claim
    assert "**" not in claims[1].normalized_claim
    assert "https://" not in claims[1].normalized_claim
    assert not claims[1].normalized_claim.startswith("1.")
    assert "不是3.5%" in claims[2].normalized_claim


def test_heading_and_empty_markers_do_not_consume_the_claim_budget():
    text = "## 核验说明\n---\n- \n1. 第一项发生于2024年。\n2. 第二项发生于2025年。"
    claims, truncated = extracted(text, limit=1)
    assert len(claims) == 1 and truncated
    assert "第一项" in claims[0].normalized_claim
    assert count_claim_candidates(text) == 2


def test_unpunctuated_list_lines_are_not_silently_dropped():
    text = "- 北京是中国的首都\n- 上海位于中国东部\n- 深圳位于广东省"
    claims, truncated = extracted(text, limit=2)
    assert len(claims) == 2 and truncated
    assert "北京" in claims[0].normalized_claim
    assert "上海" in claims[1].normalized_claim
    assert count_claim_candidates(text) == 3


def test_punctuation_only_does_not_produce_claims():
    claims, truncated = extracted("。。。！！！\n；；；\n---")
    assert claims == [] and not truncated
    assert count_claim_candidates("。。。！！！\n；；；\n---") == 0


def test_chinese_conditions_and_negation_stay_in_one_claim():
    text = "在标准大气压下，纯水的沸点不是90摄氏度，而是100摄氏度。"
    claims, _ = extracted(text)
    assert len(claims) == 1
    assert claims[0].source_text == text
    assert all(part in claims[0].normalized_claim for part in
               ["在标准大气压下", "纯水的沸点不是90摄氏度", "而是100摄氏度"])


@pytest.mark.parametrize("name", ["华为", "英伟达"])
def test_entity_names_are_not_cut_at_single_character_predicates(name):
    claim = extracted(f"{name}在2024年的研发投入为20亿元。")[0][0]
    assert name in claim.entities
    assert any("2024" in condition for condition in claim.conditions)
    assert all("研发投入" not in condition for condition in claim.conditions)


def test_attributed_organization_does_not_absorb_dates_or_introductory_words():
    claim = extracted("根据世界卫生组织2024年的报告，疟疾病例增加了10%。")[0][0]
    assert "世界卫生组织" in claim.entities
    assert all("根据" not in entity and "2024" not in entity for entity in claim.entities)


def test_markdown_emphasis_is_removed_but_approximation_and_identifiers_survive():
    claim = extracted("__Atlas_900__ 的误差约为 ~5%，**样本量**为100。")[0][0]
    assert "__" not in claim.normalized_claim and "**" not in claim.normalized_claim
    assert "Atlas_900" in claim.normalized_claim
    assert "~5%" in claim.normalized_claim


def test_queries_deduplicate_embedded_entities_and_conditions():
    queries = build_queries(
        "c", action="华为在2024年的研发投入为1797亿元。",
        entities=["华为", "华为"], conditions=["在2024年", "在2024年"],
    )
    assert 1 <= len(queries) <= 3
    assert len({q.query.casefold() for q in queries}) == len(queries)
    assert all(q.query.count("华为") <= 1 for q in queries)
    assert all(q.query.count("2024") <= 1 for q in queries)
    assert any("华为" in q.query and "2024" in q.query and "研发" in q.query
               and "1797" not in q.query for q in queries)
    assert all("site:" not in q.query for q in queries)


def test_primary_query_preserves_negation_and_scope():
    claim = extracted("在标准大气压下，纯水的沸点不是90摄氏度。")[0][0]
    queries = build_queries("c", action=claim.normalized_claim,
                            entities=claim.entities, conditions=claim.conditions)
    assert "不是" in queries[0].query
    assert "90" in queries[0].query
    assert "标准大气压" in queries[0].query
    assert all("沸点不" not in q.query or "沸点不是90" in q.query for q in queries)


def test_neutral_keywords_have_no_dangling_predicates_or_repeated_subject():
    claims = [extracted(text)[0][0] for text in
              ["华为在2024年的研发投入为1797亿元。", "中国的首都是北京。"]]
    for claim in claims:
        queries = build_queries("c", action=claim.normalized_claim,
                                entities=claim.entities, conditions=claim.conditions)
        assert all(q.query.count("华为") <= 1 and q.query.count("中国") <= 1 for q in queries)
        if "华为" in claim.source_text:
            neutral = [q.query for q in queries if "1797" not in q.query]
            assert neutral and all("投入为" not in q and "的研发" not in q for q in neutral)


def test_neutral_keyword_cleanup_preserves_characters_inside_topic_words():
    queries = build_queries("c", action="测试线路的目的地是北京。", entities=["测试线路"])
    neutral = [q.query for q in queries if "北京" not in q.query]
    assert neutral and all("目的地" in q for q in neutral)


def test_query_budget_is_bounded_without_cutting_short_product_identifiers():
    queries = build_queries(
        "c", entities=["Atlas-900", "Atlas-900"],
        action="Atlas-900 在2024年发布。" + "关于公开资料中的各项相关说明，" * 30,
        time_range="2024", conditions=["公开资料"] * 10,
    )
    assert queries and len(queries) <= 3
    assert all(len(q.query) <= 160 for q in queries)
    assert "Atlas-900" in queries[0].query
    assert all(q.query.count("Atlas-900") <= 1 for q in queries)


def test_excerpt_can_reach_a_fact_late_in_one_unbroken_paragraph():
    prefix = "页面说明和通用介绍" * 220
    fact = "长江的长度约为6300千米"
    body = prefix + fact + "，这一数据来自流域资料。"
    excerpt = _relevant_excerpt(body, "长江的长度为6300千米", "")
    assert fact in excerpt
    assert excerpt in body and len(excerpt) <= 1000


def test_repeated_navigation_does_not_outweigh_topic_coverage():
    body = ("长江 首页 登录 注册 目录\n" * 100) + "长江的长度约为6300千米，流域面积约180万平方千米。"
    excerpt = _relevant_excerpt(body, "长江的长度为6300千米", "")
    assert "长度约为6300千米" in excerpt
    assert excerpt in body and len(excerpt) <= 1000


def test_repeated_keywords_in_a_single_paragraph_do_not_hide_later_body():
    body = ("长江的长度为6300千米 首页 登录 目录 " * 100) + "长江的长度为6300千米，这一测量采用河源至入海口距离。"
    excerpt = _relevant_excerpt(body, "长江的长度为6300千米", "")
    assert "采用河源至入海口距离" in excerpt
    assert excerpt in body and len(excerpt) <= 1000


def test_provider_snippet_does_not_override_the_claim_topic():
    snippet = "关于城市停车设施和居民出行规划的政策说明，探讨交通道路建设、公共服务、生活便利以及社区公共空间和商场管理。"
    body = snippet + ("会议还有其他一些内容。" * 200) + "长江的长度约为6300千米。"
    excerpt = _relevant_excerpt(body, "长江的长度为6300千米", snippet)
    assert "长江的长度约为6300千米" in excerpt
    assert excerpt in body


def test_relevant_body_outranks_an_unrelated_official_page():
    provider = FixtureSearch([
        SearchResult("https://source.gov.cn/notice", title="会议安排", content="本次会议讨论城市停车管理。"),
        SearchResult("https://source.example.org/report", title="长江水文资料",
                     content="长江的长度约为6300千米，其流域面积约180万平方千米。"),
    ])
    result = SearchBackedRetriever(provider, max_evidence=1).retrieve(claim_for("长江的长度为6300千米。"))
    assert result[0].items[0].url == "https://source.example.org/report"
    assert result[0].items[0].relation.value == "unknown"


def test_different_numbers_do_not_remove_potential_refuting_evidence():
    provider = FixtureSearch([
        SearchResult("https://irrelevant.gov.cn/notice", title="2024年数据",
                     content="2024年会议接待1500名参会者。"),
        SearchResult("https://company.example.org/report", title="华为研发投入",
                     content="华为2024年的研发投入为1797亿元。"),
    ])
    result = SearchBackedRetriever(provider, max_evidence=1).retrieve(claim_for("华为2024年的研发投入为1500亿元。"))
    assert result[0].items[0].url == "https://company.example.org/report"
    assert "1797" in result[0].items[0].excerpt


def test_repeated_url_keeps_the_full_body_even_if_snippet_arrives_first():
    url = "https://source.example.org/fact"
    provider = FixtureSearch([
        SearchResult(url, title="长江长度", content="长江长度资料", metadata={"content_kind": "search_snippet"}),
        SearchResult(url, title="长江长度", content="长江的长度约为6300千米。", metadata={"content_kind": "raw_text"}),
    ])
    result = SearchBackedRetriever(provider).retrieve(claim_for("长江的长度为6300千米。"))
    items = [i for cluster in result for i in cluster.items]
    assert len(items) == 1
    assert "6300" in items[0].excerpt
    assert "搜索结果摘要" not in items[0].quality_reason
    assert len(provider.calls) <= 3


def test_pipeline_persists_clean_queries_and_counts_only_actual_claims():
    from app.pipeline import Pipeline
    from app.storage import Storage

    text = "## 统计说明\n1. **甲公司**2024年的研发投入为20亿元。\n2. 乙公司2024年的研发投入不是30亿元。\n3. 丙公司2024年的研发投入为40亿元。"
    storage = Storage(":memory:")
    try:
        provider = FixtureSearch([])
        pipeline = Pipeline(storage, retriever=SearchBackedRetriever(provider))
        task = pipeline.create(text, 2)
        summary = pipeline.run_sync(task.task_id)
        claims, _ = storage.list_claims(task.task_id)
        assert summary.truncated and summary.claims_unchecked == 1
        assert summary.claims_extracted == 3
        assert len(claims) == 2
        assert all(c.queries for c in claims)
        assert all("**" not in q and "统计说明" not in q for c in claims for q in c.queries)
        assert "不是" in claims[1].queries[0]
        assert summary.score is None
    finally:
        storage.close()
