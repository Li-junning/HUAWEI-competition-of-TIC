"""Shared task summary assembly for execution, queries and exports."""

import json
from datetime import datetime
from sqlite3 import Row

from .schemas import Claim, ClaimState, TaskStatus, TaskSummary
from .scoring import summarize


def build_task_summary(
    row: Row, claims: list[Claim], *, technical_failure: bool | None = None,
) -> TaskSummary:
    failed_providers = json.loads(row["failed_providers"])
    if technical_failure is None:
        technical_failure = (
            bool(failed_providers)
            or TaskStatus(row["status"]) not in {TaskStatus.SUCCEEDED, TaskStatus.PARTIAL}
            or any(c.state in {ClaimState.FAILED, ClaimState.UNCHECKED} for c in claims)
        )
    counts, cov, score, note = summarize(
        claims, truncated=bool(row["truncated"]), technical_failure=technical_failure,
    )
    return TaskSummary(
        task_id=row["task_id"], status=TaskStatus(row["status"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        input_char_count=row["input_char_count"], claim_limit=row["claim_limit"],
        claims_extracted=len(claims) + int(row["claims_unchecked"]),
        claims_processed=sum(c.state == ClaimState.DONE for c in claims),
        claims_unchecked=int(row["claims_unchecked"]) + sum(
            c.state in {ClaimState.UNCHECKED, ClaimState.FAILED} for c in claims
        ),
        truncated=bool(row["truncated"]), failed_providers=failed_providers,
        coverage=cov, label_counts=counts, score=score, score_note=note,
        error_code=row["error_code"],
        segmentation_method=row["segmentation_method"],
    )
