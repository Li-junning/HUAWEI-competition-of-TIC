"""Conservative rule aggregation; external judge adapters can implement the same shape."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Sequence

from .schemas import Claim, ClaimLabel, ClaimState, EvidenceCluster, EvidenceRelation
from .source_policy import source_profile
from .claim_segmentation import judgment_parts


def source_kind(item) -> str:
    if getattr(item, "source_type", "web") == "knowledge":
        return "body"
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
        # A server-validated direct factual quote from an institutional source
        # can decide an atomic fact even when full-page extraction failed.
        if kind in {"official", "academic", "science"} and any(
            check.relation in {EvidenceRelation.SUPPORTS, EvidenceRelation.REFUTES}
            and check.excerpt and check.excerpt in item.excerpt for check in item.checks
        ):
            cap = 0.78
        q = min(q, cap)
    if source_kind(item) == "social":
        q = min(q, 0.45)
    return q


def _eligible_quality(item, relation: EvidenceRelation, *, check=None) -> float | None:
    # Uploads have no verified provenance, even when a URL was self-declared.
    # Preserve visible alignment checks but exclude them from verdict strength,
    # including historical uploads saved in separate evidence clusters.
    if item.source_type == "knowledge":
        return None
    kind = source_kind(item)
    actual_relation = check.relation if check is not None else item.relation
    if not item.excerpt or actual_relation != relation or kind not in {"body", "snippet"}:
        return None
    if check is not None and (not check.excerpt or check.excerpt not in item.excerpt):
        return None
    if item.relevance < .75 or item.time_fit < .5:
        return None
    quality = _quality(item)
    return quality if quality >= (.55 if kind == "snippet" else .58) else None


def _combine(vals: Sequence[float]) -> float:
    vals = sorted(vals, reverse=True)
    return min(1.0, vals[0] + .2 * (vals[1] if len(vals)>1 else 0) + .1 * (vals[2] if len(vals)>2 else 0)) if vals else 0.0


def evidence_strengths(clusters: Iterable[EvidenceCluster], *, parts: Sequence[str] = ()) -> tuple[float, float]:
    clusters = list(clusters)
    supports, refutes = [], []
    for cluster in clusters:
        for relation, target in ((EvidenceRelation.SUPPORTS, supports), (EvidenceRelation.REFUTES, refutes)):
            vals = [quality for item in cluster.items
                    if (quality := _eligible_quality(item, relation)) is not None]
            if vals: target.append(max(vals))
    support, refute = _combine(supports), _combine(refutes)
    if parts:
        part_supports = []
        for part_id, part in enumerate(parts, 1):
            independent = []
            for cluster in clusters:
                qualities = []
                for item in cluster.items:
                    # Old full-claim evidence remains compatible. Partial
                    # evidence counts only for its validated, unchanged fact.
                    quality = _eligible_quality(item, EvidenceRelation.SUPPORTS)
                    if quality is not None:
                        qualities.append(quality)
                    for check in item.checks:
                        if check.part_id == part_id and check.part_text == part:
                            quality = _eligible_quality(item, EvidenceRelation.SUPPORTS, check=check)
                            if quality is not None:
                                qualities.append(quality)
                if qualities:
                    independent.append(max(qualities))
            part_supports.append(_combine(independent))
        # The least covered fact controls whole-claim support. Reprints can
        # cover a new fact but cannot become independent votes for one fact.
        support = max(support, min(part_supports))
    return support, refute


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
    s, f = evidence_strengths(clusters, parts=judgment_parts(claim.normalized_claim))
    if s >= 0.75 and f < 0.5:
        claim.label, claim.reason = ClaimLabel.CREDIBLE, "当前保存的证据在实体、语境和条件上提供了充分支持。"
        if not any(source_kind(item) == "body" and (item.relation == EvidenceRelation.SUPPORTS or
                   any(check.relation == EvidenceRelation.SUPPORTS for check in item.checks))
                   for cluster in clusters for item in cluster.items):
            claim.reason = "经过逐项校验的权威摘要或独立来源摘要支持该声明；尚未取得可核对的网页全文。"
        elif not any(item.relation == EvidenceRelation.SUPPORTS for cluster in clusters for item in cluster.items):
            claim.reason = "不同网页分别证明各项事实，逐项汇总后已覆盖整条声明。"
    elif f >= 0.75 and s < 0.5:
        claim.label, claim.reason = ClaimLabel.INCORRECT, "当前保存的证据对该声明提供了充分反驳。"
        if not any(source_kind(item) == "body" and _eligible_quality(item, EvidenceRelation.REFUTES) is not None
                   for cluster in clusters for item in cluster.items):
            claim.reason = "经过逐项校验的权威摘要或独立来源摘要直接反驳该声明；尚未取得可核对的网页全文。"
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
        elif not any(item.relation in {EvidenceRelation.SUPPORTS, EvidenceRelation.REFUTES}
                     or any(check.relation in {EvidenceRelation.SUPPORTS, EvidenceRelation.REFUTES} for check in item.checks)
                     for cluster in clusters for item in cluster.items):
            claim.reason = "已找到相关资料，但未匹配声明的主体、目标事实或适用条件，请查看证据中的逐项核对原因。"
        elif any(item.source_type == "knowledge" for cluster in clusters for item in cluster.items) and s == 0 and f == 0:
            claim.reason = "本地资料与声明相关，但出处尚未核实，仅供参考；当前缺少可用于裁决的网页证据。"
        elif max(s, f) < 0.75:
            claim.reason = "已有直接证据，但来源可靠性或独立交叉验证尚未达到裁决要求。"
    claim.state = ClaimState.DONE
    return claim
