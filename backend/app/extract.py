"""Deterministic extraction fallback with JavaScript UTF-16 offsets."""

from __future__ import annotations

import re

from .schemas import Claim, ClaimLabel, ClaimState, new_id
from .text_processing import TextSpan
from .claim_segmentation import atomic_spans
from .reference_resolution import has_unresolved_reference, resolve_references


def utf16_len(value: str) -> int:
    return len(value.encode("utf-16-le")) // 2


def utf16_offset(value: str, codepoint_offset: int) -> int:
    return utf16_len(value[:codepoint_offset])


def _kind(sentence: str) -> tuple[str, bool]:
    # Epistemic qualifiers do not make a claim an opinion: "可能发生" is
    # still a falsifiable claim.  Only explicit advice/opinion language is
    # excluded from factual verification.
    if re.search(r"观点|建议|我认为|值得|应当(?:考虑|注意)|推荐", sentence):
        return "opinion", False
    if re.search(r"论文|研究表明|期刊|DOI|doi", sentence, re.I):
        return "paper_citation", True
    if re.search(r"\d+(?:\.\d+)?\s*(?:%|％|万人|亿元|年|月|次|倍|米|kg|公斤)", sentence, re.I):
        return "statistic", True
    return "general", True


def extract_claims(text: str, task_id: str, limit: int = 15, *, spans: list[TextSpan] | None = None) -> tuple[list[Claim], bool]:
    """Split conservatively; offsets are UTF-16 half-open offsets in original text."""
    spans = atomic_spans(text) if spans is None else spans
    spans = resolve_references(text, spans)
    truncated = len(spans) > limit
    claims: list[Claim] = []
    for span in spans[:limit]:
        kind, verifiable = _kind(span.normalized)
        claim = Claim(
            claim_id=new_id("c"), task_id=task_id, source_text=span.source,
            char_start=utf16_offset(text, span.start), char_end=utf16_offset(text, span.end),
            type=kind, normalized_claim=span.normalized, state=ClaimState.PENDING,
            entities=_entities(span.normalized), conditions=_conditions(span.normalized),
            label=None if verifiable else ClaimLabel.NOT_APPLICABLE,
        )
        if has_unresolved_reference(span.source, span.normalized):
            claim.state = ClaimState.UNCHECKED
            claim.reason = "“该学科”的指代对象无法从相邻原文唯一确定，未进行检索核验。"
        claims.append(claim)
    return claims, truncated


def _entities(text: str) -> list[str]:
    """Find bounded subject/name anchors without treating a whole claim as one entity."""
    found: list[str] = []
    found.extend(re.findall(r"[“\"「『]([^”\"」』]{1,40})[”\"」』]", text))
    identifier_text = re.sub(r"(?:https?://|10\.\d{4,9}/)\S+", " ", text, flags=re.I)
    found.extend(re.findall(r"\b[A-Za-z][A-Za-z0-9]*(?:[-_/][A-Za-z0-9]+)+(?:\.[A-Za-z0-9]+)*\b", identifier_text))
    subject = r"[\u4e00-\u9fffA-Za-z0-9·_-]{2,24}"
    for match in re.finditer(rf"(?:根据|关于|针对|对于|由|来自)\s*({subject})", text):
        value = match.group(1)
        # Single characters such as ``为`` and ``达`` are valid parts of an
        # organisation name (华为, 英伟达). Only use boundaries that carry
        # clear syntactic context here.
        value = re.split(r"(?=(?:在|于|的|是|将|发布|表示|称|超过|低于))", value, maxsplit=1)[0]
        value = re.sub(r"\d{4}(?:年|[-/]\d{1,2}(?:月|[-/]\d{1,2}日?)?)?$", "", value)
        if len(value.strip()) >= 2:
            found.append(value.strip())
    noun = re.match(r"^([\u4e00-\u9fff]{2,20}?)(?=是|为|将|发布|表示|称)", text)
    if noun:
        found.append(noun.group(1))
    leading = re.match(r"^([\u4e00-\u9fff]{2,12})(?=(?:在|于|的|是|将|发布|表示|称|超过|低于))", text)
    if leading:
        found.append(leading.group(1))
    cleaned = list(dict.fromkeys(x.strip() for x in found if x.strip()))
    return [item for item in cleaned if not any(item != other and item in other for other in cleaned)][:8]


def _conditions(text: str) -> list[str]:
    """Keep applicability/date/range phrases as compact retrieval anchors."""
    found: list[str] = []
    for match in re.finditer(r"((?:在|当|截至|按照|仅在|适用于|适用范围为)[^，,。；！？!?\n]{1,30}?)(?=，|,|的|时|期间|范围|：|:|$)", text):
        value = match.group(1).strip()
        if len(value) > 2:
            found.append(value)
    for match in re.finditer(r"(?:\d{4}(?:年|[-/]\d{1,2}(?:月|[-/]\d{1,2}日?)?)|截至\s*\d{4})", text):
        found.append(match.group(0).strip())
    return list(dict.fromkeys(found))[:8]


def count_claim_candidates(text: str) -> int:
    """Count the same valid candidate spans used by :func:`extract_claims`."""
    return len(atomic_spans(text))
