from collections import deque
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from pathlib import PurePosixPath
from typing import Literal
from urllib.parse import urldefrag, urljoin, urlsplit, urlunsplit

from .models import MAX_SITE_URL_BYTES
from .normalize import canonical_host
from .url_identity import dedup_key_for

CrawlSource = Literal["seed", "sitemap", "link", "search"]
CrawlState = Literal[
    "discovered",
    "admitted",
    "queued",
    "fetched",
    "filtered",
    "failed",
    "cancelled",
]
QueryPolicy = Literal["preserve", "strip", "exclude"]


def _within_url_limit(value: str) -> bool:
    try:
        return (
            len(value) <= MAX_SITE_URL_BYTES
            and len(value.encode("utf-8")) <= MAX_SITE_URL_BYTES
        )
    except UnicodeError:
        return False


def _remove_dot_segments(path: str) -> str:
    segments: list[str] = []
    trailing_slash = path.endswith(("/", "/.", "/.."))
    for segment in path.split("/"):
        dot_value = segment.replace("%2e", ".").replace("%2E", ".")
        if dot_value == ".":
            continue
        if dot_value == "..":
            if segments and segments[-1]:
                segments.pop()
            continue
        segments.append(segment)
    normalized = "/".join(segments) or "/"
    if path.startswith("/") and not normalized.startswith("/"):
        normalized = f"/{normalized}"
    if trailing_slash and not normalized.endswith("/"):
        normalized = f"{normalized}/"
    return normalized


def _origin_parts(url: str) -> tuple[str, str, int | None]:
    parsed = urlsplit(url)
    scheme = parsed.scheme.casefold()
    port = parsed.port
    if port is None:
        port = 80 if scheme == "http" else 443 if scheme == "https" else None
    return scheme, canonical_host(parsed.hostname), port


def _seed_path_prefix(url: str) -> str:
    path = urlsplit(url).path or "/"
    if path == "/" or path.endswith("/"):
        return path
    parent = str(PurePosixPath(path).parent)
    return f"{parent.rstrip('/')}/" if parent != "/" else "/"


@dataclass(frozen=True)
class CrawlPolicy:
    effective_url: str
    max_depth: int
    max_discovered_urls: int
    include_parent_paths: bool
    include_subdomains: bool
    include_paths: tuple[str, ...]
    exclude_paths: tuple[str, ...]
    query_policy: QueryPolicy
    allowed_file_extensions: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.max_depth < 0:
            raise ValueError("max_depth must be >= 0")
        if self.max_discovered_urls < 1:
            raise ValueError("max_discovered_urls must be >= 1")
        if self.query_policy not in {"preserve", "strip", "exclude"}:
            raise ValueError("unsupported query policy")

    def normalize(self, candidate: str) -> str | None:
        try:
            joined = urldefrag(urljoin(self.effective_url, candidate)).url
            parsed = urlsplit(joined)
            if parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname:
                return None
            if parsed.username is not None or parsed.password is not None:
                return None
            query = "" if self.query_policy == "strip" else parsed.query
            normalized = urlunsplit(
                (
                    parsed.scheme.casefold(),
                    parsed.netloc,
                    _remove_dot_segments(parsed.path or "/"),
                    query,
                    "",
                )
            )
            if not _within_url_limit(normalized):
                return None
            dedup_key_for(normalized)
            return normalized
        except (TypeError, ValueError, UnicodeError):
            return None

    def rejection_reason(self, url: str, depth: int) -> str | None:
        if depth > self.max_depth:
            return "depth_exceeded"
        try:
            candidate = urlsplit(url)
            seed_scheme, seed_host, seed_port = _origin_parts(self.effective_url)
            candidate_scheme, candidate_host, candidate_port = _origin_parts(url)
        except (TypeError, ValueError, UnicodeError):
            return "invalid_url"
        same_origin = (
            candidate_scheme,
            candidate_host,
            candidate_port,
        ) == (seed_scheme, seed_host, seed_port)
        accepted_subdomain = (
            self.include_subdomains
            and candidate_scheme == seed_scheme
            and candidate_port == seed_port
            and candidate_host.endswith(f".{seed_host}")
        )
        if not same_origin and not accepted_subdomain:
            return "outside_origin"
        path = candidate.path or "/"
        if not self.include_parent_paths:
            prefix = _seed_path_prefix(self.effective_url)
            seed_path = urlsplit(self.effective_url).path or "/"
            if path != seed_path and not path.startswith(prefix):
                return "outside_seed_path"
        if self.query_policy == "exclude" and candidate.query:
            return "query_excluded"
        if any(fnmatchcase(path, pattern) for pattern in self.exclude_paths):
            return "excluded_path"
        if self.include_paths and not any(
            fnmatchcase(path, pattern) for pattern in self.include_paths
        ):
            return "outside_included_paths"
        suffix = PurePosixPath(path).suffix.casefold()
        allowed = {value.casefold() for value in self.allowed_file_extensions}
        if suffix not in allowed:
            return "unsupported_file_type"
        return None


