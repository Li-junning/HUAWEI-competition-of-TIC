"""Offline-first retrieval planning, safe fetching, and simple reprint clustering."""

from __future__ import annotations

import hashlib
import inspect
import re
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable, Sequence
from urllib.parse import urljoin
from urllib.parse import urlsplit

from .security import SafeHttpClient, SecurityError, extract_readable_text, normalize_url
from .providers import SearchProvider, SearchResult
from .providers.base import ProviderError, SearchProviderError
from .schemas import EvidenceCluster as ApiEvidenceCluster
from .schemas import EvidenceItem as ApiEvidenceItem
from .schemas import EvidenceRelation
from .text_processing import candidate_spans, query_terms
from .source_policy import source_allowed, source_profile
from .fact_queries import causal_query, fact_slot
from .claim_segmentation import atomic_spans


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "").casefold()
    return " ".join(value.split())


def content_hash(content: str | bytes) -> str:
    data = content if isinstance(content, bytes) else content.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class RetrievalQuery:
    claim_id: str
    query: str


@dataclass
class EvidenceItem:
    evidence_id: str
    url: str
    title: str
    publisher: str | None
    published_at: str | None
    retrieved_at: str
    excerpt: str
    content_hash: str
    relation: str = "unknown"
    quality_reason: str = ""
    is_reprint: bool = False
    original_url: str | None = None


@dataclass
class EvidenceCluster:
    cluster_id: str
    items: list[EvidenceItem] = field(default_factory=list)
    independence_reason: str = ""


def build_queries(claim_id: str, *, entities: Sequence[str] = (), action: str = "", time_range: str = "", value: str = "", conditions: Sequence[str] = ()) -> list[RetrievalQuery]:
    """Generate at most three bounded, answer-neutral search queries.

    The first query retains the supplied claim wording when it is the only
    structure available.  Additional queries remove values and duplicate
    structural anchors, giving a search provider a way to find both support
    and refutation without preselecting an answer.
    """
    pieces = _unique_parts([*entities, action, time_range, value, *conditions])
    if not pieces:
        return []
    entity_parts = _unique_parts(entities)
    condition_parts = _unique_parts(conditions)
    action_text = " ".join(str(action).split()).strip()
    # Keep a compact exact query as the first variant.  Components already
    # present in a full claim are not appended a second time.
    # Full claims already contain their structural anchors. Short action
    # labels (发布, 事实, ...) need those fields appended in the historical
    # order used by the provider fixtures.
    action_has_anchor = any(
        _contains_text(action_text, part)
        for part in [*entity_parts, time_range, value, *condition_parts]
        if part
    )
    if action_has_anchor:
        base_parts = [action_text]
        for part in [*entity_parts, time_range, value, *condition_parts]:
            if part and not _contains_text(action_text, part):
                base_parts.append(part)
    else:
        base_parts = [part for part in [*entity_parts, action_text, time_range, value, *condition_parts] if part]
    base_parts = _dedupe_query_parts(base_parts)
    base = _bounded_query(base_parts)
    neutral_action = _neutral_action(action_text, [*entity_parts, *condition_parts], value)
    neutral_conditions = [_neutral_action(part, (), value) for part in condition_parts]
    neutral_parts = _dedupe_query_parts([*entity_parts, neutral_action, time_range, *neutral_conditions])
    neutral = _bounded_query(neutral_parts)
    context_parts = [*entity_parts, neutral_action, time_range]
    if neutral_conditions:
        context_parts.append(neutral_conditions[0])
    context = _bounded_query(_dedupe_query_parts(context_parts))
    # A named work/treaty can be researched independently of a possibly false
    # actor, date or place. Keep the full assertion for the semantic judge.
    titles = re.findall(r"《([^《》\n]{2,60})》", action_text)
    if titles:
        neutral = _bounded_query(_unique_parts(titles))
        context = neutral
    variants = [base, neutral, context]
    seen: set[str] = set()
    result: list[RetrievalQuery] = []
    for query in variants:
        key = re.sub(r"[\s。！？!?；;，,]+", "", normalize_text(query))
        if query and key not in seen:
            seen.add(key)
            result.append(RetrievalQuery(claim_id, query))
    return result[:3]


