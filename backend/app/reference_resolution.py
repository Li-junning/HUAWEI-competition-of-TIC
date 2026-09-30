"""Resolve high-confidence references before evidence retrieval.

The source span and its offsets remain unchanged. A replacement is allowed
only when the immediately preceding sentence states a single antecedent.
"""

from __future__ import annotations

import re

from .text_processing import TextSpan, candidate_spans, normalize_claim
from .claim_segmentation import split_atomic_span


_SCHOOL = r"[\u3400-\u9fffA-Za-z·]{2,20}(?:大学|学院|学校)"
_DISCIPLINE = re.compile(
    rf"(?P<school>{_SCHOOL})(?:(?:上榜|入选|开设|设立|拥有)的|的)?"
    r"学科(?:是|为)(?P<field>[^，,。！？!?；;：:\n]{2,30})"
)
_REFERENCE = re.compile(r"^(?:该|这一|这个)学科(?=在|于|以|的|是|为|位列|排名|获得|入选|上榜)")
_MULTIPLE_FIELDS = re.compile(r"[、/]|(?:和|及|与|以及)")
_PERSONAL_REFERENCE = re.compile(r"^[它他她](?:们)?(?=最终|主要|通常|在|于|的|是|为|位于|成立|创立|出生|毕业|流入|注入|发源|拥有|支持|能|不)")


def _antecedent(sentence: str) -> str | None:
    candidates: set[str] = set()
    for match in _DISCIPLINE.finditer(sentence):
        field = match.group("field").strip()
        if field.endswith("学科"):
            field = field[:-2]
        if not field or _MULTIPLE_FIELDS.search(field):
            continue
        candidates.add(f'{match.group("school")}的{field}学科')
    return next(iter(candidates)) if len(candidates) == 1 else None


def resolve_references(text: str, spans: list[TextSpan]) -> list[TextSpan]:
    """Return claims with explicit subjects where the local source is unique."""
    sentences = candidate_spans(text)
    resolved: list[TextSpan] = []
    for span in spans:
        match = _REFERENCE.match(span.source)
        personal = _PERSONAL_REFERENCE.match(span.source)
        parent_index = next((i for i, sentence in enumerate(sentences)
                             if sentence.start <= span.start < sentence.end), None)
        if (match is None and personal is None) or parent_index is None:
            resolved.append(span)
            continue
        parent = sentences[parent_index]
        if span.start > parent.start:
            # A semantic split may leave the antecedent in an earlier clause.
            context = text[parent.start:span.start]
        elif parent_index > 0:
            previous = sentences[parent_index - 1]
            context = previous.source if "\n" not in text[previous.end:parent.start] else ""
        else:
            context = ""
        if personal:
            # Reuse the same conservative subject-continuity check as local
            # splitting, but preserve this sentence's original source range.
            combined = context.rstrip("。.!！?？；;，,") + "，" + span.source
            parts = split_atomic_span(TextSpan(0, len(combined), combined, normalize_claim(combined))) if context else []
            normalized = parts[-1].normalized if len(parts) > 1 and parts[-1].source == span.source else span.normalized
            resolved.append(TextSpan(span.start, span.end, span.source, normalized))
            continue
        antecedent = _antecedent(context)
        if antecedent is None:
            resolved.append(span)
            continue
        normalized = normalize_claim(span.normalized)
        replacement = re.sub(r"^(?:该|这一|这个)学科", antecedent, normalized, count=1)
        resolved.append(TextSpan(span.start, span.end, span.source, replacement))
    return resolved



def has_unresolved_reference(source: str, normalized: str) -> bool:
    """A leading discipline reference still lacks a source-backed subject."""
    return bool((_REFERENCE.match(source) and _REFERENCE.match(normalized))
                or (_PERSONAL_REFERENCE.match(source) and _PERSONAL_REFERENCE.match(normalized)))
