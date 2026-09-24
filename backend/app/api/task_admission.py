"""Cap paid task admission per process, including background work."""

from __future__ import annotations

import json
import threading
import time
from collections import deque
from typing import Any
from uuid import uuid4


class TaskAdmissionLimit:
    def __init__(self, app: Any, *, max_active: int = 3, max_per_minute: int = 10) -> None:
        self.app = app
        self.max_active = max_active
        self.max_per_minute = max_per_minute
        self.active = 0
        self.recent: deque[float] = deque()
        self.lock = threading.Lock()

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        path = scope.get("path", "")
        costly = (scope["type"] == "http" and scope["method"] == "POST" and
                  (path == "/api/tasks" or (path.startswith("/api/claims/") and path.endswith("/retry"))))
        if not costly:
            await self.app(scope, receive, send)
            return

        now = time.monotonic()
        with self.lock:
            while self.recent and self.recent[0] <= now - 60:
                self.recent.popleft()
            admitted = self.active < self.max_active and len(self.recent) < self.max_per_minute
            if admitted:
                self.active += 1
                self.recent.append(now)
        if not admitted:
            body = json.dumps({"error": {"code": "TASK_RATE_LIMIT", "message": "任务提交过于频繁，请稍后重试", "request_id": f"r_{uuid4()}"}}).encode()
            await send({"type": "http.response.start", "status": 429,
                        "headers": [(b"content-type", b"application/json; charset=utf-8"),
                                    (b"content-length", str(len(body)).encode()),
                                    (b"retry-after", b"60")]})
            await send({"type": "http.response.body", "body": body})
            return
        try:
            await self.app(scope, receive, send)
        finally:
            with self.lock:
                self.active -= 1