def _unique_parts(parts: Sequence[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in parts:
        part = " ".join(str(raw or "").split()).strip()
        key = normalize_text(part)
        if part and key not in seen:
            seen.add(key)
            out.append(part)
    return out


def _contains_text(container: str, part: str) -> bool:
    return bool(part and normalize_text(part) in normalize_text(container))


def _dedupe_query_parts(parts: Sequence[str]) -> list[str]:
    out: list[str] = []
    for part in parts:
        part = " ".join(str(part).split()).strip()
        if not part:
            continue
        if any(_contains_text(existing, part) for existing in out):
            continue
        tokens = part.split()
        if len(tokens) > 1 and all(any(_contains_text(existing, token) for existing in out) for token in tokens):
            continue
        # A later, longer full claim replaces a short structural prefix.
        out = [existing for existing in out if not _contains_text(part, existing)]
        out.append(part)
    return out


def _contains_part(parts: Sequence[str], value: str) -> bool:
    needle = normalize_text(value)
    return any(needle and (needle in normalize_text(existing) or normalize_text(existing) in needle) for existing in parts)


def _neutral_action(action: str, anchors: Sequence[str], value: str = "") -> str:
    """Remove answer-like values while preserving negation and scope wording."""
    slot = fact_slot(action)
    neutral = slot.query if slot else causal_query(action) or action
    neutral = re.sub(r"^(?:研究显示|研究表明|资料显示|报告显示|数据显示)[，,:：\s]*", "", neutral)
    # Turn directional/comparative answers into properties to look up. Keep
    # subjects and conditions, and never insert a presumed correct answer.
    neutral = re.sub(r"(?:自|从|由)[东南西北](?:方)?(?:向|往|到|至)[东南西北](?:方)?", "方向", neutral)
    neutral = re.sub(r"(?:从|自)[东南西北](?:方|边|面)?(?=升起|落下)", "", neutral)
    neutral = re.sub(r"(升起|落下)(?=[。！？!?；;，,\s]|$)", r"\1 方向", neutral)
    neutral = re.sub(r"(?:传播|运动|移动)(?:得)?最[快慢]", "速度", neutral)
    neutral = re.sub(r"(?<![\u3400-\u9fff])(?:因为|由于)", "", neutral)
    changed = neutral != action
    # For classification-style claims, the predicate value is the answer.
    match = re.match(r"^\s*(.+?)的(.+?)(?<!不)是(.+?)[。！？!?；;]?$", neutral)
    if match:
        neutral = f"{match.group(1)} {match.group(2)}"
        changed = True
    else:
        match = re.match(r"^\s*(.+?)(?<!不)是(.+?)的(.+?)[。！？!?；;]?$", neutral)
        if match:
            neutral = f"{match.group(2)} {match.group(3)}"
            changed = True
        elif re.fullmatch(r"[^，,。；;]{2,40}是[^，,。；;]{2,40}[。.]?", neutral):
            subject, answer = neutral.rstrip("。.").split("是", 1)
            # Classification queries must not carry the proposed category.
            # Numeric/dated assertions retain their metric and scope instead.
            if not re.search(r"\d|最|比|超过|低于|高于|因为|如果", answer):
                neutral = f"{subject} 分类 定义"
                changed = True
    for anchor in anchors:
        if anchor:
            updated = re.sub(re.escape(anchor), " ", neutral, flags=re.I)
            changed = changed or updated != neutral
            neutral = updated
    if value:
        updated = re.sub(re.escape(value), " ", neutral, flags=re.I)
        changed = changed or updated != neutral
        neutral = updated
    # Values with units are answer-bearing; years are retained as scope.
    neutral = re.sub(r"(?<![A-Za-z0-9])\d+(?:[.,]\d+)?\s*(?:%|％|亿元|万人|万|亿|吨|公斤|kg|米|千米|次|倍|摄氏度|平方公里)(?![A-Za-z0-9])", " ", neutral, flags=re.I)
    # Remove grammatical residue left where an embedded anchor/value was
    # removed. The complete first query retains the original syntax.
    updated = re.sub(r"不是|并非|不等于|不属于", " ", neutral)
    changed = changed or updated != neutral
    neutral = updated
    if changed:
        neutral = re.sub(r"^[\s，,。;；:：]*(?:(?:根据|关于|针对|对于|的|为|在|于|截至|下|是)\s*)+", "", neutral)
        neutral = re.sub(r"(?:(?:的|为|在|于|截至|下|是)\s*)+[\s，,。;；:：]*$", "", neutral)
    neutral = re.sub(r"\s+", " ", neutral).strip(" ，,。;；")
    return neutral


def _retrieval_queries(claim, *, max_queries: int = 3) -> list[RetrievalQuery]:
    max_queries = max(1, min(max_queries, 3))
    queries = build_queries(claim.claim_id, entities=claim.entities,
                            action=claim.normalized_claim, conditions=claim.conditions)
    if not queries:
        return []
    parts = atomic_spans(claim.normalized_claim)
    if len(parts) > 1:
        focused = []
        for part in parts:
            variants = build_queries(claim.claim_id, action=part.normalized)
            focused.append(variants[1] if len(variants) > 1 else variants[0])
        # Spend the same three-query budget on fact coverage before source discovery.
        return ([queries[0], *focused] if len(focused) == 2 else focused)[:max_queries]
    topic = queries[1].query if len(queries) > 1 else queries[0].query
    # Reserve one of the existing three calls for source discovery. Ordinary
    # searches remain available so an allowlist cannot decide the answer.
    if len(topic) >= 6 and len(query_terms(topic)) >= 3:
        science = re.search(r"声音|声速|真空|光速|太阳|地球|自转|公转|沸点|大气压|物理|化学|生物", topic)
        suffix = " (site:ac.cn OR site:cas.cn OR site:edu.cn OR site:kepu.gmw.cn)" if science else " 官方 原始资料"
        focused = _bounded_query([topic], limit=160 - len(suffix)) + suffix
        queries = [*queries[:2], RetrievalQuery(claim.claim_id, focused)]
    return queries[:max_queries]


def _topic_coverage(text: str, topic: str) -> tuple[float, int]:
    # Numbers and grammatical CJK bigrams are not topic anchors. Otherwise a
    # different measurement is lost, or the same year passes as relevance.
    terms = {term.casefold() for term in query_terms(topic)
             if not re.fullmatch(r"\d+(?:[.,]\d+)?%?", term)
             and not re.search(r"[的是在于了从向最]", term)}
    if not terms:
        terms = {term.casefold() for term in query_terms(topic)
                 if not re.fullmatch(r"\d+(?:[.,]\d+)?%?", term)}
    body = normalize_text(text)
    hits = sum(term in body for term in terms)
    return hits / len(terms) if terms else 0.0, hits


def _subject_matches(body: str, claim_text: str) -> bool:
    # Only gate explicitly recognizable subjects. Unknown syntax/aliases are
    # left for the semantic judge instead of guessing a subject from bigrams.
    aliases = (("声音", "声波", "声速"), ("太阳", "日出", "日落"), ("地球",),
               ("狗", "犬"), ("猫",), ("水", "纯水", "蒸馏水"))
    text = re.sub(r"^(?:因为|由于)", "", claim_text.strip())
    text = re.sub(r"^(?:在|截至)[^，,。；;]{1,40}[，,]\s*", "", text)
    for group in aliases:
        if any(re.match(re.escape(name) + r"(?:的|在|是|从|自|公|属|能|不|会|由)", text) for name in group):
            return any(name in body for name in group)
    return True


def _usable_excerpt(item: EvidenceItem, topic: str, claim_text: str) -> bool:
    slot = fact_slot(claim_text)
    if slot:
        # Matching the entity alone does not verify its requested property.
        # A different answer (place, date, category) must remain retrievable.
        if not slot.matches(item.excerpt):
            return False
    else:
        coverage, hits = _topic_coverage(item.excerpt, topic)
        if coverage < .3 or hits < 1:
            return False
    # A search title alone is not evidence; navigation/error pages are not
    # promoted even when their title exactly repeats the target assertion.
    body = normalize_text(item.excerpt)
    if not _subject_matches(body, claim_text):
        return False
    markers = ("edit history", "what links here", "permanent link", "access denied",
               "enable javascript", "verify you are human", "隐私政策", "用户登录", "网站导航")
    if sum(body.count(marker) for marker in markers) >= 3:
        return False
    return True


def _bounded_query(parts: Sequence[str], limit: int = 160) -> str:
    """Join complete clauses under a character budget without URL/model cuts."""
    joined = " ".join(part for part in parts if part).strip()
    if len(joined) <= limit:
        return joined
    # Prefer complete punctuation-delimited clauses. This preserves identifiers
    # such as Atlas-900 and keeps the query understandable to a provider.
    clauses = [c.strip() for c in re.split(r"(?<=[。！？!?；;，,])\s*", joined) if c.strip()]
    if len(clauses) > 1:
        selected: list[str] = []
        for clause in clauses:
            candidate = " ".join([*selected, clause])
            if len(candidate) > limit:
                break
            selected.append(clause)
        if selected:
            return " ".join(selected)
    # Whitespace boundaries are safe for Latin identifiers; for unspaced CJK
    # text cut at the final punctuation boundary available.
    cut = joined[:limit].rstrip()
    if " " in cut:
        cut = cut[:cut.rfind(" ")].rstrip()
    return cut


def search_candidates(provider: SearchProvider, queries: Sequence[RetrievalQuery], *, per_query_limit: int = 5) -> list[SearchResult]:
    """Call an injected provider only; provider failures are isolated per query."""
    results: list[SearchResult] = []
    for item in queries[:3]:
        try:
            results.extend(provider.search(item.query, limit=max(0, min(per_query_limit, 5))))
        except Exception:
            # A failed source makes evidence insufficient, never a refutation.
            continue
    return results


def fetch_result(result: SearchResult, *, client: SafeHttpClient, evidence_id: str, retrieved_at: str | None = None, anchor: str = "", anchors: Sequence[str] = ()) -> EvidenceItem:
    """Fetch a candidate with bounded, plain-text extraction.

    A provider-supplied ``content`` is accepted as controlled data (e.g. an API
    returning page text); otherwise SafeHttpClient is required.  No HTML is kept.
    """
    normalized = normalize_url(result.url, resolver=client.resolver, resolve_dns=client.allow_network)
    if result.content is None:
        response = client.fetch(normalized)
        text = extract_readable_text(response.body)
    else:
        text = extract_readable_text(result.content.encode("utf-8"))
    if not text:
        raise SecurityError("candidate has no readable text")
    timestamp = retrieved_at or datetime.now(timezone.utc).isoformat()
    return EvidenceItem(
        evidence_id=evidence_id,
        url=normalized,
        title=" ".join(result.title.split()),
        publisher=result.publisher,
        published_at=result.published_at,
        retrieved_at=timestamp,
        excerpt=_multi_target_excerpt(text, anchors, result.snippet) if len(anchors) > 1 else _relevant_excerpt(text, anchor or result.title, result.snippet),
        content_hash=content_hash(text),
        quality_reason="provider result with bounded plain-text content",
    )


def _canonical_title(value: str) -> str:
    return re.sub(r"[^\w]+", " ", normalize_text(value)).strip()


def _relevant_excerpt(text: str, title: str = "", snippet: str = "") -> str:
    if not text:
        return ""
    primary_terms = query_terms(title)[:64]
    secondary_terms = query_terms(snippet)[:64]
    terms = list(dict.fromkeys([*primary_terms, *secondary_terms]))
    # The anchor is supplied by SearchBackedRetriever through ``title`` when
    # needed; callers that pass a claim title still get topic coverage rather
    # than repeated term frequency.
    span_records = candidate_spans(text)
    spans = [(span.start, span.end) for span in span_records]
    candidates: list[tuple[int, int]] = []
    for start, end in spans:
        if end - start <= 1000:
            candidates.append((start, end))
            continue
        # Long unbroken paragraphs need windows around every topic occurrence;
        # taking only the first thousand characters loses facts near the end.
        # Fixed overlapping windows cover repeated navigation and every later
        # occurrence of a topic, while the cap keeps huge pages bounded.
        candidates.extend((pos, min(pos + 1000, end)) for pos in range(start, end, 750))
        if terms:
            folded = text.casefold()
            for term in terms[:64]:
                needle = term.casefold()
                pos = folded.find(needle, start, end)
                while pos >= 0 and len(candidates) < 8192:
                    left = max(start, pos - 350)
                    candidates.append((left, min(end, left + 1000)))
                    pos = folded.find(needle, pos + max(1, len(needle)), end)
    if not candidates:
        candidates = [(pos, min(pos + 1000, len(text))) for pos in range(0, len(text), 750)]
    template_terms = {"首页", "登录", "注册", "目录", "cookie", "导航", "菜单", "分享", "搜索", "版权"}
    slot = fact_slot(title)

    def score(window: tuple[int, int]) -> tuple[int, float, float, int, int, int]:
        start, end = window
        body = text[start:end].casefold()
        def weighted(found: set[str]) -> float:
            return sum(0.35 if re.fullmatch(r"\d+(?:[.,]\d+)?%?", term) else 1.0 for term in found)
        matched_primary = {term.casefold() for term in primary_terms if term and term.casefold() in body}
        matched_secondary = {term.casefold() for term in secondary_terms if term and term.casefold() in body}
        primary_coverage = weighted(matched_primary)
        secondary_coverage = weighted(matched_secondary)
        phrase = 1 if title.strip() and normalize_text(title) in normalize_text(body) else 0
        template_penalty = sum(body.count(term) for term in template_terms)
        # Unique topic coverage dominates repeated navigation; a complete
        # short sentence wins ties over a clipped or empty window.
        complete = 1 if end < len(text) and text[end - 1:end] in "。！？!?；;" else 0
        # The anchor is the retrieval target; a verbose provider snippet can
        # only break ties after primary topic coverage has been compared.
        return (slot.match_strength(body) if slot else 0), primary_coverage, secondary_coverage, phrase, complete, -template_penalty

    start, end = max(candidates, key=score)
    # A short sentence that begins with a pronoun benefits from one adjacent
    # sentence for context, while the returned value remains one source slice.
    for index, record in enumerate(span_records):
        if (record.start, record.end) != (start, end):
            continue
        if index and end - span_records[index - 1].start <= 1000:
            start = span_records[index - 1].start
        if index + 1 < len(span_records) and span_records[index + 1].end - start <= 1000:
            end = span_records[index + 1].end
        break
    return text[start:min(end, start + 1000)]


def _multi_target_excerpt(text: str, anchors: Sequence[str], snippet: str = "") -> str:
    """Retain separate original passages for distant facts in a compound claim.

    Each passage stays a verbatim source slice. Explicit separators prevent
    implying that the page placed distant statements next to one another.
    """
    passages: list[str] = []
    for anchor in anchors[:3]:
        passage = _relevant_excerpt(text, anchor, snippet)
        if not passage or any(passage in saved for saved in passages):
            continue
        passages = [saved for saved in passages if saved not in passage]
        passages.append(passage)
    return "\n\n[另一处原文片段]\n\n".join(passages)


def cluster_evidence(items: Iterable[EvidenceItem]) -> list[EvidenceCluster]:
    """Cluster exact-content reprints, with title fallback for sparse records."""
    groups: dict[str, EvidenceCluster] = {}
    all_items = list(items)
    parent = list(range(len(all_items)))
    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]; x = parent[x]
        return x
    def site(url):
        h = (urlsplit(url).hostname or '').casefold().split('.')
        return '.'.join(h[-3:]) if len(h) >= 3 and '.'.join(h[-2:]) in {'com.cn','gov.cn','edu.cn','org.cn','ac.cn','net.cn','co.uk','org.uk'} else '.'.join(h[-2:])
    for i, left in enumerate(all_items):
        for j in range(i):
            right = all_items[j]
            if site(left.url) == site(right.url) or (left.content_hash and left.content_hash == right.content_hash):
                a, b = find(i), find(j)
                if a != b: parent[b] = a
    for i, item in enumerate(all_items):
        host = (urlsplit(item.url).hostname or "").casefold()
        # Pages from one publisher are correlated even when their text differs;
        # they must not count as independent confirmation.
        hash_key = f"hash:{item.content_hash}" if item.content_hash else ""
        key = f"uf:{find(i)}"
        cluster = groups.get(key)
        if cluster is None:
            cluster = EvidenceCluster(cluster_id=f"ec_{len(groups)+1:04d}", independence_reason="same site or identical content hash")
            groups[key] = cluster
        elif item.content_hash == cluster.items[0].content_hash:
            item.is_reprint = True
            item.original_url = cluster.items[0].url
        cluster.items.append(item)
    return list(groups.values())


def candidate_evidence_text(items: Iterable[EvidenceItem]) -> dict[str, str]:
    """Build the allowlist map consumed by model-output validation."""
    return {item.evidence_id: item.excerpt for item in items}


class SearchBackedRetriever:
    """Bridge a constrained search provider into API evidence clusters.

    It only accepts provider-returned text in this first integration stage.  It
    never turns arbitrary result URLs into server-side requests, so the stricter
    DNS-pinned page fetcher can be added independently later.
    """

    def __init__(self, search_provider: SearchProvider, *, max_evidence: int = 5, max_queries: int = 3) -> None:
        self.search_provider = search_provider
        self.provider = search_provider.name
        self.max_evidence = max(1, min(max_evidence, 5))
        self.max_queries = max(1, min(max_queries, 3))
        self._safe_client = SafeHttpClient(allow_network=False)

    def retrieve(self, claim, *, deadline: float | None = None) -> list[ApiEvidenceCluster]:
        queries = _retrieval_queries(claim, max_queries=self.max_queries)
        claim.retrieval_warnings = []
        if not queries:
            return []
        claim.queries = []
        topic = queries[1].query if len(queries) > 1 and "site:" not in queries[1].query and not queries[1].query.endswith("官方 原始资料") else queries[0].query
        parts = atomic_spans(claim.normalized_claim)
        targets = [(part.normalized, _neutral_action(part.normalized, ())) for part in parts] if len(parts) > 1 else [(claim.normalized_claim, topic)]
        candidates: list[SearchResult] = []
        provider_errors = 0
        last_error: ProviderError | None = None
        for query_index, query in enumerate(queries, 1):
            claim.queries.append(query.query)
            try:
                if deadline is not None and time.monotonic() >= deadline:
                    raise SearchProviderError("SEARCH_TIMEOUT")
                search = self.search_provider.search
                parameters = inspect.signature(search).parameters.values()
                accepts_deadline = any(
                    parameter.name == "deadline" or parameter.kind is inspect.Parameter.VAR_KEYWORD
                    for parameter in parameters
                )
                if accepts_deadline:
                    found = search(query.query, limit=5, deadline=deadline)
                else:
                    found = search(query.query, limit=5)
                candidates.extend(found)
            except ProviderError as exc:
                provider_errors += 1
                last_error = exc
                message = exc.public_message if isinstance(exc, SearchProviderError) else "搜索服务暂时不可用（SEARCH_UNAVAILABLE）。"
                claim.retrieval_warnings.append(f"第 {query_index} 组网页检索未完成：{message} 已保留取得的证据，未将技术失败当作反证。")
                if isinstance(exc, SearchProviderError) and exc.code in {
                    "SEARCH_AUTH", "SEARCH_FORBIDDEN", "SEARCH_QUOTA", "SEARCH_NETWORK_PERMISSION",
                }:
                    if not candidates:
                        raise
                    break
        if provider_errors == len(queries):
            assert last_error is not None
            raise last_error

        evidence: list[EvidenceItem] = []
        seen_urls: set[str] = set()
        # Providers may return a short search snippet before the full body for
        # the same URL. Process complete text first, then use URL de-duplication
        # so the richer representation survives.
        candidates = sorted(
            candidates,
            key=lambda candidate: (
                0 if candidate.metadata.get("content_kind") == "search_snippet" else 1,
                len(candidate.content or ""),
            ),
            reverse=True,
        )
        for candidate in candidates:
            try:
                anchor = max((t for _, t in targets), key=lambda t: _topic_coverage(candidate.content or candidate.snippet, t))
                item = fetch_result(candidate, client=self._safe_client, evidence_id=f"e_{len(evidence)+1}", anchor=anchor,
                                    anchors=[target for _, target in targets])
            except SecurityError:
                continue
            if not source_allowed(item.url, claim.normalized_claim, item.title):
                continue
            if item.url in seen_urls:
                continue
            if not any(_usable_excerpt(item, target, part) for part, target in targets):
                continue
            seen_urls.add(item.url)
            if candidate.metadata.get("content_kind") == "search_snippet":
                item.quality_reason = "搜索结果摘要；未取得完整正文，需逐项核对或独立来源印证"
            else:
                item.quality_reason = "搜索服务返回的正文；仍需核对实体、时间与语境"
            item.quality_reason += "；" + source_profile(item.url).reason
            evidence.append(item)

        from .judge import source_kind
        def rank(item):
            # Relevance is based on distinct topic/identifier coverage. A
            # repeated navigation label cannot outweigh a body containing the
            # claim's subject, metric, and date. Numeric disagreement remains
            # a possible refutation and is never filtered here.
            relevance = max(_topic_coverage(item.excerpt, target)[0] for _, target in targets)
            quality = 1 if source_kind(item) == "body" else 0
            property_match = max((slot.match_strength(item.excerpt) if (slot := fact_slot(part)) else 0)
                                 for part, _ in targets)
            # Only relevant pages reach this stage. Full bodies first, then
            # balance coverage with source priors instead of keyword stuffing.
            return (property_match, .5 * relevance + .35 * _source_authority(item.url) + .15 * quality, relevance)
        clusters = cluster_evidence(evidence)
        selected = [max(cluster.items, key=rank) for cluster in clusters]
        selected_ids = {id(item) for item in selected}
        remainder = [item for item in evidence if id(item) not in selected_ids]
        ranked = sorted(selected, key=rank, reverse=True)
        evidence = []
        if len(targets) > 1:
            all_ranked = sorted([*ranked, *remainder], key=rank, reverse=True)
            for part, target in targets:
                best = next((item for item in all_ranked if _usable_excerpt(item, target, part)), None)
                if best is not None and best not in evidence:
                    evidence.append(best)
        evidence = (evidence + [item for item in ranked if item not in evidence])[:self.max_evidence]
        if len(evidence) < self.max_evidence:
            evidence.extend([item for item in sorted(remainder, key=rank, reverse=True) if item not in evidence][:self.max_evidence-len(evidence)])

        kept = {id(item) for item in evidence}
        clusters = [EvidenceCluster(c.cluster_id, sorted([i for i in c.items if id(i) in kept], key=rank, reverse=True), c.independence_reason)
                    for c in clusters if any(id(i) in kept for i in c.items)]
        clusters.sort(key=lambda c: rank(c.items[0]), reverse=True)
        return [_to_api_cluster(c) for c in clusters]


def _to_api_cluster(cluster: EvidenceCluster) -> ApiEvidenceCluster:
    return ApiEvidenceCluster(
        cluster_id=cluster.cluster_id,
        independence_reason=cluster.independence_reason,
        items=[
            ApiEvidenceItem(
                evidence_id=item.evidence_id,
                url=item.url,
                title=item.title or None,
                publisher=item.publisher,
                published_at=_parse_datetime(item.published_at),
                retrieved_at=_parse_datetime(item.retrieved_at),
                excerpt=item.excerpt,
                relation=EvidenceRelation.UNKNOWN,
                quality_reason=item.quality_reason,
                is_reprint=item.is_reprint,
                content_hash=item.content_hash,
                authority=_source_authority(item.url),
            )
            for item in cluster.items
        ],
    )


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _source_authority(url: str) -> float:
    """Conservative, explainable baseline; relation and context still gate scoring."""
    return source_profile(url).authority
