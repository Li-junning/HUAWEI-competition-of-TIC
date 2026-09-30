import pytest

from app.judge import judge_claim, source_kind
from app.extract import extract_claims
from app.schemas import Claim, ClaimLabel, EvidenceCluster, EvidenceItem, EvidenceRelation
from app.scoring import support_score
from app.source_policy import source_profile


def claim(text="事实"):
    return Claim(claim_id="c_1", task_id="t_1", source_text=text, char_start=0,
                 char_end=len(text), normalized_claim=text)


def evidence(eid, relation, *, authority=.55, relevance=.9, time_fit=.5,
             url="https://example.com/article", reason=None):
    return EvidenceItem(evidence_id=eid, url=url, excerpt="可核验的正文摘录",
                        relation=relation, authority=authority, relevance=relevance,
                        time_fit=time_fit, quality_reason=reason)


def cluster(cid, item):
    return EvidenceCluster(cluster_id=cid, items=[item], independence_reason="不同来源")


def snippet(eid, relation, url):
    return evidence(eid, relation, authority=source_profile(url).authority,
                    url=url, reason="搜索结果摘要；未取得完整正文")


def test_two_independent_medium_refutations_can_reach_incorrect_and_score_matches():
    clusters = [cluster("f1", evidence("e1", EvidenceRelation.REFUTES, authority=.45,
                                        url="https://one.example/article")),
                cluster("f2", evidence("e2", EvidenceRelation.REFUTES, authority=.55,
                                        url="https://two.example/article"))]
    result = judge_claim(claim(), clusters)
    assert result.label == ClaimLabel.INCORRECT
    assert support_score(result) == 12


def test_one_medium_source_is_still_insufficient():
    result = judge_claim(claim(), [cluster("f1", evidence("e1", EvidenceRelation.REFUTES, authority=.55))])
    assert result.label == ClaimLabel.EVIDENCE_INSUFFICIENT


def test_missing_body_is_explained_without_guessing_a_verdict():
    result = judge_claim(claim(), [])
    assert result.label == ClaimLabel.EVIDENCE_INSUFFICIENT
    assert '未取得可用的证据正文' in result.reason


def test_qualified_possible_fact_remains_verifiable():
    claims, _ = extract_claims("这项技术可能提高效率。", "t_1")
    assert claims[0].label is None


def test_two_medium_refutations_in_one_cluster_remain_insufficient():
    items = [evidence("e1", EvidenceRelation.REFUTES, authority=.45),
             evidence("e2", EvidenceRelation.REFUTES, authority=.55)]
    result = judge_claim(claim(), [EvidenceCluster(cluster_id="same", items=items)])
    assert result.label == ClaimLabel.EVIDENCE_INSUFFICIENT


def test_social_support_does_not_create_disputed():
    clusters = [cluster("s", evidence("s1", EvidenceRelation.SUPPORTS, url="https://sub.tieba.baidu.com/p/1")),
                cluster("f1", evidence("f1", EvidenceRelation.REFUTES, authority=.45)),
                cluster("f2", evidence("f2", EvidenceRelation.REFUTES, authority=.55))]
    assert judge_claim(claim(), clusters).label == ClaimLabel.INCORRECT


def test_two_strong_conflicting_directions_are_disputed():
    clusters = [cluster("s", evidence("s1", EvidenceRelation.SUPPORTS, authority=.9)),
                cluster("f", evidence("f1", EvidenceRelation.REFUTES, authority=.9))]
    assert judge_claim(claim(), clusters).label == ClaimLabel.DISPUTED


def test_one_snippet_on_each_side_cannot_settle_a_conflict():
    clusters = [cluster("s", evidence("s1", EvidenceRelation.SUPPORTS, authority=1, reason="摘要")),
                cluster("f", evidence("f1", EvidenceRelation.REFUTES, authority=1, reason="snippet"))]
    assert judge_claim(claim(), clusters).label == ClaimLabel.EVIDENCE_INSUFFICIENT


