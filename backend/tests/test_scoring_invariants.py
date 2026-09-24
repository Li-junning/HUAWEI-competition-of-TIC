from app.judge import judge_claim
from app.schemas import Claim, ClaimLabel, EvidenceCluster, EvidenceItem, EvidenceRelation


def _claim() -> Claim:
    return Claim(
        claim_id="c_00000000-0000-0000-0000-000000000000",
        task_id="t_00000000-0000-0000-0000-000000000000",
        source_text="测试事实。",
        char_start=0,
        char_end=5,
        normalized_claim="测试事实。",
    )


def _support(evidence_id: str, quality: float) -> EvidenceItem:
    return EvidenceItem(
        evidence_id=evidence_id,
        excerpt="同一公告",
        relation=EvidenceRelation.SUPPORTS,
        authority=quality,
        relevance=quality,
        time_fit=quality,
    )


def test_reprints_in_one_cluster_do_not_gain_independence_bonus():
    claim = _claim()
    cluster = EvidenceCluster(cluster_id="ec_1", items=[_support("e_1", 0.72), _support("e_2", 0.72)])
    judge_claim(claim, [cluster])
    assert claim.label == ClaimLabel.EVIDENCE_INSUFFICIENT


def test_two_independent_clusters_receive_only_limited_bonus():
    claim = _claim()
    clusters = [
        EvidenceCluster(cluster_id="ec_1", items=[_support("e_1", 0.75)]),
        EvidenceCluster(cluster_id="ec_2", items=[_support("e_2", 0.75)]),
    ]
    judge_claim(claim, clusters)
    assert claim.label == ClaimLabel.CREDIBLE
