"""Conservative Crossref/Semantic Scholar citation normalization and checking."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any, Iterable, Mapping, Sequence

from .security import validate_evidence_reference


def normalize_doi(value: str | None) -> str | None:
    if not value:
        return None
    doi = value.strip().casefold()
    doi = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", doi)
    doi = re.sub(r"^doi:\s*", "", doi).strip()
    doi = doi.rstrip(".,;:)]}")
    return doi if re.fullmatch(r"10\.\d{4,9}/\S+", doi) else None


def normalize_title(value: str | None) -> str:
    value = unicodedata.normalize("NFKC", value or "").casefold()
    value = re.sub(r"[^\w\s]", " ", value, flags=re.UNICODE)
    return " ".join(value.split())


def normalize_author(value: str | Mapping[str, Any]) -> str:
    if isinstance(value, Mapping):
        name = " ".join(str(value.get(k, "")) for k in ("family", "given", "name") if value.get(k))
    else:
        name = str(value)
    return " ".join(re.sub(r"[^\w\s]", " ", unicodedata.normalize("NFKC", name).casefold()).split())


def normalize_year(value: Any) -> int | None:
    if isinstance(value, int) and 1000 <= value <= 9999:
        return value
    if isinstance(value, str):
        found = re.search(r"\b(\d{4})\b", value)
        return int(found.group(1)) if found else None
    if isinstance(value, Mapping):
        parts = value.get("date-parts") or value.get("date_parts")
        if isinstance(parts, Sequence) and parts and isinstance(parts[0], Sequence) and parts[0]:
            try:
                return int(parts[0][0])
            except (TypeError, ValueError):
                return None
    return None


@dataclass(frozen=True)
class CitationInput:
    title: str | None = None
    authors: tuple[str, ...] = ()
    year: int | None = None
    journal: str | None = None
    doi: str | None = None

    def normalized(self) -> "CitationInput":
        return CitationInput(
            title=normalize_title(self.title),
            authors=tuple(normalize_author(x) for x in self.authors if normalize_author(x)),
            year=normalize_year(self.year),
            journal=normalize_title(self.journal),
            doi=normalize_doi(self.doi),
        )


@dataclass(frozen=True)
class PaperMetadata:
    title: str | None
    authors: tuple[str, ...]
    year: int | None
    journal: str | None
    doi: str | None
    source: str

    def normalized(self) -> "PaperMetadata":
        return PaperMetadata(
            title=normalize_title(self.title),
            authors=tuple(normalize_author(x) for x in self.authors if normalize_author(x)),
            year=normalize_year(self.year),
            journal=normalize_title(self.journal),
            doi=normalize_doi(self.doi),
            source=self.source,
        )


@dataclass(frozen=True)
class FieldMatch:
    matched: bool | None
    reason: str


@dataclass
class PaperCheck:
    input_metadata: CitationInput
    candidate: PaperMetadata | None
    field_matches: dict[str, FieldMatch]
    existence_status: str  # found | not_found | uncertain | error
    claim_support_status: str = "not_checked"  # supported | not_supported | conflicting | insufficient_evidence | not_checked
    claim_support_reason: str = ""
    lookup_note: str = ""


def metadata_from_record(record: Mapping[str, Any], *, source: str) -> PaperMetadata:
    """Read common Crossref and Semantic Scholar response shapes without trusting them."""
    titles = record.get("title")
    title = titles[0] if isinstance(titles, Sequence) and not isinstance(titles, str) and titles else titles
    authors_raw = record.get("authors") or record.get("author") or []
    authors = tuple(normalize_author(item) for item in authors_raw if normalize_author(item)) if isinstance(authors_raw, Sequence) else ()
    journal = record.get("container-title") or record.get("venue") or record.get("journal")
    year = normalize_year(record.get("published") or record.get("published-print") or record.get("publicationDate") or record.get("year"))
    return PaperMetadata(title=title, authors=authors, year=year, journal=journal, doi=record.get("DOI") or record.get("doi"), source=source).normalized()


def _authors_match(left: tuple[str, ...], right: tuple[str, ...]) -> bool | None:
    if not left or not right:
        return None
    left_keys = {x.split()[-1] for x in left}
    right_keys = {x.split()[-1] for x in right}
    return bool(left_keys & right_keys)


def compare_metadata(citation: CitationInput, candidate: PaperMetadata) -> tuple[dict[str, FieldMatch], bool]:
    wanted, got = citation.normalized(), candidate.normalized()
    title_ratio = SequenceMatcher(None, wanted.title or "", got.title or "").ratio() if wanted.title and got.title else None
    matches = {
        "doi": FieldMatch(None if not wanted.doi or not got.doi else wanted.doi == got.doi, "DOI exact after normalization"),
        "title": FieldMatch(None if title_ratio is None else title_ratio >= 0.92, f"title similarity={title_ratio:.3f}" if title_ratio is not None else "title unavailable"),
        "authors": FieldMatch(_authors_match(wanted.authors, got.authors), "at least one normalized family name overlaps"),
        "year": FieldMatch(None if wanted.year is None or got.year is None else wanted.year == got.year, "publication year comparison"),
    }
    doi_ok = matches["doi"].matched is True if wanted.doi else True
    title_ok = matches["title"].matched is True
    author_ok = matches["authors"].matched in (True, None)
    year_ok = matches["year"].matched in (True, None)
    return matches, bool(doi_ok and title_ok and author_ok and year_ok)


def build_paper_check(citation: CitationInput, records: Iterable[PaperMetadata], *, lookup_error: bool = False) -> PaperCheck:
    """Choose a strict candidate; no result means uncertain operationally, not proof of fiction."""
    normalized_input = citation.normalized()
    candidates = list(records)
    if lookup_error:
        return PaperCheck(normalized_input, None, {}, "uncertain", lookup_note="provider lookup failed")
    for candidate in candidates:
        matches, accepted = compare_metadata(normalized_input, candidate)
        if accepted:
            return PaperCheck(normalized_input, candidate.normalized(), matches, "found", lookup_note="metadata matched")
    if candidates:
        # Preserve mismatch details for the first candidate; this is not existence proof.
        matches, _ = compare_metadata(normalized_input, candidates[0])
        return PaperCheck(normalized_input, candidates[0].normalized(), matches, "not_found", lookup_note="candidates returned but metadata did not match")
    return PaperCheck(normalized_input, None, {}, "uncertain", lookup_note="no matching record returned; databases are incomplete")


def assess_claim_support(
    check: PaperCheck,
    *,
    evidence: Mapping[str, str],
    relations: Mapping[str, str],
    excerpts: Mapping[str, str],
) -> PaperCheck:
    """Assess the cited conclusion separately from paper existence.

    ``relations`` must come from a constrained judge and ``excerpts`` must be
    exact members of the saved evidence text; otherwise support remains unknown.
    """
    valid: list[str] = []
    for evidence_id, excerpt in excerpts.items():
        if validate_evidence_reference(evidence_id, excerpt, evidence):
            valid.append(evidence_id)
    if not valid:
        check.claim_support_status = "insufficient_evidence"
        check.claim_support_reason = "no allowlisted evidence excerpt was validated"
        return check
    support = any(relations.get(item) in {"supports", "partially_supports"} for item in valid)
    refute = any(relations.get(item) == "refutes" for item in valid)
    if support and refute:
        check.claim_support_status = "conflicting"
    elif support:
        check.claim_support_status = "supported"
    elif refute:
        check.claim_support_status = "not_supported"
    else:
        check.claim_support_status = "insufficient_evidence"
    check.claim_support_reason = "validated excerpts and constrained evidence relations"
    return check
