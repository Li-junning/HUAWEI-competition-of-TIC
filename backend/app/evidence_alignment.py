"""Conservative guards for quotes that exist but do not entail an assertion.

These checks only veto model decisions. They never infer a new fact, correct an
answer, or assign authority from a matching word. Unsupported syntax stays with
the semantic classifier, whose explicit alignment dimensions are also required.
"""

from __future__ import annotations

import re
import unicodedata
from fractions import Fraction

from .fact_queries import fact_slot
from .schemas import EvidenceRelation
from .text_processing import candidate_spans


def compact(text: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text)).casefold()


def _literal(text: str) -> str:
    return compact(text).strip("。.!！?？；;，,")


_QUALIFIERS = re.compile(
    r"据称|传闻|网传|有人声称|可能|或许|预计|有望|计划|如果|假如|仅在|只有|只要|"
    r"在[^，,。；;]{1,25}(?:条件下|情况下|大气压下|期间)|"
    r"\b(?:if|unless|may|might|could|allegedly|reportedly|plans? to)\b", re.I,
)
_NEGATION = re.compile(
    r"不能|无法|并非|不是|不会|没有|未曾|尚未|不支持|不属于|不在|"
    r"\b(?:cannot|can't|does not|do not|is not|are not|never|no longer)\b", re.I,
)
_DATE = re.compile(r"(?<!\d)(\d{4})\s*年(?:\s*(\d{1,2})\s*月(?:\s*(\d{1,2})\s*日)?)?")
_COMPARISON = re.compile(r"超过|大于|小于|低于|高于|至少|至多|不少于|不超过|约|大约|[<>≥≤~～]|\b(?:over|under|at least|about)\b", re.I)
_REQUIRED_SCOPE = re.compile(r"仅在|只有|只要|如果|假如|在[^，,。；;]{1,25}(?:条件下|情况下|大气压下|期间)")
_METRICS = (
    ("研发投入", "研发费用", "研发支出"), ("营收", "营业收入"),
    ("净利润",), ("员工人数", "员工数量"), ("用户数", "用户数量"),
    ("沸点", "boiling point"), ("声速", "声音传播速度", "speed of sound"),
)
_CAUSAL = re.compile(r"因为|由于|因此|从而|导致|使得|原因|因(?=[^。]{1,30}而)|\b(?:because|causes?|caused|therefore|due to)\b", re.I)
_UNIVERSAL = re.compile(r"所有|全部|每个|任何|\b(?:all|every|always)\b", re.I)
_PARTICULAR = re.compile(r"部分|一些|有些|多数|\b(?:some|many|sometimes)\b", re.I)

# Conversion is restricted to equivalent units of the same dimension/currency.
_UNITS = {
    "亿元": ("CNY", "100000000"), "万元": ("CNY", "10000"), "元": ("CNY", "1"),
    "亿美元": ("USD", "100000000"), "万美元": ("USD", "10000"), "美元": ("USD", "1"),
    "摄氏度": ("Celsius", "1"), "°c": ("Celsius", "1"),
    "千米": ("length", "1000"), "公里": ("length", "1000"), "km": ("length", "1000"),
    "厘米": ("length", ".01"), "米": ("length", "1"),
    "千克": ("mass", "1"), "公斤": ("mass", "1"), "kg": ("mass", "1"), "克": ("mass", ".001"),
    "万人": ("people", "10000"), "人": ("people", "1"),
    "百分比": ("percent", "1"), "%": ("percent", "1"),
    "米/秒": ("speed", "1"), "m/s": ("speed", "1"),
    "千米/小时": ("speed", "5/18"), "km/h": ("speed", "5/18"),
}
_QUANTITY = re.compile(
    r"(?<![\w.])([+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)\s*("
    + "|".join(re.escape(unit) for unit in sorted(_UNITS, key=len, reverse=True)) + r")(?![A-Za-z/])", re.I,
)


