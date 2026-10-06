"""Stable HTTP error envelopes without raw input or provider responses."""

import logging
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

logger = logging.getLogger("verifier")


def safe_error(code: str, message: str, status: int = 400) -> JSONResponse:
    request_id = f"r_{uuid4()}"
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message, "request_id": request_id}},
                        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


async def http_error(_: Request, exc: HTTPException):
    detail = exc.detail if isinstance(exc.detail, str) else "请求无效"
    return safe_error("REQUEST_INVALID", detail, exc.status_code)


async def validation_error(request: Request, exc: RequestValidationError):
    # Keep validation details generic: no internal model paths or raw input echoes.
    too_large = any(error.get("type") in {"string_too_long", "too_long"} for error in exc.errors())
    if request.url.path.startswith("/api/knowledge/"):
        return safe_error("INPUT_TOO_LARGE" if too_large else "REQUEST_INVALID",
                          "知识库输入超过允许长度" if too_large else "知识库参数无效，请检查正文、日期和 HTTP(S) 出处地址。", 422)
    return safe_error("INPUT_TOO_LARGE" if too_large else "REQUEST_INVALID",
                      "输入文本超过 20000 字符" if too_large else "请求参数无效", 422)


async def unexpected_error(_: Request, exc: Exception):
    # Deliberately do not expose stack traces, paths, SQL or provider responses.
    # Exception messages/tracebacks can contain user input or provider credentials.
    logger.error("request failed: exception_type=%s", type(exc).__name__)
    return safe_error("INTERNAL_ERROR", "服务暂时不可用，请稍后重试。", 500)


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(HTTPException, http_error)
    app.add_exception_handler(RequestValidationError, validation_error)
    app.add_exception_handler(Exception, unexpected_error)
