"""Small, deterministic text helpers shared by extraction and retrieval.

The verifier deliberately does not depend on a linguistic model.  These rules
are conservative: keeping two neighbouring clauses together is preferable to
silently dropping a condition or a negation from a claim.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


_CLOSERS = "\"'”’」』）)]}》〉〕】〗〙〛»"
_OPENERS = "\"'“‘「『（([{《〈〔【〖〘〚«"
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}(?:\s|$)")
_LIST_RE = re.compile(r"^\s*(?:[-*+•]|\d+[.)])\s+")
_LINK_RE = re.compile(r"!?(\[[^\]]*\])\([^)]*\)")
_REFERENCE_LINK_RE = re.compile(r"!?(\[[^\]]*\])\s*\[[^\]]*\]")
_URL_RE = re.compile(r"https?://\S+|\b(?:doi:\s*)?10\.\d{4,9}/\S+", re.I)
_ABBREVIATIONS = {
    "mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "vs", "etc",
    "e.g", "i.e", "fig", "no", "approx", "inc", "ltd", "co",
}
_QUERY_STOPWORDS = {"the", "is", "of", "in", "on", "a", "an", "and", "to", "for", "with", "by", "from", "are", "be"}
_CJK_STOPWORDS = {"的", "是", "在", "于", "为", "和", "与", "及", "了"}


@dataclass(frozen=True)
class TextSpan:
    start: int
    end: int
    source: str
    normalized: str


def normalize_claim(value: str) -> str:
    """Normalize Markdown presentation while preserving factual wording."""
    value = unicodedata.normalize("NFKC", value or "")
    value = _LINK_RE.sub(lambda m: m.group(1)[1:-1], value)
    value = _REFERENCE_LINK_RE.sub(lambda m: m.group(1)[1:-1], value)
    value = re.sub(r"^\s{0,3}#{1,6}\s+", "", value)
    value = _LIST_RE.sub("", value)
    # Remove paired emphasis/backtick markers only. A lone ``~`` in ``~5%``
    # and underscores inside identifiers such as Atlas_900 are content.
    value = re.sub(r"\*\*(.+?)\*\*", r"\1", value)
    value = re.sub(r"(?<!\w)\*(?!\*)(.+?)(?<!\*)\*(?!\w)", r"\1", value)
    value = re.sub(r"~~(.+?)~~", r"\1", value)
    value = re.sub(r"`+(.+?)`+", r"\1", value)
    value = re.sub(r"__(.+?)__", r"\1", value)
    value = re.sub(r"(?<!\w)_(?!_)(.+?)(?<!\w)_(?!\w)", r"\1", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def is_claim_candidate(source: str, normalized: str | None = None) -> bool:
    normalized = normalize_claim(source) if normalized is None else normalized
    if not normalized or _HEADING_RE.match(source):
        return False
    if not re.search(r"[\w\u3400-\u9fff]", normalized, re.UNICODE):
        return False
    # A line made solely of Markdown/list punctuation is presentation noise.
    if not re.search(r"[A-Za-z0-9\u3400-\u9fff]", normalized):
        return False
    return True


def _is_protected_period(text: str, index: int, token_start: int) -> bool:
    """Whether a dot is part of a URL, decimal/version, or abbreviation."""
    if text[index] != ".":
        return False
    before = text[max(token_start, index - 30):index + 1]
    after_match = re.match(r"\s*([A-Za-z0-9])", text[index + 1:index + 34])
    after = after_match.group(1) if after_match else ""
    if re.match(r"\s*\d+[.)]$", text[token_start:index + 1].lstrip()):
        return True
    if index > 0 and index + 1 < len(text) and text[index - 1].isdigit() and text[index + 1].isalnum():
        return True
    # Multi-part abbreviations (e.g., i.e., U.S.) protect every internal dot.
    abbreviation_window = text[max(token_start, index - 5):index + 10]
    abbreviation = re.search(r"([A-Za-z](?:\.[A-Za-z])+\.)", abbreviation_window)
    if abbreviation:
        compact = abbreviation.group(1).casefold().rstrip(".")
        window_start = max(token_start, index - 5)
        match_start = window_start + abbreviation.start(1)
        match_end = window_start + abbreviation.end(1)
        if match_start <= index < match_end and (compact in _ABBREVIATIONS or all(len(part) == 1 for part in compact.split("."))):
            return True
    if after and (after.isalpha() or after.isdigit()):
        token = re.search(r"([A-Za-z](?:[A-Za-z.]*)?)$", before)
        if token and token.group(1).casefold().rstrip(".") in _ABBREVIATIONS:
            return True
        # Initials such as ``A. Smith`` are not sentence boundaries.
        token_text = token.group(1).rstrip(".") if token else ""
        if token_text and len(token_text) <= 2 and token_text[0].isupper():
            return True
    return False


def _protected_url_ranges(text: str) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    for match in re.finditer(r"(?:https?://|10\.\d{4,9}/)[^\s。！？；;，,]+", text, re.I):
        end = match.end()
        while end > match.start() and text[end - 1] in "。！？!?；;.，,：:）)]}》〉】\"'”’":
            end -= 1
        ranges.append((match.start(), end))
    return ranges


def candidate_spans(text: str) -> list[TextSpan]:
    """Return valid source spans in original code-point coordinates.

    The caller converts these coordinates to UTF-16.  Punctuation and closing
    quote/bracket characters stay in ``source``; surrounding whitespace does
    not, so the returned text is always an exact slice of ``text``.
    """
    if not text:
        return []
    spans: list[TextSpan] = []
    protected_urls = _protected_url_ranges(text)
    url_cursor = 0
    start = 0
    stack: list[str] = []
    quote_open: str | None = None
    i = 0
    while i < len(text):
        ch = text[i]
        while url_cursor < len(protected_urls) and i >= protected_urls[url_cursor][1]:
            url_cursor += 1
        in_url = bool(url_cursor < len(protected_urls) and protected_urls[url_cursor][0] <= i < protected_urls[url_cursor][1])
        if ch in _OPENERS:
            if ch in "\"'“‘「『«":
                # An apostrophe inside a word is not a quote boundary.
                if ch == "'" and i > 0 and i + 1 < len(text) and text[i - 1].isalnum() and text[i + 1].isalnum():
                    i += 1
                    continue
                if quote_open is None:
                    quote_open = ch
                elif ch in "\"'" and quote_open == ch:
                    quote_open = None
            elif ch in "（([{《〈〔【〖〘〚":
                stack.append(ch)
        elif ch in _CLOSERS:
            if quote_open is not None and ch in "\"'”’」』»":
                quote_open = None
            elif stack:
                stack.pop()

        boundary = False
        if not stack and quote_open is None:
            if ch in "。！？!?；;" and not in_url:
                boundary = True
            elif ch == "." and not in_url:
                boundary = not _is_protected_period(text, i, start)
            elif ch in "\r\n":
                boundary = True
        # Punctuation inside a closing quote is held until the quote closes.
        if not stack and ch in _CLOSERS and i > 0 and text[i - 1] in "。！？!?；;.":
            boundary = True
        if boundary:
            end = i + 1
            while end < len(text) and text[end] in "。！？!?；;." + _CLOSERS:
                end += 1
            if text[i] in "\r\n":
                while end < len(text) and text[end] in "\r\n":
                    end += 1
            left = start
            right = end
            while left < right and text[left].isspace():
                left += 1
            while right > left and text[right - 1].isspace():
                right -= 1
            source = text[left:right]
            normalized = normalize_claim(source)
            if is_claim_candidate(source, normalized):
                spans.append(TextSpan(left, right, source, normalized))
            start = end
            i = end
            continue
        i += 1

    left, right = start, len(text)
    while left < right and text[left].isspace():
        left += 1
    while right > left and text[right - 1].isspace():
        right -= 1
    source = text[left:right]
    normalized = normalize_claim(source)
    if is_claim_candidate(source, normalized):
        spans.append(TextSpan(left, right, source, normalized))
    return spans


def query_terms(value: str) -> list[str]:
    """Extract stable topic/identifier terms for deterministic ranking."""
    value = unicodedata.normalize("NFKC", value or "")
    terms: list[str] = []
    for token in re.findall(r"[A-Za-z][A-Za-z0-9]*(?:[-_/][A-Za-z0-9]+)*|\d+(?:[.,]\d+)?%?|[\u3400-\u9fff]+", value):
        if re.fullmatch(r"[\u3400-\u9fff]+", token):
            if len(token) <= 2:
                if token not in _CJK_STOPWORDS:
                    terms.append(token)
            else:
                terms.extend(token[i:i + 2] for i in range(len(token) - 1) if token[i:i + 2] not in _CJK_STOPWORDS)
        elif len(token) >= 2:
            if token.casefold() not in _QUERY_STOPWORDS:
                terms.append(token)
    return list(dict.fromkeys(terms))