def _quantities(text: str) -> set[tuple[str, Fraction]]:
    text = unicodedata.normalize("NFKC", text).casefold()
    # CJK adjacent characters do not delimit numbers, unlike identifiers.
    text = re.sub(r"(?<=[\u3400-\u9fff])(?=[+-]?\d)", " ", text)
    values = set()
    for match in _QUANTITY.finditer(text):
        dimension, factor = _UNITS[match.group(2)]
        value = Fraction(match.group(1).replace(",", "")) * Fraction(factor)
        values.add((dimension, value))
    return values


def _dates_cover(required: list[tuple[str, str, str]], observed: list[tuple[str, str, str]]) -> bool:
    return all(any(all(not field or (other and int(field) == int(other))
                       for field, other in zip(date, candidate)) for candidate in observed) for date in required)


def _subject_boundary(source: str, start: int) -> bool:
    prefix = source[:start]
    if not prefix or prefix[-1] in "，,。；;：:":
        return True
    prefix = re.split(r"[，,。；;：:]", prefix)[-1]
    return bool(re.fullmatch(r"(?:在|截至)?\d{4}年(?:\d{1,2}月(?:\d{1,2}日)?)?", prefix))


def _metric_subject(part: str, cues: tuple[str, ...]) -> str | None:
    target = compact(part)
    positions = [target.find(compact(cue)) for cue in cues if compact(cue) in target]
    if not positions:
        return None
    prefix = target[:min(positions)]
    date = r"\d{4}年(?:\d{1,2}月(?:\d{1,2}日)?)?"
    prefix = re.sub(r"^(?:在|截至)?" + date + r"[，,]?", "", prefix)
    prefix = re.sub(r"(?:在|于)?" + date + r"的?$", "", prefix).removesuffix("的")
    if (not re.fullmatch(r"[\u3400-\u9fffA-Za-z0-9_-]{1,40}", prefix)
            or re.search(r"^(?:在|当|如果|由于|因为)|与|和|并|且|属于", prefix)):
        return None
    return prefix


def _metric_clauses(part: str, context: str, cues: tuple[str, ...]) -> list[str]:
    source = compact(context)
    cue = "|".join(re.escape(compact(value)) for value in cues)
    subject = _metric_subject(part, cues)
    date = r"(?:在|截至)?\d{4}年(?:\d{1,2}月(?:\d{1,2}日)?)?"
    prefix = re.escape(subject) + r"(?:(?:在|于)?" + date + r")?的?" if subject else ""
    clauses = []
    all_metrics = "|".join(re.escape(compact(value)) for group in _METRICS for value in group)
    for match in re.finditer(prefix + r"(?:" + cue + r")", source):
        if subject and not _subject_boundary(source, match.start()):
            continue
        left = max((source.rfind(mark, 0, match.start()) for mark in "，,。；;"), default=-1) + 1
        # Inherit only an adjacent explicit date clause, never another fact.
        if left:
            previous = max((source.rfind(mark, 0, left - 1) for mark in "，,。；;"), default=-1) + 1
            if re.fullmatch(date, source[previous:left - 1]):
                left = previous
        right = min(len(source), match.end() + 100)
        for index in range(match.end(), right):
            if source[index] in "，,。；;":
                if source[index] == "," and index and index + 1 < len(source) and source[index - 1].isdigit() and source[index + 1].isdigit():
                    continue
                right = index
                break
        next_metric = re.search(all_metrics, source[match.end():right])
        if next_metric:
            right = match.end() + next_metric.start()
        clauses.append(source[left:right])
    return clauses


def quote_contexts(text: str, quote: str) -> list[str]:
    """Inspect every occurrence's complete sentence, including clipped negation.

    Do not borrow a subject from a distant sentence. Ambiguous repeated quotes
    must pass in all their contexts instead of choosing the convenient one.
    """
    spans = candidate_spans(text)
    contexts = []
    start = text.find(quote)
    while start >= 0 and len(contexts) <= 8:
        end = start + len(quote)
        overlapping = [span for span in spans if span.start < end and span.end > start]
        if overlapping:
            contexts.append(text[overlapping[0].start:overlapping[-1].end])
        else:
            contexts.append(text)
        start = text.find(quote, start + 1)
    return contexts


