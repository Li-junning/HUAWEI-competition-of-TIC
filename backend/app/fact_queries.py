"""Answer-neutral property queries and relevance checks, independent of entities."""

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class FactSlot:
    subject: str
    scope: str
    property_name: str
    cues: tuple[str, ...]

    @property
    def query(self) -> str:
        return " ".join(part for part in (self.scope, self.subject, self.property_name) if part)

    def matches(self, text: str, quote: str | None = None) -> bool:
        compact = lambda value: re.sub(r"\s+", "", value).casefold()
        return (compact(self.subject) in compact(text)
                and any(cue in (quote if quote is not None else text) for cue in self.cues))

    def match_strength(self, text: str) -> int:
        if not self.matches(text):
            return 0
        for cue in self.cues:
            suffix = r"(?=于|在|时间|日期|[：:\d])" if self.property_name == "成立时间" else ""
            direct = re.escape(self.subject) + r"\s*(?:(?:最终|主要|通常|目前|曾经|已经|约)\s*)*" + re.escape(cue) + suffix
            if re.search(direct, text):
                return 3
        return 2 if any(cue in text for cue in self.cues[:3]) else 1


_SLOTS = (
    (("最终流入", "流入", "注入", "汇入"), "流入地点 入海口", ("流入", "注入", "汇入", "入海", "河口")),
    (("发源于", "起源于"), "发源地", ("发源", "起源", "源头", "源地")),
    (("总部位于", "总部设在"), "总部位置", ("总部",)),
    (("出生于", "诞生于"), "出生地点 时间", ("出生", "诞生", "生于")),
    (("成立于", "创立于", "创办于"), "成立时间", ("成立", "创立", "创办", "始建", "组建")),
    (("毕业于",), "毕业院校", ("毕业", "就读", "学位")),
    (("迁至", "迁往"), "迁往地点", ("迁至", "迁往", "迁入", "迁址")),
    (("位于", "坐落于"), "地址 地理位置", ("位于", "坐落", "地处", "地址", "校址", "位置")),
    (("属于",), "所属类别", ("属于", "归属", "分类", "类别", "纲", "科", "目")),
)


def fact_slot(text: str) -> FactSlot | None:
    text = text.strip().rstrip("。.!！?？；;，,")
    if re.search(r"[，,；;]|因为|由于|如果|虽然|据称|表示|认为|可能|《|》", text):
        return None
    for verbs, label, cues in _SLOTS:
        pattern = r"^(.+?)(?:(?:最终|主要|通常|目前|最早|已经|曾经|约)\s*)?(?:" + "|".join(verbs) + r")(.+)$"
        match = re.fullmatch(r"(.+?)\s+" + re.escape(label), text) or re.match(pattern, text)
        if not match:
            continue
        subject = match.group(1).strip()
        scope_match = re.match(r"^(?:\d{4}年(?:\d{1,2}月)?)", subject)
        scope = scope_match.group() if scope_match else ""
        subject = subject[len(scope):].strip()
        if (len(subject) < 2 or len(subject) > 40
                or re.match(r"^(?:它|他|她|其|这|该|在|当|由于|因为)", subject)
                or re.search(r"不|未|并|且|而|的", subject)):
            continue
        return FactSlot(subject, scope, label, cues)
    return None


def causal_query(text: str) -> str | None:
    """Search a stated outcome's cause without inserting the proposed cause."""
    match = re.fullmatch(r"([^，,。；;]{2,30}?)(?:因为|由于|因)([^，,。；;]+)而([^，,。；;]{2,20})[。.]?", text.strip())
    if match and not re.search(r"如果|可能|据称|报告称|表示|认为|[“”\"]", text):
        return f"{match.group(1)} {match.group(3)} 原因"
    return None
