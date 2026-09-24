"""Provider interfaces.  Implementations must return validated, untrusted data."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, Sequence


class ProviderError(RuntimeError):
    """Stable internal provider failure; never expose raw responses to clients."""


class LiveProviderNotConfigured(ProviderError):
    pass


class SearchProviderError(ProviderError):
    """Allowlisted diagnostics safe to persist and display without raw errors."""

    MESSAGES = {
        "SEARCH_AUTH": "搜索 API 密钥无效或已失效，请检查后端 SEARCH_API_KEY。",
        "SEARCH_FORBIDDEN": "搜索 API 拒绝访问，请检查服务账户权限。",
        "SEARCH_QUOTA": "搜索 API 额度或付费使用上限已耗尽，请检查 Tavily 控制台。",
        "SEARCH_RATE_LIMIT": "搜索 API 请求过于频繁，请稍后重试。",
        "SEARCH_TIMEOUT": "搜索 API 请求超时，请检查后端网络后重试。",
        "SEARCH_NETWORK_PERMISSION": "后端联网被系统或沙箱权限阻止，请在允许联网的终端启动后端（WinError 10013 / EACCES）。",
        "SEARCH_CONNECTION": "后端无法连接搜索 API，请检查网络、代理和证书设置。",
        "SEARCH_UNAVAILABLE": "搜索 API 服务暂时不可用，请稍后重试。",
        "SEARCH_REJECTED": "搜索 API 拒绝了请求，请检查接口参数。",
    }

    def __init__(self, code: str) -> None:
        self.code = code if code in self.MESSAGES else "SEARCH_UNAVAILABLE"
        super().__init__(self.MESSAGES[self.code])

    @property
    def public_message(self) -> str:
        return f"{self.MESSAGES[self.code]}（{self.code}）"


class JudgmentProviderError(ProviderError):
    """Model failures with allowlisted, client-safe diagnostics."""

    MESSAGES = {
        "LLM_AUTH": "模型 API 密钥无效或已失效，请检查后端 MIMO_API_KEY。",
        "LLM_FORBIDDEN": "模型 API 拒绝访问，请检查账户或模型权限。",
        "LLM_QUOTA": "模型 API 余额或额度不足，请检查服务控制台。",
        "LLM_MODEL": "模型或接口不存在，请检查模型名称和接口地址。",
        "LLM_RATE_LIMIT": "模型 API 请求过于频繁，请稍后重试。",
        "LLM_TIMEOUT": "模型判断超过请求或总等待时间上限，本次未完成。",
        "LLM_NETWORK_PERMISSION": "后端连接模型 API 被系统或沙箱权限阻止。",
        "LLM_CONNECTION": "后端无法连接模型 API，请检查网络、代理和证书。",
        "LLM_UNAVAILABLE": "模型 API 服务暂时不可用，请稍后重试。",
        "LLM_REJECTED": "模型 API 拒绝请求，请根据后端日志中的 HTTP 状态码检查配置。",
        "LLM_CONTENT_FILTER": "MiMo 对本次输入或输出进行了内容拦截，未完成判断；已保留检索证据供人工核对。",
        "LLM_TRUNCATED": "模型输出达到长度上限，判断结果不完整。",
        "LLM_REPETITION": "模型因重复生成而停止，未返回完整判断。",
        "LLM_EMPTY": "模型未返回可用的判断内容。",
        "LLM_RESPONSE": "模型 API 返回的数据结构异常。",
        "LLM_JSON": "模型返回的判断内容不是有效 JSON。",
        "LLM_SCHEMA": "模型返回的 JSON 不符合证据判断格式。",
        "LLM_RESPONSE_SIZE": "模型 API 响应超过大小限制。",
    }

    def __init__(self, code: str, *, http_status: int | None = None) -> None:
        self.code = code if code in self.MESSAGES else "LLM_UNAVAILABLE"
        self.http_status = http_status
        self.diagnostic_id: str | None = None
        super().__init__(self.MESSAGES[self.code])

    @property
    def public_message(self) -> str:
        status = f"；HTTP {self.http_status}" if self.http_status else ""
        reference = f"；诊断编号 {self.diagnostic_id}" if self.diagnostic_id else ""
        return f"{self.MESSAGES[self.code]}（{self.code}{status}{reference}）"


@dataclass(frozen=True)
class SearchResult:
    url: str
    title: str = ""
    snippet: str = ""
    content: str | None = None
    publisher: str | None = None
    published_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class SearchProvider(Protocol):
    """Search-only contract; fetching remains behind SafeHttpClient."""

    name: str

    def search(self, query: str, *, limit: int = 5) -> Sequence[SearchResult]: ...


class PaperProvider(Protocol):
    name: str

    def lookup(self, *, doi: str | None = None, title: str | None = None) -> Sequence[dict[str, Any]]: ...


class EvidenceJudge(Protocol):
    """A judge may classify supplied evidence, but has no retrieval capability."""

    provider: str

    def judge(self, claim: Any, clusters: Sequence[Any]) -> None: ...
