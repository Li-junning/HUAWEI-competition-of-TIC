from app.extract import extract_claims
from app.retrieve import _relevant_excerpt, build_queries
from app.text_processing import normalize_claim


def test_abbreviations_and_url_terminal_periods_are_boundaries():
    claims, _ = extract_claims("See https://example.org/report. Examples include e.g. water. Next.", "t")
    assert [claim.source_text for claim in claims] == [
        "See https://example.org/report.",
        "Examples include e.g. water.",
        "Next.",
    ]


def test_neutral_query_drops_removed_grammar_without_corrupting_names():
    claim = extract_claims("华为在2024年的研发投入为1797亿元。", "t")[0][0]
    queries = [item.query for item in build_queries("c", action=claim.normalized_claim,
                                                    entities=claim.entities,
                                                    conditions=claim.conditions)]
    assert any("华为" in query and "研发投入" in query and "1797" not in query for query in queries)
    assert "行为" in extract_claims("行为。地下水。", "t")[0][0].normalized_claim


def test_anchor_coverage_beats_a_long_unrelated_provider_snippet():
    snippet = "关于城市停车设施和居民出行规划的政策说明，探讨交通道路建设、公共服务。"
    body = snippet + ("会议还有其他一些内容。" * 200) + "长江的长度约为6300千米。"
    excerpt = _relevant_excerpt(body, "长江的长度为6300千米", snippet)
    assert "6300" in excerpt and excerpt in body and len(excerpt) <= 1000


def test_markdown_emphasis_is_removed_but_content_markers_are_kept():
    normalized = normalize_claim("**华为**发布 Atlas_900，约~5%。")
    assert "华为" in normalized and "Atlas_900" in normalized and "~5%" in normalized
    assert "**" not in normalized