@pytest.mark.parametrize("statement,relation,urls,expected", [
    ("我国地势总体西高东低，影响许多河流的流向。", EvidenceRelation.SUPPORTS,
     ("https://kpzg.people.com.cn/article", "https://www.studocu.vn/document"), ClaimLabel.CREDIBLE),
    ("纯水在标准大气压下的沸点是100摄氏度。", EvidenceRelation.SUPPORTS,
     ("https://physics.ac.cn/water", "https://example.edu/water"), ClaimLabel.CREDIBLE),
    ("声音能在真空中传播。", EvidenceRelation.REFUTES,
     ("https://kepu.gmw.cn/sound", "https://example.edu/sound"), ClaimLabel.INCORRECT),
])
def test_independent_direct_snippets_can_support_or_refute_across_topics(statement, relation, urls, expected):
    clusters = [cluster(str(index), snippet(str(index), relation, url))
                for index, url in enumerate(urls)]
    result = judge_claim(claim(statement), clusters)
    assert result.label == expected
    assert support_score(result) is not None
    assert "尚未取得可核对的网页全文" in result.reason


def test_one_authoritative_snippet_is_not_enough():
    result = judge_claim(claim(), [cluster("one", snippet("e", EvidenceRelation.SUPPORTS,
                                                       "https://example.gov.cn/fact"))])
    assert result.label == ClaimLabel.EVIDENCE_INSUFFICIENT


def test_unknown_snippets_alone_are_not_enough_even_from_three_clusters():
    clusters = [cluster(str(index), snippet(str(index), EvidenceRelation.SUPPORTS,
                                            f"https://unknown{index}.example/fact"))
                for index in range(3)]
    assert judge_claim(claim(), clusters).label == ClaimLabel.EVIDENCE_INSUFFICIENT


def test_two_snippets_in_one_cluster_do_not_count_twice():
    items = [snippet("a", EvidenceRelation.REFUTES, "https://kpzg.people.com.cn/a"),
             snippet("b", EvidenceRelation.REFUTES, "https://www.studocu.vn/b")]
    result = judge_claim(claim(), [EvidenceCluster(cluster_id="same", items=items)])
    assert result.label == ClaimLabel.EVIDENCE_INSUFFICIENT


def test_partial_snippet_cannot_fill_missing_coverage_for_a_direct_news_snippet():
    clusters = [cluster("direct", snippet("a", EvidenceRelation.SUPPORTS,
                                          "https://kpzg.people.com.cn/a")),
                cluster("partial", snippet("b", EvidenceRelation.PARTIALLY_SUPPORTS,
                                           "https://www.studocu.vn/b"))]
    assert judge_claim(claim("甲成立，并导致乙发生。"), clusters).label == ClaimLabel.EVIDENCE_INSUFFICIENT


def test_partial_snippets_without_direct_support_remain_insufficient():
    clusters = [cluster(str(index), snippet(str(index), EvidenceRelation.PARTIALLY_SUPPORTS,
                                            f"https://source{index}.gov.cn/fact"))
                for index in range(3)]
    assert judge_claim(claim("甲成立，并导致乙发生。"), clusters).label == ClaimLabel.EVIDENCE_INSUFFICIENT


def test_significant_opposing_evidence_blocks_a_credible_verdict():
    clusters = [cluster("support", evidence("s", EvidenceRelation.SUPPORTS, authority=.9)),
                cluster("refute", evidence("f", EvidenceRelation.REFUTES, authority=.55))]
    result = judge_claim(claim(), clusters)
    assert result.label == ClaimLabel.EVIDENCE_INSUFFICIENT
    assert "支持与反驳" in result.reason


def test_social_domain_detection_does_not_use_url_path():
    item = evidence("e", EvidenceRelation.SUPPORTS, authority=.9, url="https://example.com/weibo.com/article")
    assert source_kind(item) == "body"
    assert judge_claim(claim(), [cluster("s", item)]).label == ClaimLabel.CREDIBLE
