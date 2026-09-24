from app.judge import judge_claim, source_kind
from app.extract import extract_claims
from app.schemas import Claim, ClaimLabel, EvidenceCluster, EvidenceItem, EvidenceRelation
from app.scoring import support_score


def claim():
    return Claim(claim_id="c_1", task_id="t_1", source_text="事实", char_start=0,
                 char_end=2, normalized_claim="事实")


def evidence(eid, relation, *, authority=.55, relevance=.9, time_fit=.5,
             url="https://example.com/article", reason=None):
    return EvidenceItem(evidence_id=eid, url=url, excerpt="可核验的正文摘录",
                        relation=relation, authority=authority, relevance=relevance,
                        time_fit=time_fit, quality_reason=reason)


def cluster(cid, item):
    return EvidenceCluster(cluster_id=cid, items=[item], independence_reason="不同来源")


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


def test_snippets_are_excluded_even_when_in_different_clusters():
    clusters = [cluster("s", evidence("s1", EvidenceRelation.SUPPORTS, authority=1, reason="摘要")),
                cluster("f", evidence("f1", EvidenceRelation.REFUTES, authority=1, reason="snippet"))]
    assert judge_claim(claim(), clusters).label == ClaimLabel.EVIDENCE_INSUFFICIENT


def test_social_domain_detection_does_not_use_url_path():
    item = evidence("e", EvidenceRelation.SUPPORTS, authority=.9, url="https://example.com/weibo.com/article")
    assert source_kind(item) == "body"
    assert judge_claim(claim(), [cluster("s", item)]).label == ClaimLabel.CREDIBLE
