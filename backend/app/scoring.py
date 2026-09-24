"""Coverage and score rules. A null score is intentional when coverage is unsafe."""

from __future__ import annotations

from collections import Counter

from .schemas import Claim, ClaimLabel, ClaimState, Coverage
from .judge import evidence_strengths


VERIFIABLE = {ClaimLabel.CREDIBLE, ClaimLabel.DISPUTED, ClaimLabel.INCORRECT, ClaimLabel.EVIDENCE_INSUFFICIENT}
ADJUDICATED = {ClaimLabel.CREDIBLE, ClaimLabel.DISPUTED, ClaimLabel.INCORRECT}


def support_score(claim: Claim) -> int | None:
    if claim.label not in ADJUDICATED:
        return None
    support, refute = evidence_strengths(claim.evidence_clusters)
    return round(50 + 50 * (support - refute))


def coverage(claims: list[Claim]) -> Coverage:
    verifiable = [c for c in claims if c.label in VERIFIABLE or c.type not in {"opinion"}]
    processed = [c for c in verifiable if c.state == ClaimState.DONE]
    adjudicated = [c for c in verifiable if c.label in ADJUDICATED]
    total = len(verifiable)
    return Coverage(
        verifiable_claims=total,
        processed_verifiable_claims=len(processed),
        adjudicated_verifiable_claims=len(adjudicated),
        processing_coverage=round(len(processed) / total, 4) if total else None,
        verification_coverage=round(len(adjudicated) / total, 4) if total else None,
        extraction_coverage=None,
    )


def summarize(claims: list[Claim], *, truncated: bool, technical_failure: bool) -> tuple[dict[str, int], Coverage, float | None, str]:
    # Technical failures/pending work are not mislabeled as evidence insufficiency.
    counts = Counter(c.label.value for c in claims if c.label is not None)
    for label in ClaimLabel:
        counts.setdefault(label.value, 0)
    cov = coverage(claims)
    for c in claims:
        c.support_score = support_score(c)
    eligible = [c.support_score for c in claims if c.support_score is not None]
    if cov.verification_coverage is None:
        return dict(counts), cov, None, "没有可核验事实，未显示总体支持指数"
    if cov.verification_coverage < 0.8 or truncated or technical_failure:
        return dict(counts), cov, None, "核验覆盖率不足、存在截断或技术性部分失败，未显示总体支持指数"
    return dict(counts), cov, (round(sum(eligible) / len(eligible), 2) if eligible else None), "已判定事实的平均支持指数"
