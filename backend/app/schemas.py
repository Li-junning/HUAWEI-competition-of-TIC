"""The single, validated API and persistence contract."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


class TaskStatus(str, Enum):
    CREATED = "created"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class ClaimState(str, Enum):
    PENDING = "pending"
    EXTRACTING = "extracting"
    RETRIEVING = "retrieving"
    JUDGING = "judging"
    DONE = "done"
    FAILED = "failed"
    UNCHECKED = "unchecked"


class ClaimLabel(str, Enum):
    CREDIBLE = "credible"
    DISPUTED = "disputed"
    INCORRECT = "incorrect"
    EVIDENCE_INSUFFICIENT = "evidence_insufficient"
    NOT_APPLICABLE = "not_applicable"


class EvidenceRelation(str, Enum):
    SUPPORTS = "supports"
    REFUTES = "refutes"
    PARTIALLY_SUPPORTS = "partially_supports"
    IRRELEVANT = "irrelevant"
    UNKNOWN = "unknown"


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4()}"


class CreateTaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    input_text: str = Field(min_length=1, max_length=20_000)
    claim_limit: int = Field(default=15, ge=1, le=15)

    @field_validator("input_text")
    @classmethod
    def non_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("input_text must not be blank")
        return value


class CreateTaskResponse(BaseModel):
    task_id: str
    status: TaskStatus


class EvidenceItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    evidence_id: str
    url: str | None = None
    title: str | None = None
    publisher: str | None = None
    published_at: datetime | None = None
    retrieved_at: datetime | None = None
    excerpt: str | None = None
    relation: EvidenceRelation = EvidenceRelation.UNKNOWN
    quality_reason: str | None = None
    is_reprint: bool = False
    content_hash: str | None = None
    authority: float = Field(default=0.0, ge=0, le=1)
    relevance: float = Field(default=0.0, ge=0, le=1)
    time_fit: float = Field(default=0.0, ge=0, le=1)

    @field_validator("url")
    @classmethod
    def safe_http_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        # Syntax/literal-IP validation happens even for provider-supplied data;
        # DNS is checked again at the live network boundary.
        from .security import normalize_url
        return normalize_url(value, resolve_dns=False)


class EvidenceCluster(BaseModel):
    cluster_id: str
    items: list[EvidenceItem] = Field(default_factory=list)
    independence_reason: str | None = None


class PaperCheck(BaseModel):
    input_title: str | None = None
    input_authors: list[str] = Field(default_factory=list)
    input_year: int | None = None
    input_venue: str | None = None
    input_doi: str | None = None
    candidate_title: str | None = None
    candidate_authors: list[str] = Field(default_factory=list)
    candidate_year: int | None = None
    candidate_venue: str | None = None
    candidate_doi: str | None = None
    field_matches: dict[str, bool | None] = Field(default_factory=dict)
    existence_status: str = "not_checked"
    claim_support_status: str = "not_checked"


class Claim(BaseModel):
    model_config = ConfigDict(extra="forbid")
    claim_id: str
    task_id: str
    source_text: str
    char_start: int = Field(ge=0)
    char_end: int = Field(ge=0)
    type: str = "general"
    normalized_claim: str
    entities: list[str] = Field(default_factory=list)
    conditions: list[str] = Field(default_factory=list)
    queries: list[str] = Field(default_factory=list)
    label: ClaimLabel | None = None
    support_score: int | None = Field(default=None, ge=0, le=100)
    reason: str | None = None
    state: ClaimState = ClaimState.PENDING
    retry_count: int = Field(default=0, ge=0)
    evidence_cluster_ids: list[str] = Field(default_factory=list)
    evidence_clusters: list[EvidenceCluster] = Field(default_factory=list)
    paper_check: PaperCheck | None = None


class Coverage(BaseModel):
    verifiable_claims: int = 0
    processed_verifiable_claims: int = 0
    adjudicated_verifiable_claims: int = 0
    processing_coverage: float | None = None
    verification_coverage: float | None = None
    extraction_coverage: float | None = None


class TaskSummary(BaseModel):
    segmentation_method: str = "rules"
    task_id: str
    status: TaskStatus
    created_at: datetime
    updated_at: datetime
    input_char_count: int
    claim_limit: int
    claims_extracted: int = 0
    claims_processed: int = 0
    claims_unchecked: int = 0
    truncated: bool = False
    failed_providers: list[str] = Field(default_factory=list)
    coverage: Coverage = Field(default_factory=Coverage)
    label_counts: dict[str, int] = Field(default_factory=dict)
    score: float | None = None
    score_note: str | None = None
    error_code: str | None = None


class ClaimListResponse(BaseModel):
    items: list[Claim]
    total: int
    offset: int
    limit: int


class ErrorBody(BaseModel):
    code: str
    message: str
    request_id: str


class ErrorResponse(BaseModel):
    error: ErrorBody


class ProviderResult(BaseModel):
    """Small adapter-neutral shape used by the deterministic fallback."""
    provider: str
    evidence: list[EvidenceItem] = Field(default_factory=list)
    failed: bool = False
    error_code: str | None = None
