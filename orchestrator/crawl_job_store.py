import asyncio
import json
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from .models import CrawlJobState, CrawlRequest, FetchResult
from .outcome_codes import JobOutcomeCode

JOB_SCHEMA_VERSION = 1
TERMINAL_JOB_STATES = frozenset(
    {
        JobOutcomeCode.COMPLETED.value,
        JobOutcomeCode.PARTIAL.value,
        JobOutcomeCode.FAILED.value,
        JobOutcomeCode.CANCELLED.value,
    }
)


class IdempotencyConflict(Exception):
    pass


class CrawlJobNotFound(Exception):
    pass


class CrawlJobExpired(Exception):
    def __init__(self, record):
        super().__init__(record.job_id)
        self.record = record


class CrawlJobResultTooLarge(Exception):
    pass


class CrawlJobQueueFull(Exception):
    pass


@dataclass(frozen=True)
class CrawlJobRecord:
    job_id: str
    scope_hash: str
    request_fingerprint: str
    request: CrawlRequest
    state: CrawlJobState
    created_at: float
    started_at: float | None
    terminal_at: float | None
    retention_deadline: float | None
    cancel_requested: bool
    pages_target: int
    pages_processed: int
    pages_succeeded: int
    pages_failed: int
    results_available: int
    attempts: int
    failure_summaries: tuple[str, ...]
    outcome: str | None
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class ClaimResult:
    record: CrawlJobRecord
    replayed: bool


@dataclass(frozen=True)
class StoredResultPage:
    results: tuple[FetchResult, ...]
    last_ordinal: int
    returned_bytes: int
    has_more: bool


