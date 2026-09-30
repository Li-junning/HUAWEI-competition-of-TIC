"""Conservative rule aggregation; external judge adapters can implement the same shape."""

from __future__ import annotations

from collections.abc import Iterable

from .schemas import Claim, ClaimLabel, ClaimState, EvidenceCluster, EvidenceRelation
from .source_policy import source_profile


def source_kind(item) -> str:
    kind = source_profile(item.url).kind
    if kind in {"social", "community", "entertainment", "dictionary"}:
        return kind
    if "摘要" in (item.quality_reason or "") or "snippet" in (item.quality_reason or "").lower():
        return "snippet"
    return "body"


def _quality(item) -> float:
    q = 0.5 * item.authority + 0.3 * item.relevance + 0.2 * item.time_fit
    if source_kind(item) == "snippet":
        # A snippet is weaker than verified page text, but several independent
        # direct excerpts can corroborate one another.  Unknown sites alone
        # remain below the decision threshold even with three clusters.
        kind = source_profile(item.url).kind
        cap = 0.69 if kind in {"official", "academic", "science"} else 0.66 if kind == "news" else 0.56
        q = min(q, cap)
    if source_kind(item) == "social":
        q = min(q, 0.45)
    return q


def _eligible_quality(item, relation: EvidenceRelation) -> float | None:
    kind = source_kind(item)
    if not item.excerpt or item.relation != relation or kind not in {"body", "snippet"}:
        return None
    if item.relevance < .75 or item.time_fit < .5:
        return None
    quality = _quality(item)
    return quality if quality >= (.55 if kind == "snippet" else .58) else None


def evidence_strengths(clusters: Iterable[EvidenceCluster]) -> tuple[float, float]:
    supports, refutes = [], []
    for cluster in clusters:
        for relation, target in ((EvidenceRelation.SUPPORTS, supports), (EvidenceRelation.REFUTES, refutes)):
            vals = [quality for item in cluster.items
                    if (quality := _eligible_quality(item, relation)) is not None]
            if vals: target.append(max(vals))
    def combine(vals):
        vals = sorted(vals, reverse=True)
        return min(1.0, vals[0] + .2 * (vals[1] if len(vals)>1 else 0) + .1 * (vals[2] if len(vals)>2 else 0)) if vals else 0.0
    return combine(supports), combine(refutes)


def judge_claim(claim: Claim, clusters: Iterable[EvidenceCluster], evidence_judge=None, *, deadline: float | None = None) -> Claim:
    clusters = list(clusters)
    claim.evidence_clusters = clusters
    claim.evidence_cluster_ids = [cluster.cluster_id for cluster in clusters]
    if claim.label == ClaimLabel.NOT_APPLICABLE:
        claim.state = ClaimState.DONE
        claim.reason = "该内容属于观点、建议或修辞，不参与事实核验。"
        return claim
    if evidence_judge is not None and clusters:
        bounded_judge = getattr(evidence_judge, "judge_with_deadline", None)
        if bounded_judge is not None and deadline is not None:
            bounded_judge(claim, clusters, deadline=deadline)
        else:
            evidence_judge.judge(claim, clusters)
    # Count independent clusters, never pages/reprints within the same cluster.
    s, f = evidence_strengths(clusters)
    if s >= 0.75 and f < 0.5:
        claim.label, claim.reason = ClaimLabel.CREDIBLE, "当前保存的证据在实体、语境和条件上提供了充分支持。"
        if not any(source_kind(item) == "body" and _eligible_quality(item, EvidenceRelation.SUPPORTS) is not None
                   for cluster in clusters for item in cluster.items):
            claim.reason = "独立来源的搜索摘要相互印证该声明；尚未取得可核对的网页全文。"
    elif f >= 0.75 and s < 0.5:
        claim.label, claim.reason = ClaimLabel.INCORRECT, "当前保存的证据对该声明提供了充分反驳。"
        if not any(source_kind(item) == "body" and _eligible_quality(item, EvidenceRelation.REFUTES) is not None
                   for cluster in clusters for item in cluster.items):
            claim.reason = "多个独立来源的搜索摘要直接反驳该声明；尚未取得可核对的网页全文。"
    elif s >= 0.75 and f >= 0.75:
        claim.label, claim.reason = ClaimLabel.DISPUTED, "独立证据对同一语境存在实质冲突，无法安全地强行定论。"
    else:
        claim.label, claim.reason = ClaimLabel.EVIDENCE_INSUFFICIENT, "当前可访问证据不足以安全判定该声明。"
        if not any(item.excerpt for cluster in clusters for item in cluster.items):
            claim.reason = "本次检索未取得可用的证据正文，尚无法判断声明真伪。未找到证据不代表声明正确。"
        elif s >= 0.5 and f >= 0.5:
            claim.reason = "当前支持与反驳证据尚未达到可裁决门槛，需要继续核对原文与适用条件。"
        elif s == 0 and f == 0 and any(item.relation == EvidenceRelation.PARTIALLY_SUPPORTS
                                      for cluster in clusters for item in cluster.items):
            claim.reason = "当前证据只支持部分事实，尚未覆盖整条声明；未核验的部分不能视为正确。"
    claim.state = ClaimState.DONE
    return claim
