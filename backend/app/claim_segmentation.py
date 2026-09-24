"""Conservative atomic claims, separate from evidence sentence boundaries.

Only original text is used to restore an omitted subject. Source spans stay
verbatim; the shared prefix belongs solely to the normalized search claim.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .text_processing import TextSpan, candidate_spans, normalize_claim


# Relations whose scope cannot safely be reconstructed by these local rules.
_DEPENDENCY = re.compile(
    r"如果|假如|若|只有|只要|除非|否则|即使|虽然|但是|但|然而|因为|由于|所以|因此|从而|"
    r"使得|导致|[，,]让|以便|以至于|为了|仅在|在[^，,。]{1,30}(?:下|时)|"
    r"不是|而是|并非|并不|没有|未曾|尚未|不得|不能|不但|不仅|相比|比起|分别|依次|"
    r"据称|声称|据报道|研究表明|报告称|表示|认为|建议|可能|或许|预计|有望|计划|试图|"
    r"不|未|无|“|”|\"|「|」|『|』"
)
_VERBS = (
    "签署", "签订", "割让", "宣布", "发布", "成立", "出生", "毕业", "位于", "属于",
    "拥有", "获得", "收购", "推出", "支持", "采用", "使用", "建成", "开通", "达到",
    "超过", "增长", "下降", "增加", "减少", "担任", "加入", "迁至", "迁往", "研发",
)
_ACTION = re.compile("|".join(_VERBS))
_CONNECTOR = re.compile(r"^(?:并且|而且|同时|并|且|也|还)")
_SUBJECT = re.compile(r"[\u3400-\u9fffA-Za-z0-9]")
_DATE = r"\d{4}年(?:\d{1,2}月(?:\d{1,2}日)?)?"
_TIME = re.compile(r"^(?:" + _DATE + r"|[^，,]{1,16}(?:时期|期间|年代))[，,]\s*")
_INLINE_TIME = re.compile(r"^(?:" + _DATE + r"|[^，,]{1,16}(?:时期|期间|年代))")


def requires_joint_context(source: str) -> bool:
    # Words inside a title (e.g. 未来) are not sentence-level qualifiers.
    # Parenthetical conditions still constrain the surrounding assertion.
    outside_titles = re.sub(r"《[^《》]*》", lambda m: " " * len(m.group()), source)
    return bool(_DEPENDENCY.search(outside_titles))


def _top_level(text: str) -> list[bool]:
    """Protect names/titles, parentheses and URLs from comma/verb matching."""
    mask = [True] * len(text)
    stack: list[str] = []
    pairs = {"（": "）", "(": ")", "[": "]", "【": "】", "《": "》"}
    for i, char in enumerate(text):
        if char in pairs:
            stack.append(pairs[char])
        if stack:
            mask[i] = False
        if stack and char == stack[-1]:
            stack.pop()
    for match in re.finditer(r"https?://[^\s，,。]+", text):
        mask[match.start():match.end()] = [False] * len(match.group())
    return mask


def _predicate(source: str) -> re.Match | None:
    mask = _top_level(source)
    return next((match for match in _ACTION.finditer(source) if mask[match.start()]), None)


def normalize_atomic_claim(source: str, context: str = "") -> str:
    """Remove clause joiners only in the derived claim; offsets stay verbatim."""
    body = _strip_connector(source.strip())
    return normalize_claim(context + body).rstrip("，,")


def _strip_connector(text: str) -> str:
    match = _CONNECTOR.match(text)
    if match:
        rest = text[match.end():].strip()
        # Do not strip a character from names such as 并州大学 or 也门.
        if not rest or _ACTION.match(rest) or re.match(r"^(?:在|于)", rest):
            return rest
    return text


@dataclass(frozen=True)
class SharedScope:
    time: str
    subject: str
    modifier: str = ""

    @property
    def prefix(self) -> str:
        return self.time + self.subject + self.modifier


def _scope(prefix: str, inherited_time: str = "") -> SharedScope | None:
    """Separate a subject from explicit time and pre-predicate location scope."""
    prefix = _strip_connector(prefix.strip())
    temporal = _TIME.match(prefix) or _INLINE_TIME.match(prefix)
    time = temporal.group() if temporal else inherited_time
    rest = prefix[temporal.end():] if temporal else prefix
    # A leading adverbial is not an explicit subject. Unsupported order stays
    # joint rather than inventing a subject from a place/date.
    if re.match(r"^(?:在|于|其|它|他|她|这|该|其中)", rest):
        return None
    parts = re.split(r"(?<=\S)(?=在|于)", rest, maxsplit=1)
    subject = parts[0].strip()
    if not _SUBJECT.search(subject):
        return None
    return SharedScope(time, subject, parts[1] if len(parts) > 1 else "")


def _clause_boundaries(source: str) -> list[int]:
    mask = _top_level(source)
    boundaries = [0]
    for match in re.finditer(r"[，,]|(?:并且|而且|同时|并|且|也|还)(?=" + _ACTION.pattern + r")", source):
        if mask[match.start()]:
            boundary = match.end() if match.group() in "，," else match.start()
            if boundary > boundaries[-1]:
                boundaries.append(boundary)
    if boundaries[-1] != len(source):
        boundaries.append(len(source))
    return boundaries


def split_atomic_span(span: TextSpan) -> list[TextSpan]:
    source = span.source
    if requires_joint_context(source):
        return [span]
    boundaries = _clause_boundaries(source)
    if len(boundaries) <= 2:
        return [span]

    first = _predicate(source[:boundaries[1]])
    time_match = _TIME.match(source)
    # An introductory time phrase is attached to the first actual assertion.
    if first is None and time_match and len(boundaries) > 3:
        boundaries.pop(1)
        first = _predicate(source[:boundaries[1]])
    if first is None:
        return [span]
    scope = _scope(source[:first.start()])
    if scope is None:
        return [span]
    result: list[TextSpan] = []
    for index, (left, right) in enumerate(zip(boundaries, boundaries[1:])):
        while left < right and source[left].isspace():
            left += 1
        while right > left and source[right - 1].isspace():
            right -= 1
        piece = source[left:right]
        predicate = _predicate(piece)
        if predicate is None:
            return [span]  # An object list/relative clause is not a new fact.
        context = ""
        if index:
            before = _strip_connector(piece[:predicate.start()].strip())
            if not before:
                context = scope.prefix
            elif re.match(r"^(?:在|于)", before):
                # A new adverbial is not a new subject. Only replace a simple
                # location; date/mixed scope needs semantic disambiguation.
                if re.search(r"\d|年|月|日|时期|期间|年代", before + scope.modifier):
                    return [span]
                context = scope.time + scope.subject
                scope = SharedScope(scope.time, scope.subject, before)
            else:
                next_scope = _scope(before, scope.time)
                if next_scope is None:
                    return [span]
                # A subject switch drops the previous subject/location. An
                # explicit new date supersedes the inherited sentence date.
                context = "" if _INLINE_TIME.match(before) else scope.time
                scope = next_scope
        result.append(TextSpan(span.start + left, span.start + right, piece,
                               normalize_atomic_claim(piece, context)))
    return result


def atomic_spans(text: str) -> list[TextSpan]:
    return [atomic for sentence in candidate_spans(text) for atomic in split_atomic_span(sentence)]
