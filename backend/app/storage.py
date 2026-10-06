"""Small parameterized SQLite repository. It never stores provider secrets or raw HTML."""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable

from .reference_resolution import has_unresolved_reference
from .schemas import Claim, ClaimState, TaskStatus, TaskSummary, new_id, now_utc
from .scoring import support_score


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
            CREATE TABLE IF NOT EXISTS review_events (
              event_id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(task_id),
              action TEXT NOT NULL, reviewer TEXT NOT NULL, created_at TEXT NOT NULL,
              before_claims TEXT NOT NULL, after_claims TEXT NOT NULL,
              before_text TEXT NOT NULL, after_text TEXT NOT NULL,
              undone_at TEXT, undone_by TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_review_events_task ON review_events(task_id, created_at);
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
                        error_code: str | None = None, clear_error_code: bool = False,
                        segmentation_method: str | None = None) -> None:
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
        if error_code is not None or clear_error_code:
            fields.append("error_code = ?"); values.append(error_code)
        values.append(task_id)
        with self._lock:
            self.conn.execute(f"UPDATE tasks SET {', '.join(fields)} WHERE task_id = ?", values)
            self.conn.commit()

    def save_claim(self, claim: Claim) -> None:
        claim.support_score = support_score(claim)
        with self._lock:
            self.conn.execute(
                "INSERT INTO claims(claim_id,task_id,data,state,retry_count) VALUES(?,?,?,?,?) "
                "ON CONFLICT(claim_id) DO UPDATE SET task_id=excluded.task_id,data=excluded.data,"
                "state=excluded.state,retry_count=excluded.retry_count",
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
                    elif has_unresolved_reference(claim.source_text, claim.normalized_claim):
                        result = (None, "RETRY_NOT_ALLOWED")
                    elif claim.retry_count >= max_retries:
                        result = (None, "RETRY_LIMIT")
                    elif not (
                        claim.state.value == "failed"
                        or (claim.label and claim.label.value == "evidence_insufficient")
                        or claim.retrieval_warnings
                        or (claim.manually_edited and claim.state.value == "unchecked")
                        or (claim.state == ClaimState.UNCHECKED and (claim.unchecked_reason == "task_budget"
                            or (claim.reason or "").startswith("任务总预算已耗尽")))
                    ):
                        result = (None, "RETRY_NOT_ALLOWED")
                    else:
                        claim.retry_count += 1
                        claim.state = ClaimState.RETRIEVING
                        claim.label = None
                        claim.support_score = None
                        claim.unchecked_reason = None
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
        return self._load_claim(row[0]) if row else None

    @staticmethod
    def _load_claim(data: str) -> Claim:
        claim = Claim.model_validate_json(data)
        # Also derive missing scores on older reports without rewriting them.
        claim.support_score = support_score(claim)
        return claim

    def edit_claim(self, claim_id: str, normalized_claim: str) -> tuple[Claim | None, str | None]:
        """Apply a user's wording correction without retaining a stale verdict."""
        with self._lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                row = self.conn.execute(
                    "SELECT c.data, t.status FROM claims c JOIN tasks t ON t.task_id=c.task_id WHERE c.claim_id=?",
                    (claim_id,),
                ).fetchone()
                if row is None:
                    result = (None, "NOT_FOUND")
                elif row["status"] in {TaskStatus.CREATED.value, TaskStatus.RUNNING.value}:
                    result = (None, "TASK_BUSY")
                else:
                    claim = Claim.model_validate_json(row["data"])
                    claim.normalized_claim = normalized_claim.strip()
                    claim.manually_edited = True
                    claim.unchecked_reason = None
                    claim.label = None
                    claim.support_score = None
                    claim.reason = "声明已由用户修改，当前没有对应的新证据判断。"
                    claim.state = ClaimState.UNCHECKED
                    claim.retry_count = 0
                    claim.type = "general"
                    claim.entities = []
                    claim.conditions = []
                    claim.queries = []
                    claim.evidence_clusters = []
                    claim.evidence_cluster_ids = []
                    claim.paper_check = None
                    self.conn.execute("UPDATE claims SET data=?, state=?, retry_count=0 WHERE claim_id=?",
                                      (claim.model_dump_json(), claim.state.value, claim_id))
                    self.conn.execute("UPDATE tasks SET updated_at=? WHERE task_id=?",
                                      (now_utc().isoformat(), claim.task_id))
                    result = (claim, None)
                self.conn.commit()
                return result
            except BaseException:
                self.conn.rollback()
                raise

    def delete_claim(self, claim_id: str) -> tuple[bool, str | None]:
        with self._lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                row = self.conn.execute(
                    "SELECT c.task_id, t.status FROM claims c JOIN tasks t ON t.task_id=c.task_id WHERE c.claim_id=?",
                    (claim_id,),
                ).fetchone()
                if row is None:
                    result = (False, "NOT_FOUND")
                elif row["status"] in {TaskStatus.CREATED.value, TaskStatus.RUNNING.value}:
                    result = (False, "TASK_BUSY")
                else:
                    self.conn.execute("DELETE FROM claims WHERE claim_id=?", (claim_id,))
                    self.conn.execute("UPDATE tasks SET updated_at=? WHERE task_id=?",
                                      (now_utc().isoformat(), row["task_id"]))
                    result = (True, None)
                self.conn.commit()
                return result
            except BaseException:
                self.conn.rollback()
                raise

    def list_claims(self, task_id: str, offset: int = 0, limit: int = 20) -> tuple[list[Claim], int]:
        with self._lock:
            rows = self.conn.execute("SELECT data FROM claims WHERE task_id = ? ORDER BY rowid LIMIT ? OFFSET ?",
                                     (task_id, limit, offset)).fetchall()
            total = self.conn.execute("SELECT COUNT(*) FROM claims WHERE task_id = ?", (task_id,)).fetchone()[0]
        return [self._load_claim(row[0]) for row in rows], int(total)

    def review_change(self, task_id: str, action: str, reviewer: str, *,
                      claim_ids: list[str] | None = None, char_start: int | None = None,
                      char_end: int | None = None, split_at: int | None = None,
                      texts: list[str] | None = None) -> tuple[list[Claim] | None, str | None]:
        """Apply one manual change and save a reversible snapshot atomically."""
        reviewer = reviewer.strip()
        texts = [value.strip() for value in (texts or [])]
        claim_ids = claim_ids or []
        if not reviewer or any(not value for value in texts):
            return None, "INVALID_REVIEW"
        with self._lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                task = self.conn.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
                if task is None:
                    return self._finish_review(None, "NOT_FOUND")
                if task["status"] in {TaskStatus.CREATED.value, TaskStatus.RUNNING.value}:
                    return self._finish_review(None, "TASK_BUSY")
                rows = self.conn.execute("SELECT data FROM claims WHERE task_id=? ORDER BY rowid", (task_id,)).fetchall()
                before = [Claim.model_validate_json(row["data"]) for row in rows]
                claims = [claim.model_copy(deep=True) for claim in before]
                by_id = {claim.claim_id: claim for claim in claims}
                if any(claim_id not in by_id for claim_id in claim_ids):
                    return self._finish_review(None, "NOT_FOUND")
                original = task["input_text"]

                def source_slice(start: int, end: int) -> str | None:
                    raw = original.encode("utf-16-le")
                    if start < 0 or end <= start or end * 2 > len(raw):
                        return None
                    try:
                        value = raw[start * 2:end * 2].decode("utf-16-le")
                    except UnicodeDecodeError:
                        return None
                    return value if value.strip() else None

                def unchecked(claim: Claim, wording: str) -> None:
                    claim.normalized_claim = wording
                    claim.manually_edited = True
                    claim.unchecked_reason = None
                    claim.label = None
                    claim.support_score = None
                    claim.reason = "人工调整了声明，尚无对应的新证据判断。"
                    claim.state = ClaimState.UNCHECKED
                    claim.retry_count = 0
                    claim.type = "general"
                    claim.entities = []
                    claim.conditions = []
                    claim.queries = []
                    claim.evidence_clusters = []
                    claim.evidence_cluster_ids = []
                    claim.paper_check = None

                if action == "edit" and len(claim_ids) == 1 and len(texts) == 1:
                    unchecked(by_id[claim_ids[0]], texts[0])
                    result_ids = claim_ids
                elif action == "delete" and len(claim_ids) == 1:
                    claims = [claim for claim in claims if claim.claim_id != claim_ids[0]]
                    result_ids = []
                elif action == "add" and len(texts) == 1 and char_start is not None and char_end is not None:
                    source = source_slice(char_start, char_end)
                    if source is None or any(char_start < claim.char_end and claim.char_start < char_end for claim in claims):
                        return self._finish_review(None, "INVALID_RANGE")
                    claim = Claim(claim_id=new_id("c"), task_id=task_id, source_text=source,
                                  char_start=char_start, char_end=char_end, normalized_claim=texts[0])
                    unchecked(claim, texts[0])
                    claims.append(claim)
                    result_ids = [claim.claim_id]
                elif action == "split" and len(claim_ids) == 1 and len(texts) == 2 and split_at is not None:
                    original_claim = by_id[claim_ids[0]]
                    if not original_claim.char_start < split_at < original_claim.char_end:
                        return self._finish_review(None, "INVALID_RANGE")
                    left = source_slice(original_claim.char_start, split_at)
                    right = source_slice(split_at, original_claim.char_end)
                    if left is None or right is None:
                        return self._finish_review(None, "INVALID_RANGE")
                    original_end = original_claim.char_end
                    original_claim.source_text = left
                    original_claim.char_end = split_at
                    unchecked(original_claim, texts[0])
                    second = Claim(claim_id=new_id("c"), task_id=task_id, source_text=right,
                                   char_start=split_at, char_end=original_end,
                                   normalized_claim=texts[1])
                    unchecked(second, texts[1])
                    claims.append(second)
                    result_ids = [original_claim.claim_id, second.claim_id]
                elif action == "merge" and len(claim_ids) == 2 and len(texts) == 1 and claim_ids[0] != claim_ids[1]:
                    ordered = sorted(claims, key=lambda claim: (claim.char_start, claim.char_end))
                    first, second = sorted((by_id[claim_ids[0]], by_id[claim_ids[1]]), key=lambda claim: claim.char_start)
                    if ordered.index(second) != ordered.index(first) + 1 or first.char_end > second.char_start:
                        return self._finish_review(None, "INVALID_RANGE")
                    source = source_slice(first.char_start, second.char_end)
                    if source is None:
                        return self._finish_review(None, "INVALID_RANGE")
                    first.source_text = source
                    first.char_end = second.char_end
                    unchecked(first, texts[0])
                    claims = [claim for claim in claims if claim.claim_id != second.claim_id]
                    result_ids = [first.claim_id]
                else:
                    return self._finish_review(None, "INVALID_REVIEW")
                if len(claims) > task["claim_limit"]:
                    return self._finish_review(None, "CLAIM_LIMIT")
                claims.sort(key=lambda claim: (claim.char_start, claim.char_end))
                before_json = json.dumps([claim.model_dump(mode="json") for claim in before], ensure_ascii=False)
                after_json = json.dumps([claim.model_dump(mode="json") for claim in claims], ensure_ascii=False)
                def descriptions(items: list[Claim], ids: list[str]) -> str:
                    return "；".join(claim.normalized_claim for claim in items if claim.claim_id in ids)[:4000]
                before_text = descriptions(before, claim_ids)
                after_text = descriptions(claims, result_ids)
                self.conn.execute("DELETE FROM claims WHERE task_id=?", (task_id,))
                for claim in claims:
                    self.conn.execute("INSERT INTO claims(claim_id,task_id,data,state,retry_count) VALUES(?,?,?,?,?)",
                                      (claim.claim_id, task_id, claim.model_dump_json(), claim.state.value, claim.retry_count))
                timestamp = now_utc().isoformat()
                self.conn.execute("UPDATE tasks SET updated_at=? WHERE task_id=?", (timestamp, task_id))
                self.conn.execute("INSERT INTO review_events(event_id,task_id,action,reviewer,created_at,before_claims,after_claims,before_text,after_text) VALUES(?,?,?,?,?,?,?,?,?)",
                                  (new_id("r"), task_id, action, reviewer, timestamp, before_json, after_json, before_text, after_text))
                return self._finish_review([claim for claim in claims if claim.claim_id in result_ids], None)
            except BaseException:
                self.conn.rollback()
                raise

    def _finish_review(self, claims: list[Claim] | None, error: str | None) -> tuple[list[Claim] | None, str | None]:
        if error:
            self.conn.rollback()
        else:
            self.conn.commit()
        return claims, error

    def list_review_events(self, task_id: str) -> list[dict[str, Any]] | None:
        with self._lock:
            if self.conn.execute("SELECT 1 FROM tasks WHERE task_id=?", (task_id,)).fetchone() is None:
                return None
            rows = self.conn.execute("SELECT event_id, action, reviewer, created_at, before_text, after_text, undone_at, undone_by FROM review_events WHERE task_id=? ORDER BY rowid DESC", (task_id,)).fetchall()
        return [dict(row) for row in rows]

    def undo_review_event(self, task_id: str, event_id: str, reviewer: str) -> str | None:
        if not reviewer.strip():
            return "INVALID_REVIEW"
        with self._lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                task = self.conn.execute("SELECT status FROM tasks WHERE task_id=?", (task_id,)).fetchone()
                if task is None:
                    return self._finish_undo("NOT_FOUND")
                if task["status"] in {TaskStatus.CREATED.value, TaskStatus.RUNNING.value}:
                    return self._finish_undo("TASK_BUSY")
                event = self.conn.execute("SELECT * FROM review_events WHERE event_id=? AND task_id=?", (event_id, task_id)).fetchone()
                if event is None:
                    return self._finish_undo("NOT_FOUND")
                latest = self.conn.execute("SELECT event_id FROM review_events WHERE task_id=? AND undone_at IS NULL ORDER BY rowid DESC LIMIT 1", (task_id,)).fetchone()
                if event["undone_at"] or latest is None or latest["event_id"] != event_id:
                    return self._finish_undo("UNDO_ORDER")
                rows = self.conn.execute("SELECT data FROM claims WHERE task_id=? ORDER BY rowid", (task_id,)).fetchall()
                current = [json.loads(row["data"]) for row in rows]
                if current != json.loads(event["after_claims"]):
                    return self._finish_undo("STATE_CHANGED")
                self.conn.execute("DELETE FROM claims WHERE task_id=?", (task_id,))
                for data in json.loads(event["before_claims"]):
                    claim = Claim.model_validate(data)
                    self.conn.execute("INSERT INTO claims(claim_id,task_id,data,state,retry_count) VALUES(?,?,?,?,?)",
                                      (claim.claim_id, task_id, claim.model_dump_json(), claim.state.value, claim.retry_count))
                timestamp = now_utc().isoformat()
                self.conn.execute("UPDATE review_events SET undone_at=?, undone_by=? WHERE event_id=?", (timestamp, reviewer.strip(), event_id))
                self.conn.execute("UPDATE tasks SET updated_at=? WHERE task_id=?", (timestamp, task_id))
                return self._finish_undo(None)
            except BaseException:
                self.conn.rollback()
                raise

    def _finish_undo(self, error: str | None) -> str | None:
        if error:
            self.conn.rollback()
        else:
            self.conn.commit()
        return error

    def get_task(self, task_id: str) -> tuple[sqlite3.Row, list[Claim]] | None:
        with self._lock:
            row = self.conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        if not row:
            return None
        claims, _ = self.list_claims(task_id, 0, 1000)
        return row, claims

    def mark_running_interrupted(self) -> int:
        """Finish in-flight claims and their tasks together after a restart."""
        with self._lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                active_states = (
                    ClaimState.PENDING.value, ClaimState.EXTRACTING.value,
                    ClaimState.RETRIEVING.value, ClaimState.JUDGING.value,
                )
                rows = self.conn.execute(
                    "SELECT c.claim_id, c.data FROM claims c JOIN tasks t ON t.task_id = c.task_id "
                    "WHERE t.status IN (?, ?) AND c.state IN (?, ?, ?, ?)",
                    (TaskStatus.CREATED.value, TaskStatus.RUNNING.value, *active_states),
                ).fetchall()
                for row in rows:
                    claim = Claim.model_validate_json(row["data"])
                    claim.state = ClaimState.FAILED
                    claim.label = None
                    claim.support_score = None
                    claim.reason = "服务重启中断了本次核验，该声明尚未完成；可以重试。未将中断当作反证。"
                    self.conn.execute(
                        "UPDATE claims SET data = ?, state = ? WHERE claim_id = ?",
                        (claim.model_dump_json(), claim.state.value, claim.claim_id),
                    )
                cur = self.conn.execute(
                    "UPDATE tasks SET status = ?, updated_at = ? WHERE status IN (?, ?)",
                    (TaskStatus.INTERRUPTED.value, now_utc().isoformat(),
                     TaskStatus.CREATED.value, TaskStatus.RUNNING.value),
                )
                self.conn.commit()
                return cur.rowcount
            except BaseException:
                self.conn.rollback()
                raise

    def close(self) -> None:
        with self._lock:
            self.conn.close()