class SqliteCrawlJobStore:
    def __init__(
        self,
        path: str,
        *,
        retention_s: int,
        tombstone_s: int,
        max_failure_summaries: int,
        max_failure_summary_chars: int,
        max_result_item_bytes: int,
        max_records: int = 100,
        clock=time.time,
    ):
        self.path = Path(path)
        self.retention_s = retention_s
        self.tombstone_s = tombstone_s
        self.max_failure_summaries = max_failure_summaries
        self.max_failure_summary_chars = max_failure_summary_chars
        self.max_result_item_bytes = max_result_item_bytes
        self.max_records = max_records
        self.clock = clock

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize_with_recovery(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._initialize()
        except sqlite3.DatabaseError as exc:
            message = str(exc).casefold()
            if not any(
                marker in message
                for marker in (
                    "malformed",
                    "not a database",
                    "file is encrypted",
                    "incompatible crawl job schema",
                    "no such column",
                )
            ):
                raise
            if self.path.exists():
                suffix = time.strftime("%Y%m%d%H%M%S", time.gmtime())
                quarantine = Path(f"{self.path}.corrupt-{suffix}")
                self.path.replace(quarantine)
                for sidecar_suffix in ("-wal", "-shm"):
                    sidecar = Path(f"{self.path}{sidecar_suffix}")
                    if sidecar.exists():
                        sidecar.replace(Path(f"{quarantine}{sidecar_suffix}"))
            self._initialize()

    def _initialize(self) -> None:
        with self._transaction() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                raise sqlite3.DatabaseError("malformed crawl job database")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS crawl_job_schema (version INTEGER NOT NULL)"
            )
            row = connection.execute("SELECT version FROM crawl_job_schema").fetchone()
            if row is None:
                connection.execute(
                    "INSERT INTO crawl_job_schema(version) VALUES (?)",
                    (JOB_SCHEMA_VERSION,),
                )
            elif row["version"] > JOB_SCHEMA_VERSION:
                version = row["version"]
                raise RuntimeError(
                    f"crawl job schema {version} is newer than supported "
                    f"{JOB_SCHEMA_VERSION}"
                )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS crawl_jobs (
                    job_id TEXT PRIMARY KEY,
                    scope_hash TEXT NOT NULL,
                    idempotency_hash TEXT,
                    request_fingerprint TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    state TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    started_at REAL,
                    terminal_at REAL,
                    retention_deadline REAL,
                    purge_after REAL,
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    pages_target INTEGER NOT NULL,
                    pages_processed INTEGER NOT NULL DEFAULT 0,
                    pages_succeeded INTEGER NOT NULL DEFAULT 0,
                    pages_failed INTEGER NOT NULL DEFAULT 0,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    failure_summaries_json TEXT NOT NULL DEFAULT '[]',
                    outcome TEXT,
                    warnings_json TEXT NOT NULL DEFAULT '[]'
                )
                """
            )
            connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS crawl_jobs_idempotency
                ON crawl_jobs(scope_hash, idempotency_hash)
                WHERE idempotency_hash IS NOT NULL
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS crawl_jobs_state_created
                ON crawl_jobs(state, created_at)
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS crawl_jobs_retention ON crawl_jobs(retention_deadline)"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS crawl_job_results (
                    job_id TEXT NOT NULL,
                    result_key TEXT NOT NULL,
                    ordinal INTEGER NOT NULL,
                    result_json TEXT NOT NULL,
                    item_bytes INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    PRIMARY KEY(job_id, result_key),
                    UNIQUE(job_id, ordinal),
                    FOREIGN KEY(job_id) REFERENCES crawl_jobs(job_id) ON DELETE CASCADE
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS crawl_job_failures (
                    job_id TEXT NOT NULL,
                    failure_key TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    PRIMARY KEY(job_id, failure_key),
                    FOREIGN KEY(job_id) REFERENCES crawl_jobs(job_id) ON DELETE CASCADE
                )
                """
            )
            expected_columns = {
                "crawl_jobs": {
                    "job_id",
                    "scope_hash",
                    "idempotency_hash",
                    "request_fingerprint",
                    "request_json",
                    "state",
                    "created_at",
                    "started_at",
                    "terminal_at",
                    "retention_deadline",
                    "purge_after",
                    "cancel_requested",
                    "pages_target",
                    "pages_processed",
                    "pages_succeeded",
                    "pages_failed",
                    "attempts",
                    "failure_summaries_json",
                    "outcome",
                    "warnings_json",
                },
                "crawl_job_results": {
                    "job_id",
                    "result_key",
                    "ordinal",
                    "result_json",
                    "item_bytes",
                    "created_at",
                },
                "crawl_job_failures": {
                    "job_id",
                    "failure_key",
                    "summary",
                    "created_at",
                },
            }
            for table, expected in expected_columns.items():
                actual = {
                    column["name"]
                    for column in connection.execute(f"PRAGMA table_info({table})")
                }
                if not expected.issubset(actual):
                    raise sqlite3.DatabaseError("incompatible crawl job schema")
            connection.execute(
                "UPDATE crawl_job_schema SET version = ?",
                (JOB_SCHEMA_VERSION,),
            )

    async def start(self) -> None:
        await asyncio.to_thread(self._initialize_with_recovery)

    def _expire_due(self, connection: sqlite3.Connection, now: float) -> None:
        due = connection.execute(
            """
            SELECT job_id, retention_deadline
            FROM crawl_jobs
            WHERE state IN ('completed', 'partial', 'failed', 'cancelled')
              AND retention_deadline <= ?
            """,
            (now,),
        ).fetchall()
        for row in due:
            purge_after = row["retention_deadline"] + self.tombstone_s
            connection.execute(
                "DELETE FROM crawl_job_results WHERE job_id = ?",
                (row["job_id"],),
            )
            connection.execute(
                "DELETE FROM crawl_job_failures WHERE job_id = ?",
                (row["job_id"],),
            )
            connection.execute(
                """
                UPDATE crawl_jobs
                SET state = 'expired', idempotency_hash = NULL,
                    cancel_requested = 0, purge_after = ?
                WHERE job_id = ?
                """,
                (purge_after, row["job_id"]),
            )
        expired = connection.execute(
            "SELECT job_id FROM crawl_jobs WHERE state = 'expired' AND purge_after <= ?",
            (now,),
        ).fetchall()
        if expired:
            connection.executemany(
                "DELETE FROM crawl_jobs WHERE job_id = ?",
                ((row["job_id"],) for row in expired),
            )

    @staticmethod
    def _record(connection: sqlite3.Connection, row: sqlite3.Row) -> CrawlJobRecord:
        results_available = connection.execute(
            "SELECT COUNT(*) FROM crawl_job_results WHERE job_id = ?",
            (row["job_id"],),
        ).fetchone()[0]
        return CrawlJobRecord(
            job_id=row["job_id"],
            scope_hash=row["scope_hash"],
            request_fingerprint=row["request_fingerprint"],
            request=CrawlRequest.model_validate_json(row["request_json"]),
            state=row["state"],
            created_at=row["created_at"],
            started_at=row["started_at"],
            terminal_at=row["terminal_at"],
            retention_deadline=row["retention_deadline"],
            cancel_requested=bool(row["cancel_requested"]),
            pages_target=row["pages_target"],
            pages_processed=row["pages_processed"],
            pages_succeeded=row["pages_succeeded"],
            pages_failed=row["pages_failed"],
            results_available=results_available,
            attempts=row["attempts"],
            failure_summaries=tuple(json.loads(row["failure_summaries_json"])),
            outcome=row["outcome"],
            warnings=tuple(json.loads(row["warnings_json"])),
        )

    def _claim(
        self,
        *,
        job_id: str,
        scope_hash: str,
        idempotency_hash: str,
        request_fingerprint: str,
        request: CrawlRequest,
    ) -> ClaimResult:
        now = self.clock()
        result = None
        conflict = False
        full = False
        with self._transaction() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._expire_due(connection, now)
            row = connection.execute(
                """
                SELECT * FROM crawl_jobs
                WHERE scope_hash = ? AND idempotency_hash = ?
                """,
                (scope_hash, idempotency_hash),
            ).fetchone()
            if row is not None:
                if row["request_fingerprint"] != request_fingerprint:
                    conflict = True
                else:
                    result = ClaimResult(self._record(connection, row), True)
            elif (
                connection.execute("SELECT COUNT(*) FROM crawl_jobs").fetchone()[0]
                >= self.max_records
            ):
                full = True
            else:
                connection.execute(
                    """
                    INSERT INTO crawl_jobs (
                        job_id, scope_hash, idempotency_hash, request_fingerprint,
                        request_json, state, created_at, pages_target
                    ) VALUES (?, ?, ?, ?, ?, 'queued', ?, ?)
                    """,
                    (
                        job_id,
                        scope_hash,
                        idempotency_hash,
                        request_fingerprint,
                        request.model_dump_json(),
                        now,
                        request.max_pages,
                    ),
                )
                row = connection.execute(
                    "SELECT * FROM crawl_jobs WHERE job_id = ?",
                    (job_id,),
                ).fetchone()
                result = ClaimResult(self._record(connection, row), False)
        if conflict:
            raise IdempotencyConflict
        if full:
            raise CrawlJobQueueFull
        return result

    async def claim(self, **values) -> ClaimResult:
        return await asyncio.to_thread(self._claim, **values)

    def _get(self, job_id: str, scope_hash: str) -> CrawlJobRecord:
        now = self.clock()
        record = None
        with self._transaction() as connection:
            self._expire_due(connection, now)
            row = connection.execute(
                "SELECT * FROM crawl_jobs WHERE job_id = ? AND scope_hash = ?",
                (job_id, scope_hash),
            ).fetchone()
            if row is not None:
                record = self._record(connection, row)
        if record is None:
            raise CrawlJobNotFound(job_id)
        if record.state == JobOutcomeCode.EXPIRED.value:
            raise CrawlJobExpired(record)
        return record

    async def get(self, job_id: str, scope_hash: str) -> CrawlJobRecord:
        return await asyncio.to_thread(self._get, job_id, scope_hash)

    def _get_any(self, job_id: str) -> CrawlJobRecord | None:
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM crawl_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            return self._record(connection, row) if row is not None else None

    async def get_any(self, job_id: str) -> CrawlJobRecord | None:
        return await asyncio.to_thread(self._get_any, job_id)

    def _recover_interrupted(self, max_attempts: int) -> int:
        now = self.clock()
        with self._transaction() as connection:
            cancelled = connection.execute(
                """
                UPDATE crawl_jobs
                SET state = 'cancelled', terminal_at = ?, retention_deadline = ?,
                    cancel_requested = 0, outcome = 'cancelled'
                WHERE state = 'running' AND cancel_requested = 1
                """,
                (now, now + self.retention_s),
            ).rowcount
            exhausted = connection.execute(
                """
                UPDATE crawl_jobs
                SET state = 'failed', terminal_at = ?, retention_deadline = ?,
                    outcome = 'local_processing_failure',
                    failure_summaries_json = '["restart_retry_exhausted"]'
                WHERE state = 'running' AND attempts >= ?
                """,
                (now, now + self.retention_s, max_attempts),
            ).rowcount
            cursor = connection.execute(
                """
                UPDATE crawl_jobs
                SET state = 'queued'
                WHERE state = 'running' AND attempts < ?
                """,
                (max_attempts,),
            )
            return cancelled + exhausted + cursor.rowcount

    async def recover_interrupted(self, max_attempts: int) -> int:
        return await asyncio.to_thread(self._recover_interrupted, max_attempts)

    def _claim_next(self) -> CrawlJobRecord | None:
        now = self.clock()
        with self._transaction() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._expire_due(connection, now)
            row = connection.execute(
                """
                SELECT * FROM crawl_jobs
                WHERE state = 'queued'
                ORDER BY created_at, job_id
                LIMIT 1
                """
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                """
                UPDATE crawl_jobs
                SET state = 'running', started_at = COALESCE(started_at, ?),
                    attempts = attempts + 1
                WHERE job_id = ? AND state = 'queued'
                """,
                (now, row["job_id"]),
            )
            claimed = connection.execute(
                "SELECT * FROM crawl_jobs WHERE job_id = ?",
                (row["job_id"],),
            ).fetchone()
            return self._record(connection, claimed)

    async def claim_next(self) -> CrawlJobRecord | None:
        return await asyncio.to_thread(self._claim_next)

    def _increment_attempt(self, job_id: str) -> CrawlJobRecord:
        with self._transaction() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE crawl_jobs SET attempts = attempts + 1
                WHERE job_id = ? AND state = 'running'
                """,
                (job_id,),
            )
            row = connection.execute(
                "SELECT * FROM crawl_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            if row is None:
                raise CrawlJobNotFound(job_id)
            return self._record(connection, row)

    async def increment_attempt(self, job_id: str) -> CrawlJobRecord:
        return await asyncio.to_thread(self._increment_attempt, job_id)

    def _append_result(
        self,
        job_id: str,
        result_key: str,
        result: FetchResult,
        failure_keys: tuple[str, ...],
    ) -> bool:
        encoded = result.model_dump_json()
        item_bytes = len(encoded.encode("utf-8"))
        if item_bytes > self.max_result_item_bytes:
            raise CrawlJobResultTooLarge(item_bytes)
        now = self.clock()
        with self._transaction() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT 1 FROM crawl_job_results
                WHERE job_id = ? AND result_key = ?
                """,
                (job_id, result_key),
            ).fetchone()
            if existing is not None:
                return False
            ordinal = connection.execute(
                "SELECT COALESCE(MAX(ordinal), 0) + 1 FROM crawl_job_results WHERE job_id = ?",
                (job_id,),
            ).fetchone()[0]
            connection.execute(
                """
                INSERT INTO crawl_job_results (
                    job_id, result_key, ordinal, result_json, item_bytes, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (job_id, result_key, ordinal, encoded, item_bytes, now),
            )
            success = result.outcome == "content"
            reconciled_keys = tuple(dict.fromkeys((result_key, *failure_keys)))
            placeholders = ",".join("?" for _ in reconciled_keys)
            prior_failures = connection.execute(
                f"""
                SELECT COUNT(*) FROM crawl_job_failures
                WHERE job_id = ? AND failure_key IN ({placeholders})
                """,
                (job_id, *reconciled_keys),
            ).fetchone()[0]
            if prior_failures:
                connection.execute(
                    f"""
                    DELETE FROM crawl_job_failures
                    WHERE job_id = ? AND failure_key IN ({placeholders})
                    """,
                    (job_id, *reconciled_keys),
                )
            connection.execute(
                """
                UPDATE crawl_jobs
                SET pages_processed = MAX(0, pages_processed - ? + 1),
                    pages_succeeded = pages_succeeded + ?,
                    pages_failed = MAX(0, pages_failed - ? + ?)
                WHERE job_id = ? AND state = 'running'
                """,
                (
                    prior_failures,
                    1 if success else 0,
                    prior_failures,
                    0 if success else 1,
                    job_id,
                ),
            )
            return True

    async def append_result(
        self,
        job_id: str,
        result_key: str,
        result: FetchResult,
        failure_keys: tuple[str, ...] = (),
    ) -> bool:
        return await asyncio.to_thread(
            self._append_result,
            job_id,
            result_key,
            result,
            failure_keys,
        )

    def _append_failure(self, job_id: str, failure_key: str, summary: str) -> None:
        bounded = summary[: self.max_failure_summary_chars]
        with self._transaction() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute(
                "SELECT 1 FROM crawl_job_results WHERE job_id = ? AND result_key = ?",
                (job_id, failure_key),
            ).fetchone() is not None:
                return
            inserted = connection.execute(
                """
                INSERT OR IGNORE INTO crawl_job_failures (
                    job_id, failure_key, summary, created_at
                ) VALUES (?, ?, ?, ?)
                """,
                (job_id, failure_key, bounded, self.clock()),
            ).rowcount
            if not inserted:
                connection.execute(
                    """
                    UPDATE crawl_job_failures SET summary = ?
                    WHERE job_id = ? AND failure_key = ?
                    """,
                    (bounded, job_id, failure_key),
                )
            row = connection.execute(
                "SELECT failure_summaries_json FROM crawl_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            if row is None:
                return
            values = json.loads(row["failure_summaries_json"])
            if bounded not in values and len(values) < self.max_failure_summaries:
                values.append(bounded)
            connection.execute(
                """
                UPDATE crawl_jobs
                SET failure_summaries_json = ?, pages_processed = pages_processed + ?,
                    pages_failed = pages_failed + ?
                WHERE job_id = ? AND state = 'running'
                """,
                (
                    json.dumps(values, separators=(",", ":")),
                    1 if inserted else 0,
                    1 if inserted else 0,
                    job_id,
                ),
            )

    async def append_failure(self, job_id: str, failure_key: str, summary: str) -> None:
        await asyncio.to_thread(self._append_failure, job_id, failure_key, summary)

    def _append_summary(self, job_id: str, summary: str) -> None:
        bounded = summary[: self.max_failure_summary_chars]
        with self._transaction() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT failure_summaries_json FROM crawl_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            if row is None:
                return
            values = json.loads(row["failure_summaries_json"])
            if bounded not in values and len(values) < self.max_failure_summaries:
                values.append(bounded)
                connection.execute(
                    "UPDATE crawl_jobs SET failure_summaries_json = ? WHERE job_id = ?",
                    (json.dumps(values, separators=(",", ":")), job_id),
                )

    async def append_summary(self, job_id: str, summary: str) -> None:
        await asyncio.to_thread(self._append_summary, job_id, summary)

    def _finish(
        self,
        job_id: str,
        state: CrawlJobState,
        outcome: str | None,
        warnings: tuple[str, ...],
    ) -> CrawlJobRecord:
        now = self.clock()
        retention_deadline = now + self.retention_s
        with self._transaction() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM crawl_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            if row is None:
                raise CrawlJobNotFound(job_id)
            if row["state"] in TERMINAL_JOB_STATES or row["state"] == "expired":
                return self._record(connection, row)
            final_state = "cancelled" if row["cancel_requested"] else state
            connection.execute(
                """
                UPDATE crawl_jobs
                SET state = ?, terminal_at = ?, retention_deadline = ?,
                    outcome = ?, warnings_json = ?, cancel_requested = 0
                WHERE job_id = ? AND state IN ('queued', 'running')
                """,
                (
                    final_state,
                    now,
                    retention_deadline,
                    outcome,
                    json.dumps(list(warnings)[:32], separators=(",", ":")),
                    job_id,
                ),
            )
            final = connection.execute(
                "SELECT * FROM crawl_jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            return self._record(connection, final)

    async def finish(
        self,
        job_id: str,
        state: CrawlJobState,
        outcome: str | None = None,
        warnings: tuple[str, ...] = (),
    ) -> CrawlJobRecord:
        return await asyncio.to_thread(self._finish, job_id, state, outcome, warnings)

    def _request_cancel(self, job_id: str, scope_hash: str) -> CrawlJobRecord:
        now = self.clock()
        record = None
        with self._transaction() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._expire_due(connection, now)
            row = connection.execute(
                "SELECT * FROM crawl_jobs WHERE job_id = ? AND scope_hash = ?",
                (job_id, scope_hash),
            ).fetchone()
            if row is None:
                pass
            elif row["state"] == JobOutcomeCode.EXPIRED.value:
                record = self._record(connection, row)
            elif row["state"] == JobOutcomeCode.QUEUED.value:
                connection.execute(
                    """
                    UPDATE crawl_jobs
                    SET state = 'cancelled', terminal_at = ?, retention_deadline = ?,
                        cancel_requested = 0, outcome = 'cancelled'
                    WHERE job_id = ? AND state = 'queued'
                    """,
                    (now, now + self.retention_s, job_id),
                )
            elif row["state"] == JobOutcomeCode.RUNNING.value:
                connection.execute(
                    "UPDATE crawl_jobs SET cancel_requested = 1 WHERE job_id = ?",
                    (job_id,),
                )
            if row is not None and record is None:
                final = connection.execute(
                    "SELECT * FROM crawl_jobs WHERE job_id = ?",
                    (job_id,),
                ).fetchone()
                record = self._record(connection, final)
        if record is None:
            raise CrawlJobNotFound(job_id)
        if record.state == JobOutcomeCode.EXPIRED.value:
            raise CrawlJobExpired(record)
        return record

    async def request_cancel(self, job_id: str, scope_hash: str) -> CrawlJobRecord:
        return await asyncio.to_thread(self._request_cancel, job_id, scope_hash)

    def _result_page(
        self,
        job_id: str,
        scope_hash: str,
        after_ordinal: int,
        max_items: int,
        max_bytes: int,
    ) -> tuple[CrawlJobRecord, StoredResultPage]:
        now = self.clock()
        record = None
        page = None
        oversized_item = None
        with self._transaction() as connection:
            self._expire_due(connection, now)
            row = connection.execute(
                "SELECT * FROM crawl_jobs WHERE job_id = ? AND scope_hash = ?",
                (job_id, scope_hash),
            ).fetchone()
            if row is not None:
                record = self._record(connection, row)
            if record is not None and record.state != JobOutcomeCode.EXPIRED.value:
                rows = connection.execute(
                    """
                    SELECT ordinal, result_json, item_bytes
                    FROM crawl_job_results
                    WHERE job_id = ? AND ordinal > ?
                    ORDER BY ordinal
                    LIMIT ?
                    """,
                    (job_id, after_ordinal, max_items + 1),
                ).fetchall()
                results: list[FetchResult] = []
                used = 0
                last_ordinal = after_ordinal
                has_more = False
                for result_row in rows:
                    if len(results) >= max_items:
                        has_more = True
                        break
                    item_bytes = result_row["item_bytes"]
                    if used + item_bytes > max_bytes:
                        if not results:
                            oversized_item = item_bytes
                        else:
                            has_more = True
                        break
                    results.append(FetchResult.model_validate_json(result_row["result_json"]))
                    used += item_bytes
                    last_ordinal = result_row["ordinal"]
                page = StoredResultPage(
                    results=tuple(results),
                    last_ordinal=last_ordinal,
                    returned_bytes=used,
                    has_more=has_more,
                )
        if record is None:
            raise CrawlJobNotFound(job_id)
        if record.state == JobOutcomeCode.EXPIRED.value:
            raise CrawlJobExpired(record)
        if oversized_item is not None:
            raise CrawlJobResultTooLarge(oversized_item)
        return record, page

    async def result_page(self, *args, **kwargs):
        return await asyncio.to_thread(self._result_page, *args, **kwargs)

    def _cleanup(self) -> int:
        now = self.clock()
        with self._transaction() as connection:
            before = connection.execute("SELECT COUNT(*) FROM crawl_jobs").fetchone()[0]
            self._expire_due(connection, now)
            after = connection.execute("SELECT COUNT(*) FROM crawl_jobs").fetchone()[0]
            return before - after

    async def cleanup(self) -> int:
        return await asyncio.to_thread(self._cleanup)
