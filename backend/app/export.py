"""Safe, deterministic report export (plain text Markdown; no HTML passthrough)."""

from __future__ import annotations

import json
import re
from html import escape
from urllib.parse import quote

from .schemas import Claim, TaskSummary
from .security import SecurityError, normalize_url


def _text(value: str | None) -> str:
    escaped = escape(" ".join((value or "").split()), quote=False).replace("`", "\\`")
    # Keep user content readable while preventing it from becoming Markdown syntax
    # or a dangerous autolink when rendered by a client.
    escaped = re.sub(r"(?i)(?:javascript|data|vbscript):", "[blocked-url]:", escaped)
    return re.sub(r"([\\[\\]\\(\\)])", r"\\\\\1", escaped)


def _url(value: str | None) -> str:
    if not value:
        return ""
    try:
        return normalize_url(value, resolve_dns=False)
    except SecurityError:
        return ""


def _markdown_url(value: str) -> str:
    """Encode a validated URL as a Markdown link destination."""
    # Encode every character that can affect Markdown structure, including
    # whitespace, angle brackets, brackets, parentheses, and Unicode separators.
    return quote(value, safe=":/?#@!$&'*+,;=%")


def to_json(summary: TaskSummary, claims: list[Claim]) -> str:
    safe_claims = []
    for claim in claims:
        data = claim.model_dump(mode="json")
        for cluster in data.get("evidence_clusters", []):
            for item in cluster.get("items", []):
                item["url"] = _url(item.get("url")) or None
        safe_claims.append(data)
    payload = {"task": summary.model_dump(mode="json"), "claims": safe_claims,
               "disclaimer": "该报告表示当前证据支持程度，不是事实为真的概率或绝对裁决。"}
    return json.dumps(payload, ensure_ascii=False, indent=2)


def to_markdown(summary: TaskSummary, claims: list[Claim]) -> str:
    lines = ["# AI 答案可信度验证报告", "", f"- 任务状态：`{summary.status.value}`", f"- 输入字符数：{summary.input_char_count}",
             f"- 抽取声明：{summary.claims_extracted}，已处理：{summary.claims_processed}，未核验：{summary.claims_unchecked}",
             f"- 是否因上限截断：{'是' if summary.truncated else '否'}",
             f"- 处理覆盖率：{summary.coverage.processing_coverage if summary.coverage.processing_coverage is not None else 'null'}",
             f"- 核验覆盖率：{summary.coverage.verification_coverage if summary.coverage.verification_coverage is not None else 'null'}",
             f"- 失败来源：{_text('、'.join(summary.failed_providers)) if summary.failed_providers else '无'}",
             f"- 运行规则版本：`heuristic-v1`", "", "## 声明"]
    for claim in claims:
        lines += ["", f"### {_text(claim.normalized_claim)}", f"- 标签：`{claim.label.value if claim.label else 'unknown'}`",
                  f"- 原文：{_text(claim.source_text)}", f"- 支持指数：{claim.support_score if claim.support_score is not None else 'null'}",
                  f"- 原因：{_text(claim.reason)}"]
        for cluster in claim.evidence_clusters:
            for item in cluster.items:
                url = _url(item.url)
                title = _text(item.title) or "证据"
                lines.append(f"- 证据：[{title}]({_markdown_url(url)})" if url else f"- 证据：{title}")
                lines.append(f"  - 片段：{_text(item.excerpt)}")
    lines += ["", "---", "免责声明：该报告仅供复核和排序，不证明全文正确，也不替代专业判断。", ""]
    return "\n".join(lines)