def _property_assertion(part: str, context: str, quote: str) -> tuple[str, str] | None:
    """Bind an explicitly recognizable property to its own grammatical subject."""
    slot = fact_slot(part)
    if slot is None:
        return None
    # 就读 does not establish graduation; generic 分类/科 cannot prove 属于.
    cues = {
        "毕业院校": ("毕业于", "毕业"),
        "成立时间": ("成立于", "创立于", "创办于", "始建于", "成立", "创立", "创办", "始建"),
        "出生地点 时间": ("出生于", "诞生于", "生于", "出生", "诞生"),
        "地址 地理位置": ("位于", "坐落于", "坐落", "地处", "地址", "校址", "位置"),
        "所属类别": ("属于", "归属"),
    }.get(slot.property_name, slot.cues)
    cue = "|".join(re.escape(value) for value in sorted(cues, key=len, reverse=True))
    pattern = re.compile(
        re.escape(compact(slot.subject))
        + r"(?:的)?(?:(?:最终|最后|主要|通常|目前|曾经|已经|约)的?)*"
        + r"(?:" + cue + r")(?:于|在|为|是|：|:)?"
        + r"([^，,。；;！？!?]+(?:[，,](?:而非|而不是|不是|并非|不在)[^，,。；;！？!?]+)?)", re.I,
    )
    # This binds the property to the same subject. A school name inside its
    # affiliated hospital's name cannot satisfy the school location check.
    source = compact(context)
    matches = list(pattern.finditer(source))
    grounded = [m for m in matches if _subject_boundary(source, m.start())
                and (compact(quote) in m.group() or m.group() in compact(quote))]
    if len(grounded) != 1:
        return ("", "")
    # Parse the claimed answer with the same property vocabulary.
    target = pattern.search(compact(part))
    return (target.group(1).strip("。.!，,"), grounded[0].group(1).strip("。.!，,")) if target else None


