"""Bounded local provider audit log; callers supply metadata, never raw data."""

import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


logger = logging.getLogger("verifier.providers")
logger.setLevel(logging.INFO)
_FIELDS = {
    "diagnostic_id", "claim_id", "task_id", "attempt", "elapsed_ms", "http_status",
    "code", "finish_reason", "response_bytes", "content_chars", "decision_count",
    "exception_type", "location", "validation_types", "stage",
    "timeout_seconds", "remaining_ms", "completion_token_limit", "delay_seconds",
}


def configure_provider_logging(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    path = str((directory / "providers.log").resolve())
    if any(isinstance(handler, RotatingFileHandler) and handler.baseFilename == path for handler in logger.handlers):
        return
    handler = RotatingFileHandler(path, maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    logger.addHandler(handler)


def audit(event: str, **metadata) -> None:
    logger.info(json.dumps({"event": event, **{key: value for key, value in metadata.items() if key in _FIELDS}}, ensure_ascii=True))
