"""Configuration with conservative, environment-driven defaults."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _load_project_env() -> None:
    """Load simple KEY=VALUE lines without executing or overriding env vars."""
    path = Path(__file__).resolve().parents[2] / ".env"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip("\"'")
        if key and key.replace("_", "").isalnum():
            os.environ.setdefault(key, value)


def _int_env(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, value))


@dataclass(frozen=True)
class Settings:
    database_path: Path
    max_input_chars: int = 20_000
    max_claims: int = 15
    max_queries_per_claim: int = 3
    max_evidence_per_claim: int = 5
    max_retries: int = 2
    task_timeout_seconds: int = 180
    request_timeout_seconds: int = 20
    max_concurrency: int = 3
    live_providers: bool = False
    scoring_rules_version: str = "heuristic-v1"
    allowed_origins: tuple[str, ...] = ("http://localhost:5173", "http://127.0.0.1:5173")


def get_settings() -> Settings:
    _load_project_env()
    default_db = Path(__file__).resolve().parents[1] / "data" / "app.db"
    raw_db = os.getenv("VERIFIER_DATABASE_PATH", str(default_db))
    raw_origins = os.getenv("VERIFIER_ALLOWED_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")
    allowed_origins = tuple(origin.strip() for origin in raw_origins.split(",") if origin.strip())
    return Settings(
        database_path=Path(raw_db),
        max_input_chars=_int_env("VERIFIER_MAX_INPUT_CHARS", 20_000, 1, 20_000),
        max_claims=_int_env("VERIFIER_MAX_CLAIMS", 15, 1, 15),
        max_queries_per_claim=_int_env("VERIFIER_MAX_QUERIES", 3, 1, 3),
        max_evidence_per_claim=_int_env("VERIFIER_MAX_EVIDENCE", 5, 1, 5),
        max_retries=_int_env("VERIFIER_MAX_RETRIES", 2, 0, 2),
        task_timeout_seconds=_int_env("VERIFIER_TASK_TIMEOUT_SECONDS", 180, 1, 180),
        request_timeout_seconds=_int_env("VERIFIER_REQUEST_TIMEOUT_SECONDS", 20, 1, 20),
        max_concurrency=_int_env("VERIFIER_MAX_CONCURRENCY", 3, 1, 3),
        live_providers=os.getenv("VERIFIER_LIVE_PROVIDERS", "").lower() in {"1", "true", "yes"},
        allowed_origins=allowed_origins,
    )
