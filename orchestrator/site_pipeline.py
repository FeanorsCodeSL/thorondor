import asyncio
import time
from collections import Counter, deque
from collections.abc import Awaitable, Callable
from dataclasses import replace
from inspect import isawaitable
from typing import cast
from urllib.parse import urlsplit

from .crawl_policy import CrawlFrontier, CrawlPolicy, FrontierRecord
from .fetch_pipeline import wire_fetch_result_with_schema
from .models import (
    CrawlRequest,
    CrawlResponse,
    FetchOutcomeCount,
    FetchResult,
    MapRequest,
    MapResponse,
    SiteSourceCount,
    SiteStats,
    SiteUrlRecord,
)
from .normalize import canonical_host
from .outcome_codes import FetchOutcomeCode
from .politeness import parse_retry_after
from .resource_policy import RouteDeadlineExceeded
from .robots_policy import RobotsSnapshot, robots_body
from .sitemap_policy import SitemapDocument, parse_sitemap
from .types import FetchStageOutcome

MAX_SITEMAP_DEPTH = 3
MAP_CAPABILITIES = frozenset({"markdown", "javascript", "links", "metadata", "raw_html"})


class CrawlOperationCancelled(Exception):
    pass


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _origin(url: str) -> str | None:
    try:
        parsed = urlsplit(url)
        host = canonical_host(parsed.hostname)
        if parsed.scheme.casefold() not in {"http", "https"} or not host:
            return None
        port = parsed.port
    except (TypeError, ValueError):
        return None
    include_port = port is not None and not (
        (parsed.scheme.casefold() == "http" and port == 80)
        or (parsed.scheme.casefold() == "https" and port == 443)
    )
    suffix = f":{port}" if include_port else ""
    return f"{parsed.scheme.casefold()}://{host}{suffix}"


def _body(outcome: FetchStageOutcome) -> str:
    if outcome.page is None:
        return ""
    return robots_body(
        outcome.page.markdown,
        outcome.page.raw_html or outcome.page.html,
        outcome.content_type or outcome.page.content_type,
    )


def _links(outcome: FetchStageOutcome) -> list[str]:
    found: list[str] = []
    for category in sorted(outcome.links):
        values = outcome.links.get(category)
        if not isinstance(values, list):
            continue
        for item in values:
            href = item.get("href") if isinstance(item, dict) else None
            if isinstance(href, str) and href and href not in found:
                found.append(href)
    return found


def _site_reason(code: FetchOutcomeCode, *, seed: bool = False) -> str:
    if seed and code == FetchOutcomeCode.UNSAFE_TARGET:
        return "unsafe_seed"
    mapping = {
        FetchOutcomeCode.UNSAFE_TARGET: "unsafe_target",
        FetchOutcomeCode.UNSAFE_REDIRECT: "unsafe_redirect",
        FetchOutcomeCode.ROBOTS_REFUSED: "robots_refused",
        FetchOutcomeCode.UPSTREAM_TIMEOUT: "upstream_timeout",
        FetchOutcomeCode.DEADLINE_CANCELLED: "deadline_cancelled",
        FetchOutcomeCode.MALFORMED_UPSTREAM_RESPONSE: "malformed_upstream_response",
        FetchOutcomeCode.RATE_LIMITED: "rate_limited",
        FetchOutcomeCode.LOCAL_PROCESSING_FAILURE: "local_processing_failure",
    }
    return mapping.get(code, "upstream_failure")


