import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass

from thorondor_contracts import (
    DEFAULT_ADMISSION_RETRY_AFTER_S,
    DEFAULT_ADMISSION_WAIT_S,
    DEFAULT_CHUNK_CONCURRENCY,
    DEFAULT_CHUNK_STAGE_DEADLINE_S,
    DEFAULT_CRAWL_STAGE_DEADLINE_S,
    DEFAULT_DISCOVERY_STAGE_DEADLINE_S,
    DEFAULT_FETCH_ROUTE_DEADLINE_S,
    DEFAULT_MAP_ROUTE_DEADLINE_S,
    DEFAULT_MAX_CONTENT_BYTES,
    DEFAULT_MAX_INFLIGHT_CRAWLS,
    DEFAULT_MAX_INFLIGHT_FETCHES,
    DEFAULT_MAX_INFLIGHT_MAPS,
    DEFAULT_MAX_INFLIGHT_SEARCHES,
    DEFAULT_MAX_INTERNAL_FANOUT,
    DEFAULT_MAX_REQUEST_BODY_BYTES,
    DEFAULT_MAX_RESPONSE_BODY_BYTES,
    DEFAULT_RERANK_STAGE_DEADLINE_S,
    DEFAULT_SEARCH_ROUTE_DEADLINE_S,
    DEFAULT_SITE_CRAWL_ROUTE_DEADLINE_S,
)


class CapacityUnavailable(Exception):
    def __init__(self, route: str, retry_after_s: int):
        super().__init__(route)
        self.route = route
        self.retry_after_s = retry_after_s


class RouteDeadlineExceeded(Exception):
    reason = "deadline_cancelled"

    def __init__(self, route: str):
        super().__init__(route)
        self.route = route


@dataclass(frozen=True)
class ResourcePolicy:
    max_request_body_bytes: int = DEFAULT_MAX_REQUEST_BODY_BYTES
    max_response_body_bytes: int = DEFAULT_MAX_RESPONSE_BODY_BYTES
    search_route_deadline_s: float = DEFAULT_SEARCH_ROUTE_DEADLINE_S
    fetch_route_deadline_s: float = DEFAULT_FETCH_ROUTE_DEADLINE_S
    map_route_deadline_s: float = DEFAULT_MAP_ROUTE_DEADLINE_S
    site_crawl_route_deadline_s: float = DEFAULT_SITE_CRAWL_ROUTE_DEADLINE_S
    discovery_stage_deadline_s: float = DEFAULT_DISCOVERY_STAGE_DEADLINE_S
    crawl_stage_deadline_s: float = DEFAULT_CRAWL_STAGE_DEADLINE_S
    chunk_stage_deadline_s: float = DEFAULT_CHUNK_STAGE_DEADLINE_S
    rerank_stage_deadline_s: float = DEFAULT_RERANK_STAGE_DEADLINE_S
    max_inflight_searches: int = DEFAULT_MAX_INFLIGHT_SEARCHES
    max_inflight_fetches: int = DEFAULT_MAX_INFLIGHT_FETCHES
    max_inflight_maps: int = DEFAULT_MAX_INFLIGHT_MAPS
    max_inflight_crawls: int = DEFAULT_MAX_INFLIGHT_CRAWLS
    admission_wait_s: float = DEFAULT_ADMISSION_WAIT_S
    admission_retry_after_s: int = DEFAULT_ADMISSION_RETRY_AFTER_S
    max_internal_fanout: int = DEFAULT_MAX_INTERNAL_FANOUT
    max_content_bytes: int = DEFAULT_MAX_CONTENT_BYTES
    chunk_concurrency: int = DEFAULT_CHUNK_CONCURRENCY

    def __post_init__(self) -> None:
        integer_fields = (
            "max_request_body_bytes",
            "max_response_body_bytes",
            "max_inflight_searches",
            "max_inflight_fetches",
            "max_inflight_maps",
            "max_inflight_crawls",
            "admission_retry_after_s",
            "max_internal_fanout",
            "max_content_bytes",
            "chunk_concurrency",
        )
        for name in integer_fields:
            if getattr(self, name) < 1:
                raise ValueError(f"{name} must be >= 1")
        deadline_fields = (
            "search_route_deadline_s",
            "fetch_route_deadline_s",
            "map_route_deadline_s",
            "site_crawl_route_deadline_s",
            "discovery_stage_deadline_s",
            "crawl_stage_deadline_s",
            "chunk_stage_deadline_s",
            "rerank_stage_deadline_s",
        )
        for name in deadline_fields:
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be > 0")
        if self.admission_wait_s < 0:
            raise ValueError("admission_wait_s must be >= 0")
        if self.max_content_bytes > self.max_response_body_bytes:
            raise ValueError("max_content_bytes must not exceed max_response_body_bytes")


class _AdmissionPool:
    def __init__(self, route: str, limit: int, wait_s: float, retry_after_s: int):
        self.route = route
        self._semaphore = asyncio.Semaphore(limit)
        self._wait_s = wait_s
        self._retry_after_s = retry_after_s
        self.active = 0

    @asynccontextmanager
    async def slot(self):
        acquired = False
        try:
            if self._wait_s == 0:
                if self._semaphore.locked():
                    raise CapacityUnavailable(self.route, self._retry_after_s)
                await self._semaphore.acquire()
            else:
                try:
                    await asyncio.wait_for(
                        self._semaphore.acquire(),
                        timeout=self._wait_s,
                    )
                except TimeoutError as exc:
                    raise CapacityUnavailable(self.route, self._retry_after_s) from exc
            acquired = True
            self.active += 1
            yield
        finally:
            if acquired:
                self.active -= 1
                self._semaphore.release()


class RuntimeAdmission:
    def __init__(self, policy: ResourcePolicy):
        self.policy = policy
        self._searches = _AdmissionPool(
            "search",
            policy.max_inflight_searches,
            policy.admission_wait_s,
            policy.admission_retry_after_s,
        )
        self._fetches = _AdmissionPool(
            "fetch",
            policy.max_inflight_fetches,
            policy.admission_wait_s,
            policy.admission_retry_after_s,
        )
        self._maps = _AdmissionPool(
            "map",
            policy.max_inflight_maps,
            policy.admission_wait_s,
            policy.admission_retry_after_s,
        )
        self._crawls = _AdmissionPool(
            "crawl",
            policy.max_inflight_crawls,
            policy.admission_wait_s,
            policy.admission_retry_after_s,
        )

    @property
    def active_searches(self) -> int:
        return self._searches.active

    @property
    def active_fetches(self) -> int:
        return self._fetches.active

    @property
    def active_maps(self) -> int:
        return self._maps.active

    @property
    def active_crawls(self) -> int:
        return self._crawls.active

    def search_slot(self):
        return self._searches.slot()

    def fetch_slot(self):
        return self._fetches.slot()

    def map_slot(self):
        return self._maps.slot()

    def crawl_slot(self):
        return self._crawls.slot()
