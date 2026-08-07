import argparse
import asyncio
import hashlib
import json
import logging
import sqlite3
import time
from collections.abc import Callable, Coroutine
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from weakref import WeakValueDictionary

CACHE_SCHEMA_VERSION = 1
CACHE_IDENTITY_VERSION = "thorondor.page-cache.v1"
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PageCacheRecord:
    cache_key: str
    url_identity: str
    requested_url: str
    final_url: str | None
    outcome: str
    status_code: int | None
    content_type: str | None
    title: str | None
    retrieval_method: str | None
    capabilities: tuple[str, ...]
    markdown: str | None
    raw_html: str | None
    links: dict[str, object]
    metadata: dict[str, object]
    response_headers: dict[str, str]
    source_hash: str | None
    cleaner_version: str
    fetched_at: float
    expires_at: float
    retained_until: float


@dataclass(frozen=True)
class StoredTargetSnapshot:
    resolution: str
    text: str | None
    attributes: dict[str, str]
    updated_at: float
    baseline_text: str | None = None
    baseline_attributes: dict[str, str] | None = None
    baseline_updated_at: float | None = None


@dataclass(frozen=True)
class CacheRepositoryStats:
    entries: int
    target_snapshots: int
    bytes: int


def conservative_url_identity(url: str) -> str:
    parsed = urlsplit(url)
    scheme = parsed.scheme.casefold()
    host = (parsed.hostname or "").casefold()
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    port = parsed.port
    if port is not None and not (
        (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    ):
        host = f"{host}:{port}"
    path = parsed.path or "/"
    return urlunsplit((scheme, host, path, parsed.query, ""))


def has_url_credentials(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        return parsed.username is not None or parsed.password is not None
    except (TypeError, ValueError, UnicodeError):
        return True


def build_cache_key(
    url: str,
    capabilities: frozenset[str],
    retrieval_variant: str,
    cleaner_version: str,
) -> tuple[str, str]:
    if has_url_credentials(url):
        raise ValueError("credential-bearing URLs cannot be cached")
    identity = conservative_url_identity(url)
    payload = {
        "version": CACHE_IDENTITY_VERSION,
        "url": identity,
        "capabilities": sorted(capabilities),
        "retrieval_variant": retrieval_variant,
        "cleaner_version": cleaner_version,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest(), identity


def cache_bypass_reason(
    enabled: bool,
    capabilities: frozenset[str],
    raw_html_enabled: bool,
) -> str | None:
    if not enabled:
        return "disabled"
    if "raw_html" in capabilities and not raw_html_enabled:
        return "raw_html_persistence_disabled"
    if "pdf" in capabilities or "document" in capabilities:
        return "unsupported_persistent_capability"
    return None


def source_hash(markdown: str | None) -> str | None:
    if markdown is None:
        return None
    return hashlib.sha256(markdown.encode("utf-8")).hexdigest()


class DisabledPageCache:
    enabled = False

    async def get(self, _cache_key: str) -> PageCacheRecord | None:
        return None

    async def put(self, _record: PageCacheRecord) -> None:
        return None

    async def put_with_target(
        self,
        _record: PageCacheRecord,
        _locator_hash: str,
        _snapshot: StoredTargetSnapshot,
    ) -> None:
        return None

    async def get_target(
        self, _cache_key: str, _locator_hash: str
    ) -> StoredTargetSnapshot | None:
        return None

    async def put_target(
        self,
        _cache_key: str,
        _locator_hash: str,
        _snapshot: StoredTargetSnapshot,
    ) -> None:
        return None

    async def delete(self, _cache_key: str) -> None:
        return None

    async def clear_url(self, _url_identity: str) -> int:
        return 0

    async def cleanup(self, _now: float | None = None) -> int:
        return 0

    async def stats(self) -> CacheRepositoryStats:
        return CacheRepositoryStats(0, 0, 0)

    async def aclose(self) -> None:
        return None

    async def start(self) -> None:
        return None


class SqlitePageCache:
    enabled = True

    def __init__(self, path: str, clock: Callable[[], float] = time.time):
        self.path = Path(path)
        self._clock = clock
        self._write_count = 0
        self._retention_task: asyncio.Task | None = None
        self._retention_changed: asyncio.Event | None = None
        self._initialize_with_recovery()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    def _initialize_with_recovery(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._initialize()
        except sqlite3.DatabaseError as exc:
            message = str(exc).casefold()
            if not any(
                marker in message
                for marker in ("malformed", "not a database", "file is encrypted")
            ):
                raise
            if self.path.exists():
                suffix = time.strftime("%Y%m%d%H%M%S", time.gmtime())
                self.path.replace(self.path.with_suffix(f".corrupt-{suffix}"))
            self._initialize()

    def _initialize(self) -> None:
        with self._connect() as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version > CACHE_SCHEMA_VERSION:
                raise RuntimeError(
                    f"page cache schema {version} is newer than supported {CACHE_SCHEMA_VERSION}"
                )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS page_cache (
                    cache_key TEXT PRIMARY KEY,
                    url_identity TEXT NOT NULL,
                    record_json TEXT NOT NULL,
                    fetched_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    retained_until REAL NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS page_cache_url_identity ON page_cache(url_identity)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS page_cache_retention ON page_cache(retained_until)"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS target_snapshots (
                    cache_key TEXT NOT NULL,
                    locator_hash TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (cache_key, locator_hash),
                    FOREIGN KEY (cache_key) REFERENCES page_cache(cache_key) ON DELETE CASCADE
                )
                """
            )
            connection.execute(
                "DELETE FROM page_cache WHERE retained_until <= ?",
                (self._clock(),),
            )
            connection.execute(f"PRAGMA user_version = {CACHE_SCHEMA_VERSION}")

    @staticmethod
    def _record_from_json(value: str) -> PageCacheRecord:
        payload = json.loads(value)
        payload["capabilities"] = tuple(payload["capabilities"])
        return PageCacheRecord(**payload)

    def _get(self, cache_key: str) -> PageCacheRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT record_json, retained_until FROM page_cache WHERE cache_key = ?",
                (cache_key,),
            ).fetchone()
            if row is None:
                return None
            if row["retained_until"] <= self._clock():
                connection.execute("DELETE FROM page_cache WHERE cache_key = ?", (cache_key,))
                return None
            try:
                return self._record_from_json(row["record_json"])
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                connection.execute("DELETE FROM page_cache WHERE cache_key = ?", (cache_key,))
                return None

    async def get(self, cache_key: str) -> PageCacheRecord | None:
        return await asyncio.to_thread(self._get, cache_key)

    @staticmethod
    def _write_record(connection: sqlite3.Connection, record: PageCacheRecord) -> None:
        encoded = json.dumps(asdict(record), sort_keys=True, separators=(",", ":"))
        connection.execute(
            """
            INSERT INTO page_cache (
                cache_key, url_identity, record_json, fetched_at, expires_at, retained_until
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(cache_key) DO UPDATE SET
                url_identity = excluded.url_identity,
                record_json = excluded.record_json,
                fetched_at = excluded.fetched_at,
                expires_at = excluded.expires_at,
                retained_until = excluded.retained_until
            """,
            (
                record.cache_key,
                record.url_identity,
                encoded,
                record.fetched_at,
                record.expires_at,
                record.retained_until,
            ),
        )

    def _put(self, record: PageCacheRecord) -> None:
        with self._connect() as connection:
            self._write_record(connection, record)

    async def put(self, record: PageCacheRecord) -> None:
        await asyncio.to_thread(self._put, record)
        self._write_count += 1
        self._wake_retention_cleanup()

    def _put_with_target(
        self,
        record: PageCacheRecord,
        locator_hash: str,
        snapshot: StoredTargetSnapshot,
    ) -> None:
        with self._connect() as connection:
            self._write_record(connection, record)
            self._write_target(connection, record.cache_key, locator_hash, snapshot)

    async def put_with_target(
        self,
        record: PageCacheRecord,
        locator_hash: str,
        snapshot: StoredTargetSnapshot,
    ) -> None:
        await asyncio.to_thread(self._put_with_target, record, locator_hash, snapshot)
        self._write_count += 1
        self._wake_retention_cleanup()

    def _get_target(
        self, cache_key: str, locator_hash: str
    ) -> StoredTargetSnapshot | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT target_snapshots.snapshot_json, page_cache.retained_until
                FROM target_snapshots
                JOIN page_cache USING (cache_key)
                WHERE target_snapshots.cache_key = ? AND target_snapshots.locator_hash = ?
                """,
                (cache_key, locator_hash),
            ).fetchone()
            if row is None:
                return None
            if row["retained_until"] <= self._clock():
                connection.execute("DELETE FROM page_cache WHERE cache_key = ?", (cache_key,))
                return None
            try:
                return StoredTargetSnapshot(**json.loads(row["snapshot_json"]))
            except (TypeError, ValueError, json.JSONDecodeError):
                connection.execute(
                    "DELETE FROM target_snapshots WHERE cache_key = ? AND locator_hash = ?",
                    (cache_key, locator_hash),
                )
                return None

    async def get_target(
        self, cache_key: str, locator_hash: str
    ) -> StoredTargetSnapshot | None:
        return await asyncio.to_thread(self._get_target, cache_key, locator_hash)

    @staticmethod
    def _write_target(
        connection: sqlite3.Connection,
        cache_key: str,
        locator_hash: str,
        snapshot: StoredTargetSnapshot,
    ) -> None:
        encoded = json.dumps(asdict(snapshot), sort_keys=True, separators=(",", ":"))
        connection.execute(
            """
            INSERT INTO target_snapshots (
                cache_key, locator_hash, snapshot_json, updated_at
            ) VALUES (?, ?, ?, ?)
            ON CONFLICT(cache_key, locator_hash) DO UPDATE SET
                snapshot_json = excluded.snapshot_json,
                updated_at = excluded.updated_at
            """,
            (cache_key, locator_hash, encoded, snapshot.updated_at),
        )

    def _put_target(
        self,
        cache_key: str,
        locator_hash: str,
        snapshot: StoredTargetSnapshot,
    ) -> None:
        with self._connect() as connection:
            self._write_target(connection, cache_key, locator_hash, snapshot)

    async def put_target(
        self,
        cache_key: str,
        locator_hash: str,
        snapshot: StoredTargetSnapshot,
    ) -> None:
        await asyncio.to_thread(self._put_target, cache_key, locator_hash, snapshot)

    def _delete(self, cache_key: str) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM page_cache WHERE cache_key = ?", (cache_key,))

    async def delete(self, cache_key: str) -> None:
        await asyncio.to_thread(self._delete, cache_key)

    def _clear_url(self, url_identity: str) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM page_cache WHERE url_identity = ?", (url_identity,)
            )
            return cursor.rowcount

    async def clear_url(self, url_identity: str) -> int:
        return await asyncio.to_thread(self._clear_url, url_identity)

    def _cleanup(self, now: float) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM page_cache WHERE retained_until <= ?", (now,)
            )
            return cursor.rowcount

    async def cleanup(self, now: float | None = None) -> int:
        return await asyncio.to_thread(self._cleanup, self._clock() if now is None else now)

    def _stats(self) -> CacheRepositoryStats:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM page_cache WHERE retained_until <= ?",
                (self._clock(),),
            )
            entries = connection.execute("SELECT COUNT(*) FROM page_cache").fetchone()[0]
            targets = connection.execute("SELECT COUNT(*) FROM target_snapshots").fetchone()[0]
        size = self.path.stat().st_size if self.path.exists() else 0
        return CacheRepositoryStats(entries, targets, size)

    async def stats(self) -> CacheRepositoryStats:
        return await asyncio.to_thread(self._stats)

    def _next_retention(self) -> float | None:
        with self._connect() as connection:
            return connection.execute(
                "SELECT MIN(retained_until) FROM page_cache"
            ).fetchone()[0]

    def _wake_retention_cleanup(self) -> None:
        if self._retention_changed is not None:
            self._retention_changed.set()

    async def _retention_loop(self) -> None:
        while True:
            next_retention = await asyncio.to_thread(self._next_retention)
            event = self._retention_changed
            if event is None:
                return
            if next_retention is None:
                await event.wait()
                event.clear()
                continue
            delay = max(0.0, next_retention - self._clock())
            try:
                await asyncio.wait_for(event.wait(), timeout=delay)
            except TimeoutError:
                await self.cleanup()
            finally:
                event.clear()

    async def start(self) -> None:
        if self._retention_task is not None and not self._retention_task.done():
            return
        self._retention_changed = asyncio.Event()
        self._retention_task = asyncio.create_task(
            self._retention_loop(),
            name="page-cache-retention",
        )

    async def aclose(self) -> None:
        task = self._retention_task
        if task is None:
            return
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        self._retention_task = None
        self._retention_changed = None


class PageRefreshCoordinator:
    def __init__(self):
        self._locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()
        self._background: dict[str, asyncio.Task] = {}

    def lock_for(self, cache_key: str) -> asyncio.Lock:
        lock = self._locks.get(cache_key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[cache_key] = lock
        return lock

    def start_background(
        self,
        cache_key: str,
        operation_factory: Callable[[], Coroutine[object, object, object]],
    ) -> bool:
        existing = self._background.get(cache_key)
        if existing is not None and not existing.done():
            return False
        task = asyncio.create_task(operation_factory())
        self._background[cache_key] = task

        def finished(done: asyncio.Task) -> None:
            self._background.pop(cache_key, None)
            if not done.cancelled():
                error = done.exception()
                if error is not None:
                    logger.error(
                        "Background page refresh failed",
                        exc_info=(type(error), error, error.__traceback__),
                    )

        task.add_done_callback(finished)
        return True

    async def aclose(self) -> None:
        tasks = list(self._background.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._background.clear()


def watch_fingerprint(watch: dict[str, object]) -> str:
    encoded = json.dumps(watch, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _run_cli() -> None:
    parser = argparse.ArgumentParser(prog="python -m orchestrator.page_cache")
    parser.add_argument("--path", required=True)
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("stats")
    clear = subcommands.add_parser("clear-url")
    clear.add_argument("url")
    subcommands.add_parser("cleanup")
    args = parser.parse_args()
    cache = SqlitePageCache(args.path)
    if args.command == "stats":
        print(json.dumps(asdict(asyncio.run(cache.stats())), sort_keys=True))
    elif args.command == "clear-url":
        count = asyncio.run(cache.clear_url(conservative_url_identity(args.url)))
        print(json.dumps({"deleted": count}, sort_keys=True))
    else:
        count = asyncio.run(cache.cleanup())
        print(json.dumps({"deleted": count}, sort_keys=True))


if __name__ == "__main__":
    _run_cli()