@dataclass
class FrontierRecord:
    url: str
    depth: int
    sources: list[CrawlSource]
    states: list[CrawlState] = field(default_factory=lambda: ["discovered"])
    reason: str | None = None
    modified_at: str | None = None
    priority: float | None = None
    order: int = 0


class CrawlFrontier:
    def __init__(self, policy: CrawlPolicy):
        self.policy = policy
        self.records: list[FrontierRecord] = []
        self.omitted_due_to_limit = 0
        self.non_http_urls_skipped = 0
        self._by_key: dict[str, FrontierRecord] = {}
        self._queue: deque[FrontierRecord] = deque()

    def is_known(self, url: str) -> bool:
        return str(dedup_key_for(url)) in self._by_key

    @property
    def is_full(self) -> bool:
        return len(self.records) >= self.policy.max_discovered_urls

    def discover(
        self,
        candidate: str,
        *,
        source: CrawlSource,
        depth: int,
        safe: bool,
        robots_allowed: bool,
        modified_at: str | None = None,
        priority: float | None = None,
    ) -> FrontierRecord | None:
        normalized = self.policy.normalize(candidate)
        if normalized is None:
            return self._record_filtered(
                candidate,
                source,
                depth,
                "invalid_url",
                modified_at,
                priority,
            )
        key = str(dedup_key_for(normalized))
        existing = self._by_key.get(key)
        if existing is not None:
            if source not in existing.sources:
                existing.sources.append(source)
            if existing.modified_at is None and modified_at is not None:
                existing.modified_at = modified_at
            if existing.priority is None and priority is not None:
                existing.priority = priority
            existing.depth = min(existing.depth, depth)
            return existing
        if len(self.records) >= self.policy.max_discovered_urls:
            self.omitted_due_to_limit += 1
            return None
        record = FrontierRecord(
            url=normalized,
            depth=depth,
            sources=[source],
            modified_at=modified_at,
            priority=priority,
            order=len(self.records),
        )
        self.records.append(record)
        self._by_key[key] = record
        reason = self.policy.rejection_reason(normalized, depth)
        if reason is None and not safe:
            reason = "unsafe_target"
        if reason is None and not robots_allowed:
            reason = "robots_refused"
        if reason is not None:
            record.reason = reason
            record.states.append("filtered")
            return record
        record.states.extend(("admitted", "queued"))
        self._queue.append(record)
        return record

    def _record_filtered(
        self,
        url: str,
        source: CrawlSource,
        depth: int,
        reason: str,
        modified_at: str | None,
        priority: float | None,
    ) -> FrontierRecord | None:
        if not _within_url_limit(url) or len(self.records) >= self.policy.max_discovered_urls:
            self.omitted_due_to_limit += 1
            return None
        record = FrontierRecord(
            url=url,
            depth=depth,
            sources=[source],
            states=["discovered", "filtered"],
            reason=reason,
            modified_at=modified_at,
            priority=priority,
            order=len(self.records),
        )
        self.records.append(record)
        return record

    def pop(self) -> FrontierRecord | None:
        return self._queue.popleft() if self._queue else None

    def reconcile_final(
        self,
        record: FrontierRecord,
        final_url: str,
        *,
        safe: bool,
        robots_allowed: bool,
    ) -> FrontierRecord | None:
        normalized = self.policy.normalize(final_url)
        reason = (
            self.policy.rejection_reason(normalized, record.depth)
            if normalized
            else "invalid_url"
        )
        if reason is None and not safe:
            reason = "unsafe_redirect"
        if reason is None and not robots_allowed:
            reason = "robots_refused"
        if reason is not None:
            self.mark_failed(record, reason)
            return None
        key = str(dedup_key_for(normalized))
        existing = self._by_key.get(key)
        if existing is not None and existing is not record:
            for source in record.sources:
                if source not in existing.sources:
                    existing.sources.append(source)
            existing.depth = min(existing.depth, record.depth)
            self.mark_failed(record, "duplicate_final_url")
            if existing.states[-1] == "queued":
                self.mark_fetched(existing)
                return existing
            return None
        old_key = str(dedup_key_for(record.url))
        if self._by_key.get(old_key) is record:
            self._by_key.pop(old_key)
        record.url = normalized
        self._by_key[key] = record
        return record

    def mark_fetched(self, record: FrontierRecord) -> None:
        if record.states[-1] not in {"fetched", "filtered", "failed", "cancelled"}:
            record.states.append("fetched")

    def mark_failed(self, record: FrontierRecord, reason: str) -> None:
        if record.states[-1] not in {"fetched", "filtered", "failed", "cancelled"}:
            record.reason = reason
            record.states.append("failed")

    def mark_cancelled(self, record: FrontierRecord, reason: str) -> None:
        if record.states[-1] not in {"fetched", "filtered", "failed", "cancelled"}:
            record.reason = reason
            record.states.append("cancelled")

    def cancel_queued(self) -> None:
        for record in self._queue:
            if record.states[-1] == "queued":
                record.reason = "cancelled"
                record.states.append("cancelled")
        self._queue.clear()