def alignment_problem(part: str, text: str, quote: str, relation: EvidenceRelation, *, whole_claim: str = "") -> str | None:
    """Return a reason to veto a direct relation, never a replacement verdict."""
    contexts = quote_contexts(text, quote)
    if not contexts or len(contexts) > 8:
        return "引用位置不唯一或无法定位完整语境"
    claim_scope = whole_claim or part
    scope_only = bool(_REQUIRED_SCOPE.fullmatch(_literal(part)))
    if scope_only and relation == EvidenceRelation.REFUTES:
        return "条件短语不能脱离完整事实单独构成反证"
    for context in contexts:
        extra_scope = [m.group() for m in _QUALIFIERS.finditer(context) if compact(m.group()) not in compact(claim_scope)]
        if extra_scope:
            return "引用上下文含原声明未覆盖的条件、推测或转述"
        if any(compact(m.group()) not in compact(context) for m in _REQUIRED_SCOPE.finditer(claim_scope)):
            return "证据未覆盖声明的适用条件"
        if relation == EvidenceRelation.SUPPORTS and not scope_only and bool(_NEGATION.search(part)) != bool(_NEGATION.search(context)):
            return "引用上下文与声明的否定关系不一致"
        if (relation == EvidenceRelation.REFUTES and _literal(part) in _literal(quote)
                and bool(_NEGATION.search(part)) == bool(_NEGATION.search(context))):
            return "引用重复同一声明且未呈现否定，不能构成反证"
        if _CAUSAL.search(part) and not _CAUSAL.search(context):
            return "引用仅描述相关事实，未明确证明待核验的因果关系"
        if relation == EvidenceRelation.SUPPORTS and _UNIVERSAL.search(part) and _PARTICULAR.search(context) and not _UNIVERSAL.search(context):
            return "部分个体或部分场景不能支持全称声明"
        slot = fact_slot(part)
        assertion = _property_assertion(part, context, quote)
        if slot and assertion == ("", ""):
            return "引用未将目标属性明确绑定到同一主体"
        if assertion:
            target, answer = assertion
            if relation == EvidenceRelation.SUPPORTS:
                target_dates = _DATE.findall(target)
                if target_dates and not _dates_cover(target_dates, _DATE.findall(answer)):
                    return "目标属性的日期与引用不一致或精度不足"
                if not target_dates and _literal(target) not in _literal(answer):
                    return "引用未明确覆盖声明给出的属性答案"
            elif relation == EvidenceRelation.REFUTES:
                # Containment can describe a more specific geographical level;
                # it is not an exclusive alternative without explicit denial.
                same_answer = _literal(target) in _literal(answer) or _literal(answer) in _literal(target)
                target_dates, answer_dates = _DATE.findall(target), _DATE.findall(answer)
                if target_dates and answer_dates:
                    same_answer = same_answer or _dates_cover(target_dates, answer_dates) or _dates_cover(answer_dates, target_dates)
                explicit_denial = bool(re.search(r"(?:而非|不是|不在|并非)" + re.escape(_literal(target)), _literal(answer)))
                if same_answer and not explicit_denial:
                    return "相同答案或包含关系不能构成互斥反证"
        # A founding/birth date is a property value, not the observation period.
        scope_text = slot.scope if slot else part
        dates = _DATE.findall(scope_text)
        if dates and not _dates_cover(dates, _DATE.findall(quote)):
            return "证据未覆盖声明要求的时间范围"
        required = _quantities(part)
        observed = _quantities(quote)
        for group in _METRICS:
            if not any(cue.casefold() in part.casefold() for cue in group):
                continue
            # Keep a quantity with its own metric instead of borrowing a number
            # from a different column/action in the same sentence.
            if not any(compact(cue) in compact(context) for cue in group):
                return "引用讨论的统计指标或目标属性不同"
            clauses = _metric_clauses(part, context, group)
            if not clauses:
                return "目标指标未明确绑定到同一主体"
            clauses = [clause for clause in clauses if compact(quote) in compact(clause) or compact(clause) in compact(quote)]
            if not clauses:
                return "引用未定位到声明要求的指标，不能借用其他指标的数据"
            if dates:
                clauses = [clause for clause in clauses if _dates_cover(dates, _DATE.findall(clause))]
                if not clauses:
                    return "目标指标的时间范围与声明不一致"
            if required and not _COMPARISON.search(part):
                metric_values = set().union(*(_quantities(clause) for clause in clauses))
                if relation == EvidenceRelation.SUPPORTS and not required.issubset(metric_values):
                    return "目标指标的数值未获支持，不能借用其他指标的数据"
                if relation == EvidenceRelation.REFUTES and not metric_values:
                    return "目标指标缺少可比较的数值，不能构成反证"
                if relation == EvidenceRelation.REFUTES and required == metric_values and bool(_NEGATION.search(part)) == bool(_NEGATION.search(context)):
                    return "目标指标的等价数值不能构成反证"
        if required and not _COMPARISON.search(part):
            if relation == EvidenceRelation.SUPPORTS and not required.issubset(observed):
                return "引用中的数值或单位未覆盖声明，不能仅按相同数字匹配"
            if relation == EvidenceRelation.REFUTES:
                if not observed or {d for d, _ in required} != {d for d, _ in observed}:
                    return "不同单位、指标或缺少数值不能构成直接反证"
                if required == observed and bool(_NEGATION.search(part)) == bool(_NEGATION.search(context)):
                    return "等价数值及单位不能构成反证"
    return None


def literal_support(part: str, text: str, quote: str, *, whole_claim: str = "") -> bool:
    """Compatibility path: only complete literal assertions can skip dimensions."""
    return bool(_literal(part) and _literal(part) in _literal(quote)
                and alignment_problem(part, text, quote, EvidenceRelation.SUPPORTS, whole_claim=whole_claim) is None)


def explicit_property_refutation(part: str, text: str, quote: str, *, whole_claim: str = "") -> bool:
    """A grounded explicit denial can verify a legacy single-property decision."""
    if alignment_problem(part, text, quote, EvidenceRelation.REFUTES, whole_claim=whole_claim) is not None:
        return False
    contexts = quote_contexts(text, quote)
    return bool(contexts and all(
        (assertion := _property_assertion(part, context, quote))
        and assertion != ("", "")
        and re.search(r"(?:而非|不是|不在|并非)" + re.escape(_literal(assertion[0])), _literal(assertion[1]))
        for context in contexts
    ))
