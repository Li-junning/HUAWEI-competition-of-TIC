"""Small parameterized SQLite repository. It never stores provider secrets or raw HTML."""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable

from .schemas import Claim, ClaimState, TaskStatus, TaskSummary, now_utc


class Storage:
    def __init__(self, path: str | Path = "backend/verifier.sqlite3") -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self.init_schema()

    def init_schema(self) -> None:
        with self._lock:
            self.conn.executescript(
                """
            CREATE TABLE IF NOT EXISTS tasks (
              task_id TEXT PRIMARY KEY, status TEXT NOT NULL, input_text TEXT NOT NULL,
              input_char_count INTEGER NOT NULL, claim_limit INTEGER NOT NULL,
              truncated INTEGER NOT NULL DEFAULT 0, claims_unchecked INTEGER NOT NULL DEFAULT 0,
              failed_providers TEXT NOT NULL DEFAULT '[]',
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL, error_code TEXT
            );
            CREATE TABLE IF NOT EXISTS claims (
              claim_id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(task_id),
              data TEXT NOT NULL, state TEXT NOT NULL, retry_count INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_claims_task ON claims(task_id);
                """
            )
            columns = {row[1] for row in self.conn.execute("PRAGMA table_info(tasks)").fetchall()}
            if "claims_unchecked" not in columns:
                self.conn.execute("ALTER TABLE tasks ADD COLUMN claims_unchecked INTEGER NOT NULL DEFAULT 0")
            if "segmentation_method" not in columns:
                self.conn.execute("ALTER TABLE tasks ADD COLUMN segmentation_method TEXT NOT NULL DEFAULT 'rules'")
            self.conn.commit()

    def create_task(self, task: TaskSummary, input_text: str) -> None:
        with self._lock:
            self.conn.execute(
                "INSERT INTO tasks(task_id,status,input_text,input_char_count,claim_limit,truncated,claims_unchecked,failed_providers,created_at,updated_at,error_code) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (task.task_id, task.status.value, input_text, task.input_char_count, task.claim_limit,
                 int(task.truncated), task.claims_unchecked, json.dumps(task.failed_providers), task.created_at.isoformat(),
                 task.updated_at.isoformat(), task.error_code),
            )
            self.conn.commit()

    def set_task_status(self, task_id: str, status: TaskStatus, *, truncated: bool | None = None,
                        claims_unchecked: int | None = None, failed_providers: list[str] | None = None,
                        error_code: str | None = None, segmentation_method: str | None = None) -> None:
        fields = ["status = ?", "updated_at = ?"]
        values: list[Any] = [status.value, now_utc().isoformat()]
        if segmentation_method is not None:
            fields.append("segmentation_method = ?"); values.append(segmentation_method)
        if truncated is not None:
            fields.append("truncated = ?"); values.append(int(truncated))
        if claims_unchecked is not None:
            fields.append("claims_unchecked = ?"); values.append(max(0, claims_unchecked))
        if failed_providers is not None:
            fields.append("failed_providers = ?"); values.append(json.dumps(sorted(set(failed_providers))))
        if error_code is not None:
            fields.append("error_code = ?"); values.append(error_code)
        values.append(task_id)
        with self._lock:
            self.conn.execute(f"UPDATE tasks SET {', '.join(fields)} WHERE task_id = ?", values)
            self.conn.commit()

    def save_claim(self, claim: Claim) -> None:
        with self._lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO claims(claim_id,task_id,data,state,retry_count) VALUES(?,?,?,?,?)",
                (claim.claim_id, claim.task_id, claim.model_dump_json(), claim.state.value, claim.retry_count),
            )
            self.conn.commit()

    def begin_claim_retry(self, claim_id: str, max_retries: int) -> tuple[Claim | None, str | None]:
        """Atomically validate and reserve one retry across concurrent requests."""
        with self._lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                row = self.conn.execute(
                    "SELECT c.data, t.status FROM claims c JOIN tasks t ON t.task_id = c.task_id WHERE c.claim_id = ?",
                    (claim_id,),
                ).fetchone()
                if row is None:
                    result = (None, "NOT_FOUND")
                else:
                    claim = Claim.model_validate_json(row["data"])
                    status = row["status"]
                    if status in {TaskStatus.CREATED.value, TaskStatus.RUNNING.value}:
                        result = (None, "TASK_BUSY")
                    elif claim.retry_count >= max_retries:
                        result = (None, "RETRY_LIMIT")
                    elif claim.label and claim.label.value not in {"evidence_insufficient"} and claim.state.value != "failed":
                        result = (None, "RETRY_NOT_ALLOWED")
                    else:
                        claim.retry_count += 1
                        claim.state = ClaimState.RETRIEVING
                        self.conn.execute(
                            "UPDATE claims SET data = ?, state = ?, retry_count = ? WHERE claim_id = ?",
                            (claim.model_dump_json(), claim.state.value, claim.retry_count, claim_id),
                        )
                        self.conn.execute(
                            "UPDATE tasks SET status = ?, updated_at = ? WHERE task_id = ?",
                            (TaskStatus.RUNNING.value, now_utc().isoformat(), claim.task_id),
                        )
                        result = (claim, None)
                self.conn.commit()
                return result
            except BaseException:
                self.conn.rollback()
                raise

    def get_claim(self, claim_id: str) -> Claim | None:
        with self._lock:
            row = self.conn.execute("SELECT data FROM claims WHERE claim_id = ?", (claim_id,)).fetchone()
        return Claim.model_validate_json(row[0]) if row else None

    def list_claims(self, task_id: str, offset: int = 0, limit: int = 20) -> tuple[list[Claim], int]:
        with self._lock:
            rows = self.conn.execute("SELECT data FROM claims WHERE task_id = ? ORDER BY rowid LIMIT ? OFFSET ?",
                                     (task_id, limit, offset)).fetchall()
            total = self.conn.execute("SELECT COUNT(*) FROM claims WHERE task_id = ?", (task_id,)).fetchone()[0]
        return [Claim.model_validate_json(row[0]) for row in rows], int(total)

    def get_task(self, task_id: str) -> tuple[sqlite3.Row, list[Claim]] | None:
        with self._lock:
            row = self.conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        if not row:
            return None
        claims, _ = self.list_claims(task_id, 0, 1000)
        return row, claims

    def mark_running_interrupted(self) -> int:
        with self._lock:
            cur = self.conn.execute("UPDATE tasks SET status = ?, updated_at = ? WHERE status IN (?, ?)",
                                    (TaskStatus.INTERRUPTED.value, now_utc().isoformat(),
                                     TaskStatus.CREATED.value, TaskStatus.RUNNING.value))
            self.conn.commit()
        return cur.rowcount

    def close(self) -> None:
        with self._lock:
            self.conn.close()
