"""Combine local and web evidence without granting uploaded sources extra trust."""

import hashlib
from datetime import datetime, time, timezone
from urllib.parse import urlsplit

from .providers.base import ProviderError
from .retrieve import _neutral_action, _retrieval_queries, _usable_excerpt
from .claim_segmentation import judgment_parts
from .schemas import EvidenceCluster, EvidenceItem, now_utc


def _site(url):
    parts = (urlsplit(url or "").hostname or "").casefold().split(".")
    suffix = ".".join(parts[-2:])
    return ".".join(parts[-3:]) if suffix in {"com.cn", "gov.cn", "edu.cn", "org.cn", "ac.cn", "net.cn", "co.uk", "org.uk"} else suffix


def merge_evidence(clusters, *, limit=5, claim_text=""):
    """Keep existing clusters intact; also merge shared sites and identical slices."""
    groups = []
    uploads = []
    for cluster in clusters:
        uploads.extend(item for item in cluster.items if item.source_type == "knowledge")
        items = [item for item in cluster.items if item.source_type != "knowledge"]
        if not items:
            continue
        sites = {_site(item.url) or "local-without-provenance" for item in items}
        hashes = {item.content_hash for item in items if item.content_hash}
        slices = {hashlib.sha256((item.excerpt or "").encode()).hexdigest() for item in items if item.excerpt}
        matching = [g for g in groups if sites & g[1] or hashes & g[2] or slices & g[3]]
        for group in matching:
            items.extend(group[0]); sites |= group[1]; hashes |= group[2]; slices |= group[3]
            groups.remove(group)
        groups.append([items, sites, hashes, slices])
    unverified = []
    for item in uploads:
        digest = hashlib.sha256((item.excerpt or "").encode()).hexdigest() if item.excerpt else None
        matching = next((group for group in groups if (item.content_hash and item.content_hash in group[2])
                         or (digest and digest in group[3])), None)
        if matching is not None:
            # Attach duplicate references without adding self-declared sites
            # or linking independent web sources through the uploads pool.
            matching[0].append(item)
        else:
            unverified.append(item)
    if unverified:
        groups.insert(0, [unverified, set(), set(), set()])
    for group in groups:
        # If an upload is an exact duplicate, keep the provider-returned copy
        # first so de-duplication cannot replace verified retrieval provenance.
        group[0].sort(key=lambda item: item.source_type == "knowledge")
    selected = [[] for _ in groups]
    used_slices = set()
    count = 0

    def select(index, item):
        nonlocal count
        key = (item.url, item.excerpt)
        if count >= limit or key in used_slices:
            return False
        used_slices.add(key)
        selected[index].append(item)
        count += 1
        return True

    # Cover each fact before spending slots on sources repeating another fact.
    # This selects relevant candidates; it never decides truth or relation.
    parts = judgment_parts(claim_text) if claim_text else []
    if len(parts) > 1:
        candidates = [(index, item, {part_id for part_id, part in enumerate(parts)
                      if _usable_excerpt(item, _neutral_action(part, ()), part)})
                      for index, group in enumerate(groups) for item in group[0]]
        uncovered = set(range(len(parts)))
        while uncovered and count < limit:
            available = [candidate for candidate in candidates
                         if candidate[2] & uncovered and (candidate[1].url, candidate[1].excerpt) not in used_slices]
            if not available:
                break
            index, item, covered = max(available, key=lambda candidate: (
                len(candidate[2] & uncovered), candidate[1].source_type != "knowledge", candidate[1].authority))
            select(index, item)
            uncovered -= covered

    # Fill remaining slots round-robin, preserving source independence.
    for offset in range(max((len(g[0]) for g in groups), default=0)):
        for index, group in enumerate(groups):
            if offset >= len(group[0]) or count >= limit:
                continue
            item = group[0][offset]
            select(index, item)
    return [EvidenceCluster(cluster_id=f"ec_{index + 1:04d}", items=items,
                independence_reason="同一站点、相同正文或已有证据簇合并；未核实出处的上传资料不计独立来源")
            for index, items in enumerate(selected) if items]


class KnowledgeBackedRetriever:
    def __init__(self, knowledge, web_retriever, *, max_evidence=5, max_queries=None):
        self.knowledge = knowledge
        self.web_retriever = web_retriever
        self.provider = web_retriever.provider
        self.max_evidence = max_evidence
        self.max_queries = max(1, min(max_queries if max_queries is not None else getattr(web_retriever, "max_queries", 3), 3))
        if hasattr(web_retriever, "max_queries"):
            web_retriever.max_queries = self.max_queries

    def retrieve(self, claim, *, deadline=None):
        queries = _retrieval_queries(claim, max_queries=self.max_queries)
        hits = {}
        for query in queries:
            text = query.query.split(" (site:", 1)[0].removesuffix(" 官方 原始资料")
            for hit in self.knowledge.search(text, limit=5, deadline=deadline):
                hits.setdefault(hit.chunk_id, hit)
        local = []
        for hit in list(hits.values())[:self.max_evidence]:
            locator = f"第 {hit.page} 页，" if hit.page else ""
            locator += f"正文字符 {hit.char_start}–{hit.char_end}"
            item = EvidenceItem(evidence_id=hit.chunk_id, url=hit.source_url, title=hit.title,
                publisher=hit.publisher,
                published_at=datetime.combine(hit.published_at, time.min, timezone.utc) if hit.published_at else None,
                retrieved_at=now_utc(),
                excerpt=hit.excerpt, content_hash=hit.content_hash, authority=.55,
                source_type="knowledge", knowledge_document_id=hit.document_id,
                knowledge_chunk_id=hit.chunk_id, source_page=hit.page,
                source_char_start=hit.char_start, source_char_end=hit.char_end,
                quality_reason=f"用户导入资料；{locator}；出处由用户填写，尚未核实，仅供参考，不计独立来源或裁决分数")
            local.append(EvidenceCluster(cluster_id=hit.chunk_id, items=[item]))
        claim.retrieval_warnings = []
        try:
            web = self.web_retriever.retrieve(claim, deadline=deadline)
        except ProviderError as exc:
            if not local:
                raise
            web = []
            claim.retrieval_warnings.append(getattr(exc, "public_message", "网页检索不可用") + " 本次仅使用知识库证据，网页来源未完成。")
        claim.queries = list(dict.fromkeys([*claim.queries, *(f"知识库：{q.query}" for q in queries)]))
        # Give both channels a place in the initial candidate list. Source and
        # duplicate clustering prevent a saved webpage from becoming two votes.
        return merge_evidence([*local[:1], *web, *local[1:]], limit=self.max_evidence, claim_text=claim.normalized_claim)
