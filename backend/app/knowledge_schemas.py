"""Validated contracts for a local, provenance-aware evidence library."""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .security import normalize_url


class KnowledgeImport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=160)
    publisher: str | None = Field(default=None, max_length=160)
    source_url: str | None = Field(default=None, max_length=2048)
    published_at: date | None = None
    tags: list[str] = Field(default_factory=list, max_length=12)
    content: str | None = Field(default=None, max_length=100_000)
    filename: str | None = Field(default=None, max_length=200)
    file_base64: str | None = Field(default=None, max_length=2_800_000)

    @field_validator("title", "publisher", "source_url", "filename")
    @classmethod
    def strip_text(cls, value):
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("metadata cannot be blank")
        return value

    @field_validator("source_url")
    @classmethod
    def validate_url(cls, value):
        return normalize_url(value, resolve_dns=False) if value else None

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, values):
        values = list(dict.fromkeys(v.strip() for v in values))
        if any(not v or len(v) > 40 for v in values):
            raise ValueError("invalid tag")
        return values

    @model_validator(mode="after")
    def one_input(self):
        if (self.content is not None) == (self.file_base64 is not None):
            raise ValueError("supply text or a file")
        if self.file_base64 is not None and not self.filename:
            raise ValueError("filename is required")
        return self


class KnowledgeDocument(BaseModel):
    document_id: str
    title: str
    publisher: str | None = None
    source_url: str | None = None
    published_at: date | None = None
    tags: list[str] = Field(default_factory=list)
    filename: str | None = None
    content_hash: str
    created_at: datetime
    char_count: int
    page_count: int
    chunk_count: int
    indexed_chunks: int


class KnowledgeHit(BaseModel):
    chunk_id: str
    document_id: str
    title: str
    publisher: str | None = None
    source_url: str | None = None
    published_at: date | None = None
    tags: list[str] = Field(default_factory=list)
    content_hash: str
    page: int | None = None
    char_start: int
    char_end: int
    excerpt: str
    retrieval_score: float
    matched_by: list[Literal["keyword", "semantic"]]


class KnowledgeSearch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=2000)
    tag: str | None = Field(default=None, max_length=40)
    limit: int = Field(default=5, ge=1, le=20)

    @field_validator("query")
    @classmethod
    def non_blank(cls, value):
        if not value.strip():
            raise ValueError("query cannot be blank")
        return value.strip()
