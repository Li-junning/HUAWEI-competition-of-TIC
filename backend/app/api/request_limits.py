"""Bound request bytes before JSON parsing allocates an oversized body."""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4


MAX_TASK_REQUEST_BYTES = 256 * 1024


class TaskRequestBodyLimit:
    def __init__(self, app: Any, max_bytes: int = MAX_TASK_REQUEST_BYTES) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http" or scope["method"] != "POST" or scope["path"] != "/api/tasks":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        content_length = headers.get(b"content-length")
        if content_length is not None:
            try:
                if int(content_length) > self.max_bytes:
                    await self._reject(send)
                    return
            except ValueError:
                await self._reject(send)
                return

        chunks = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunks.extend(message.get("body", b""))
            if len(chunks) > self.max_bytes:
                await self._reject(send)
                return
            if not message.get("more_body", False):
                break

        delivered = False

        async def replay() -> dict:
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(chunks), "more_body": False}
            return await receive()

        await self.app(scope, replay, send)

    @staticmethod
    async def _reject(send: Any) -> None:
        body = json.dumps({"error": {"code": "REQUEST_TOO_LARGE", "message": "请求体过大", "request_id": f"r_{uuid4()}"}}).encode()
        await send({"type": "http.response.start", "status": 413,
                    "headers": [(b"content-type", b"application/json; charset=utf-8"),
                                (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})