class _SiteOperation:
    def __init__(
        self,
        request: MapRequest | CrawlRequest,
        deps,
        *,
        return_content: bool,
        on_result: Callable[[FetchResult], Awaitable[None]] | None = None,
        on_failure: Callable[[str, str], Awaitable[None]] | None = None,
        cancel_requested: Callable[[], bool] | None = None,
    ):
        self.request = request
        self.deps = deps
        self.return_content = return_content
        self.on_result = on_result
        self.on_failure = on_failure
        self.cancel_requested = cancel_requested
        self.started = time.perf_counter()
        self.requested_origin = _origin(request.url)
        self.effective_url: str | None = None
        self.effective_origin: str | None = None
        self.frontier: CrawlFrontier | None = None
        self.results: list[FetchResult] = []
        self.page_outcomes: list[FetchStageOutcome] = []
        self.warnings: list[str] = []
        self.robots: dict[str, RobotsSnapshot] = {}
        self.robots_sources: dict[str, str] = {}
        self.robots_documents_attempted = 0
        self.sitemap_candidates_examined = 0
        self.sitemap_documents_attempted = 0
        self.sitemap_documents = 0
        self.sitemap_entries = 0
        self.sitemap_truncated = 0
        self.page_limit_reached = False
        self.url_records_omitted = 0
        self.results_omitted = 0
        self.max_pages = min(
            request.max_pages,
            deps.resource_policy.max_internal_fanout,
        )
        if self.max_pages < request.max_pages:
            self._warning("max_pages_clamped_to_internal_fanout")

    def _warning(self, value: str) -> None:
        if value not in self.warnings and len(self.warnings) < 32:
            self.warnings.append(value)

    def _check_cancelled(self) -> None:
        if self.cancel_requested is not None and self.cancel_requested():
            raise CrawlOperationCancelled

    async def _emit_result(self, result: FetchResult) -> None:
        if self.on_result is not None:
            await self.on_result(result)

    async def _emit_failure(self, url: str, reason: str) -> None:
        if self.on_failure is not None:
            await self.on_failure(url, reason)

    async def _is_safe(self, url: str) -> bool:
        try:
            result = self.deps.crawl_url_safety(url)
            if isawaitable(result):
                result = await result
            return result is True
        except Exception:
            return False

    async def _fetch_target(
        self,
        url: str,
        capabilities: frozenset[str],
        robots: RobotsSnapshot | None = None,
    ) -> FetchStageOutcome:
        self._check_cancelled()
        host = canonical_host(urlsplit(url).hostname)
        await self.deps.site_politeness.wait(
            host,
            robots_delay_s=robots.crawl_delay_s if robots else None,
        )
        try:
            outcomes = await self.deps.extractor.fetch(
                [url],
                capabilities,
                "raw_html" in capabilities
                or any(
                    format_name != "json_schema"
                    for format_name in getattr(self.request, "structured_formats", ())
                ),
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            outcomes = []
        outcome = outcomes[0] if outcomes else FetchStageOutcome(
            requested_url=url,
            final_url=None,
            code=FetchOutcomeCode.UPSTREAM_FAILURE,
            retrieval_method="crawl4ai_browser",
            elapsed_ms=0,
        )
        retry_after = parse_retry_after(
            outcome.retry_after,
            max_delay_s=self.deps.site_max_cooldown_s,
        )
        self.deps.site_politeness.record(
            host,
            status_code=outcome.status_code,
            retry_after_s=retry_after,
        )
        if outcome.final_url and not await self._is_safe(outcome.final_url):
            return cast(
                FetchStageOutcome,
                replace(
                    outcome,
                    code=FetchOutcomeCode.UNSAFE_REDIRECT,
                    page=None,
                ),
            )
        return outcome

    async def _robots_for(self, url: str) -> RobotsSnapshot:
        origin = _origin(url)
        if origin is None:
            return RobotsSnapshot(
                "",
                self.deps.crawler_robots_user_agent,
                "unreachable",
                (),
                (),
                None,
                time.time(),
                "invalid_origin",
            )
        if origin in self.robots:
            return self.robots[origin]
        now = time.time()
        cached = self.deps.robots_cache.get(origin, now=now)
        if cached is not None:
            self.robots[origin] = cached
            self.robots_sources[origin] = "cache"
            return cached
        if self.robots_documents_attempted >= self.deps.resource_policy.max_internal_fanout:
            snapshot = RobotsSnapshot(
                origin,
                self.deps.crawler_robots_user_agent,
                "unreachable",
                (),
                (),
                None,
                now,
                "document_limit_reached",
            )
            self.robots[origin] = snapshot
            self.robots_sources[origin] = "network"
            self._warning("robots_document_limit_reached")
            return snapshot
        self.robots_documents_attempted += 1
        robots_url = f"{origin}/robots.txt"
        if not await self._is_safe(robots_url):
            snapshot = RobotsSnapshot(
                origin,
                self.deps.crawler_robots_user_agent,
                "unreachable",
                (),
                (),
                None,
                now,
                "unsafe_target",
            )
        else:
            outcome = await self._fetch_target(robots_url, MAP_CAPABILITIES)
            status_code = outcome.status_code
            if outcome.code == FetchOutcomeCode.CONTENT and status_code is None:
                status_code = 200
            snapshot = RobotsSnapshot.from_http(
                origin=origin,
                user_agent=self.deps.crawler_robots_user_agent,
                status_code=status_code,
                body=_body(outcome),
                fetched_at=now,
                max_bytes=self.deps.max_robots_bytes,
            )
        self.robots[origin] = snapshot
        self.robots_sources[origin] = "network"
        self.deps.robots_cache.put(snapshot)
        if snapshot.state != "available":
            self._warning(f"robots_{snapshot.state}:{origin}")
        return snapshot

    async def _admit(
        self,
        candidate: str,
        *,
        source: str,
        depth: int,
        modified_at: str | None = None,
        priority: float | None = None,
    ) -> FrontierRecord | None:
        try:
            scheme = urlsplit(candidate).scheme.casefold()
        except (TypeError, ValueError):
            scheme = ""
        if scheme and scheme not in {"http", "https"}:
            self.frontier.non_http_urls_skipped += 1
            return None
        normalized = self.frontier.policy.normalize(candidate)
        if normalized is None:
            return self.frontier.discover(
                candidate,
                source=source,
                depth=depth,
                safe=False,
                robots_allowed=False,
                modified_at=modified_at,
                priority=priority,
            )
        if self.frontier.policy.rejection_reason(normalized, depth) is not None:
            return self.frontier.discover(
                normalized,
                source=source,
                depth=depth,
                safe=True,
                robots_allowed=True,
                modified_at=modified_at,
                priority=priority,
            )
        if self.frontier.is_known(normalized) or self.frontier.is_full:
            return self.frontier.discover(
                normalized,
                source=source,
                depth=depth,
                safe=True,
                robots_allowed=True,
                modified_at=modified_at,
                priority=priority,
            )
        safe = await self._is_safe(normalized)
        robots_allowed = False
        if safe:
            robots_allowed = (await self._robots_for(normalized)).allows(normalized)
        return self.frontier.discover(
            normalized,
            source=source,
            depth=depth,
            safe=safe,
            robots_allowed=robots_allowed,
            modified_at=modified_at,
            priority=priority,
        )

    def _sitemap_origin_allowed(self, url: str) -> bool:
        try:
            seed_origin = _origin(self.effective_url)
            candidate_origin = _origin(url)
            seed_host = canonical_host(urlsplit(self.effective_url).hostname)
            candidate_host = canonical_host(urlsplit(url).hostname)
        except (TypeError, ValueError):
            return False
        return candidate_origin == seed_origin or (
            self.request.include_subdomains
            and bool(seed_host)
            and candidate_host.endswith(f".{seed_host}")
        )

    def _sitemap_roots(self, snapshot: RobotsSnapshot) -> list[str]:
        roots = list(snapshot.sitemaps)
        for path in ("/sitemap.xml", "/sitemap_index.xml"):
            candidate = f"{self.effective_origin}{path}"
            if candidate not in roots:
                roots.append(candidate)
        return roots

    def _next_sitemap_candidate(
        self,
        sitemap_url: str,
        depth: int,
        visited: set[str],
    ) -> str | None:
        normalized = self.frontier.policy.normalize(sitemap_url)
        if normalized is None or normalized in visited or depth > MAX_SITEMAP_DEPTH:
            return None
        visited.add(normalized)
        self.sitemap_candidates_examined += 1
        return normalized

    async def _fetch_sitemap_document(
        self,
        normalized: str,
    ) -> SitemapDocument | None:
        if not self._sitemap_origin_allowed(normalized):
            self._warning("sitemap_outside_scope")
            return None
        if not await self._is_safe(normalized):
            self._warning("sitemap_unsafe_target")
            return None
        robots = await self._robots_for(normalized)
        if not robots.allows(normalized):
            self._warning("sitemap_robots_refused")
            return None
        self.sitemap_documents_attempted += 1
        outcome = await self._fetch_target(normalized, MAP_CAPABILITIES, robots)
        if outcome.code != FetchOutcomeCode.CONTENT:
            self._warning(f"sitemap_{outcome.code.value}")
            return None
        sitemap_final_url = outcome.final_url or normalized
        if not self._sitemap_origin_allowed(sitemap_final_url):
            self._warning("sitemap_redirect_outside_scope")
            return None
        final_robots = await self._robots_for(sitemap_final_url)
        if not final_robots.allows(sitemap_final_url):
            self._warning("sitemap_redirect_robots_refused")
            return None
        document = parse_sitemap(
            _body(outcome),
            max_bytes=self.deps.max_sitemap_bytes,
            max_entries=self.deps.max_sitemap_entries,
        )
        if document.reason is not None:
            self._warning(f"sitemap_{document.reason}")
            return None
        return document

    async def _queue_sitemap_entries(
        self,
        document: SitemapDocument,
        depth: int,
        queue: deque[tuple[str, int]],
        visited: set[str],
    ) -> None:
        self.sitemap_documents += 1
        self.sitemap_entries += len(document.entries)
        if document.truncated:
            self.sitemap_truncated += 1
        if document.kind == "index":
            for entry in document.entries:
                if entry.url not in visited:
                    queue.append((entry.url, depth + 1))
            return
        for entry in document.entries:
            await self._admit(
                entry.url,
                source="sitemap",
                depth=0,
                modified_at=entry.modified_at,
                priority=entry.priority,
            )

    def _finish_sitemap_loading(self, queue: deque[tuple[str, int]]) -> None:
        if queue and self.sitemap_documents_attempted >= self.deps.max_sitemap_documents:
            self._warning("sitemap_document_limit_reached")
        elif queue:
            self._warning("sitemap_candidate_limit_reached")

    async def _load_sitemaps(self, snapshot: RobotsSnapshot) -> None:
        queue = deque((url, 0) for url in self._sitemap_roots(snapshot))
        visited: set[str] = set()
        while (
            queue
            and self.sitemap_documents_attempted < self.deps.max_sitemap_documents
            and self.sitemap_candidates_examined
            < self.deps.resource_policy.max_internal_fanout
        ):
            self._check_cancelled()
            sitemap_url, depth = queue.popleft()
            normalized = self._next_sitemap_candidate(sitemap_url, depth, visited)
            if normalized is None:
                continue
            document = await self._fetch_sitemap_document(normalized)
            if document is None:
                continue
            await self._queue_sitemap_entries(document, depth, queue, visited)
        self._finish_sitemap_loading(queue)

    async def _augment_search(self) -> None:
        self._check_cancelled()
        host = canonical_host(urlsplit(self.effective_url).hostname)
        try:
            async with asyncio.timeout(self.deps.resource_policy.discovery_stage_deadline_s):
                outcome = await self.deps.discovery.search(f"site:{host}", None)
        except Exception:
            self._warning("search_augmentation_failed")
            return
        if outcome.unresponsive_engines:
            self._warning("search_augmentation_degraded")
        for result in outcome.results:
            await self._admit(result.url, source="search", depth=0)

    async def _record_seed_failure(self, outcome: FetchStageOutcome) -> str:
        effective = outcome.final_url or self.request.url
        self.effective_url = effective
        self.effective_origin = _origin(effective)
        self.frontier = CrawlFrontier(self._policy(effective))
        record = self.frontier.discover(
            effective,
            source="seed",
            depth=0,
            safe=True,
            robots_allowed=True,
        )
        if record is not None and record.states[-1] == "queued":
            self.frontier.pop()
            self.frontier.mark_failed(record, outcome.code.value)
        self.page_outcomes.append(outcome)
        await self._emit_failure(self.request.url, outcome.code.value)
        return _site_reason(outcome.code, seed=True)

    async def _reconcile_final(
        self,
        record: FrontierRecord,
        outcome: FetchStageOutcome,
    ) -> FrontierRecord | None:
        final_url = outcome.final_url or record.url
        normalized = self.frontier.policy.normalize(final_url)
        if normalized is None or self.frontier.policy.rejection_reason(
            normalized,
            record.depth,
        ) is not None:
            return self.frontier.reconcile_final(
                record,
                final_url,
                safe=True,
                robots_allowed=True,
            )
        safe = await self._is_safe(final_url)
        robots_allowed = False
        if safe:
            robots_allowed = (await self._robots_for(final_url)).allows(final_url)
        return self.frontier.reconcile_final(
            record,
            final_url,
            safe=safe,
            robots_allowed=robots_allowed,
        )

    def _policy(self, effective_url: str) -> CrawlPolicy:
        return CrawlPolicy(
            effective_url=effective_url,
            max_depth=self.request.max_depth,
            max_discovered_urls=self.request.max_discovered_urls,
            include_parent_paths=self.request.include_parent_paths,
            include_subdomains=self.request.include_subdomains,
            include_paths=tuple(self.request.include_paths),
            exclude_paths=tuple(self.request.exclude_paths),
            query_policy=self.request.query_parameters,
            allowed_file_extensions=tuple(self.request.allowed_file_extensions),
        )

    def _seed_capabilities(self) -> frozenset[str]:
        if isinstance(self.request, CrawlRequest):
            return frozenset(self.request.capabilities) | {
                "links",
                "metadata",
                "markdown",
                "javascript",
            }
        return MAP_CAPABILITIES

    async def _append_content_result(
        self,
        outcome: FetchStageOutcome,
    ) -> None:
        result = await wire_fetch_result_with_schema(
            outcome,
            frozenset(self.request.capabilities),
            self.deps.markdown_cleaner,
            tuple(self.request.structured_formats),
            self.request.extraction_schema,
            self.deps,
        )
        self.results.append(result)
        await self._emit_result(result)
        self.page_outcomes[-1] = replace(
            outcome,
            code=FetchOutcomeCode(result.outcome),
        )

    async def _prepare_seed(
        self,
    ) -> tuple[
        frozenset[str] | None,
        FetchStageOutcome | None,
        RobotsSnapshot | None,
        MapResponse | CrawlResponse | None,
    ]:
        if not await self._is_safe(self.request.url):
            await self._emit_failure(self.request.url, "unsafe_seed")
            return None, None, None, self._response("unsafe_seed")
        seed_capabilities = self._seed_capabilities()
        seed = await self._fetch_target(self.request.url, seed_capabilities)
        if seed.code != FetchOutcomeCode.CONTENT or seed.page is None:
            reason = await self._record_seed_failure(seed)
            return None, None, None, self._response(reason)
        self.effective_url = seed.final_url or self.request.url
        self.effective_origin = _origin(self.effective_url)
        if self.effective_origin is None:
            return None, None, None, self._response("unsafe_redirect")
        self.frontier = CrawlFrontier(self._policy(self.effective_url))
        robots = await self._robots_for(self.effective_url)
        seed_record = await self._admit(self.effective_url, source="seed", depth=0)
        if seed_record is None or seed_record.states[-1] == "filtered":
            reason = (
                "robots_refused"
                if seed_record and seed_record.reason == "robots_refused"
                else "no_admitted_urls"
            )
            return None, None, None, self._response(reason)
        self.frontier.pop()
        self.frontier.mark_fetched(seed_record)
        self.page_outcomes.append(seed)
        if self.return_content:
            await self._append_content_result(seed)
        return seed_capabilities, seed, robots, None

    async def _process_page(
        self,
        record: FrontierRecord,
        page_attempts: int,
        seed_capabilities: frozenset[str],
    ) -> int:
        record_robots = await self._robots_for(record.url)
        if not record_robots.allows(record.url):
            self.frontier.mark_failed(record, "robots_refused")
            await self._emit_failure(record.url, "robots_refused")
            return page_attempts
        outcome = await self._fetch_target(record.url, seed_capabilities, record_robots)
        page_attempts += 1
        self.page_outcomes.append(outcome)
        if outcome.code != FetchOutcomeCode.CONTENT or outcome.page is None:
            self.frontier.mark_failed(record, outcome.code.value)
            await self._emit_failure(record.url, outcome.code.value)
            return page_attempts
        final_record = await self._reconcile_final(record, outcome)
        if final_record is None:
            if record.reason != "duplicate_final_url":
                await self._emit_failure(
                    record.url,
                    record.reason or "local_processing_failure",
                )
            return page_attempts
        self.frontier.mark_fetched(final_record)
        if self.return_content:
            await self._append_content_result(outcome)
        if self.request.sitemap != "only" and final_record.depth < self.request.max_depth:
            for link in _links(outcome):
                await self._admit(
                    link,
                    source="link",
                    depth=final_record.depth + 1,
                )
        return page_attempts

    async def _crawl_pages(self, seed_capabilities: frozenset[str]) -> None:
        page_attempts = 1
        while True:
            self._check_cancelled()
            record = self.frontier.pop()
            if record is None:
                break
            if record.states[-1] != "queued":
                continue
            if page_attempts >= self.max_pages:
                self.frontier.mark_cancelled(record, "page_limit_reached")
                self.frontier.cancel_queued()
                self.page_limit_reached = True
                break
            page_attempts = await self._process_page(
                record,
                page_attempts,
                seed_capabilities,
            )

    def _site_outcome(self) -> str:
        if self.page_limit_reached or self.frontier.omitted_due_to_limit:
            return "limit_reached"
        if any(record.states[-1] == "failed" for record in self.frontier.records) or any(
            outcome.code != FetchOutcomeCode.CONTENT for outcome in self.page_outcomes
        ):
            return "partial"
        return "completed"

    async def run(self):
        self._check_cancelled()
        seed_capabilities, seed, robots, early_response = await self._prepare_seed()
        if early_response is not None:
            return early_response
        assert seed_capabilities is not None and seed is not None and robots is not None
        if self.request.sitemap != "skip":
            await self._load_sitemaps(robots)
        if self.request.include_search:
            await self._augment_search()
        if self.request.sitemap != "only" and self.request.max_depth > 0:
            for link in _links(seed):
                await self._admit(link, source="link", depth=1)
        await self._crawl_pages(seed_capabilities)
        return self._response(self._site_outcome())

    def _records(self) -> list[SiteUrlRecord]:
        if self.frontier is None:
            return []
        records = [
            SiteUrlRecord(
                url=record.url,
                depth=record.depth,
                sources=record.sources,
                states=record.states,
                reason=record.reason,
                modified_at=record.modified_at,
                priority=record.priority,
            )
            for record in self.frontier.records
        ]
        budget = self.deps.resource_policy.max_response_body_bytes // 4
        retained: list[SiteUrlRecord] = []
        used = 2
        for record in records:
            item_bytes = len(record.model_dump_json().encode("utf-8")) + 1
            if used + item_bytes > budget:
                self.url_records_omitted += 1
                continue
            retained.append(record)
            used += item_bytes
        if self.url_records_omitted:
            self._warning("url_response_budget_reached")
        return retained

    def _results(self) -> list[FetchResult]:
        budget = self.deps.resource_policy.max_response_body_bytes // 2
        retained: list[FetchResult] = []
        used = 2
        for result in self.results:
            item_bytes = len(result.model_dump_json().encode("utf-8")) + 1
            if used + item_bytes > budget:
                self.results_omitted += 1
                continue
            retained.append(result)
            used += item_bytes
        if self.results_omitted:
            self._warning("result_response_budget_reached")
        return retained

    def _stats(self) -> SiteStats:
        records = self.frontier.records if self.frontier is not None else []
        state_counts = Counter(state for record in records for state in record.states)
        source_counts = Counter(source for record in records for source in record.sources)
        fetch_counts = Counter(outcome.code.value for outcome in self.page_outcomes)
        succeeded = fetch_counts.get(FetchOutcomeCode.CONTENT.value, 0)
        robots_state = None
        robots_source = None
        if self.effective_origin and self.effective_origin in self.robots:
            robots_state = self.robots[self.effective_origin].state
            robots_source = self.robots_sources[self.effective_origin]
        return SiteStats(
            discovered=state_counts["discovered"],
            admitted=state_counts["admitted"],
            queued=state_counts["queued"],
            fetched=state_counts["fetched"],
            filtered=state_counts["filtered"],
            failed=state_counts["failed"],
            cancelled=state_counts["cancelled"],
            omitted=(self.frontier.omitted_due_to_limit if self.frontier else 0)
            + self.url_records_omitted,
            discovery_limit_omitted=(
                self.frontier.omitted_due_to_limit if self.frontier else 0
            ),
            response_budget_omitted=self.url_records_omitted,
            results_omitted=self.results_omitted,
            pages_succeeded=succeeded,
            pages_failed=len(self.page_outcomes) - succeeded,
            sitemap_documents_attempted=self.sitemap_documents_attempted,
            sitemap_documents=self.sitemap_documents,
            sitemap_entries=self.sitemap_entries,
            sitemap_truncated=self.sitemap_truncated,
            robots_documents_attempted=self.robots_documents_attempted,
            non_http_urls_skipped=(
                self.frontier.non_http_urls_skipped if self.frontier else 0
            ),
            robots_state=robots_state,
            robots_source=robots_source,
            source_counts=[
                SiteSourceCount(source=source, count=count)
                for source, count in sorted(source_counts.items())
            ],
            fetch_outcomes=[
                FetchOutcomeCount(outcome=outcome, count=count)
                for outcome, count in sorted(fetch_counts.items())
            ],
            elapsed_ms=_elapsed_ms(self.started),
        )

    def _response(self, outcome: str):
        records = self._records()
        results = self._results() if self.return_content else []
        values = {
            "requested_url": self.request.url,
            "effective_url": self.effective_url,
            "requested_origin": self.requested_origin,
            "effective_origin": self.effective_origin,
            "outcome": outcome,
            "urls": records,
            "stats": self._stats(),
            "warnings": self.warnings,
        }
        if self.return_content:
            return CrawlResponse(results=results, **values)
        return MapResponse(**values)


async def run_map(req: MapRequest, deps) -> MapResponse:
    async with deps.admission.map_slot():
        try:
            async with asyncio.timeout(deps.resource_policy.map_route_deadline_s):
                return await _SiteOperation(req, deps, return_content=False).run()
        except TimeoutError as exc:
            raise RouteDeadlineExceeded("map") from exc


async def run_crawl(req: CrawlRequest, deps) -> CrawlResponse:
    async with deps.admission.crawl_slot():
        try:
            async with asyncio.timeout(deps.resource_policy.site_crawl_route_deadline_s):
                return await _SiteOperation(req, deps, return_content=True).run()
        except TimeoutError as exc:
            raise RouteDeadlineExceeded("crawl") from exc


async def run_crawl_job(
    req: CrawlRequest,
    deps,
    on_result: Callable[[FetchResult], Awaitable[None]],
    on_failure: Callable[[str, str], Awaitable[None]],
    cancel_requested: Callable[[], bool],
    attempt_deadline_s: float,
) -> CrawlResponse:
    async with deps.admission.crawl_slot():
        try:
            async with asyncio.timeout(attempt_deadline_s):
                return await _SiteOperation(
                    req,
                    deps,
                    return_content=True,
                    on_result=on_result,
                    on_failure=on_failure,
                    cancel_requested=cancel_requested,
                ).run()
        except TimeoutError as exc:
            raise RouteDeadlineExceeded("crawl") from exc
