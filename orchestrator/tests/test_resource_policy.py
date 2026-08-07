import asyncio

import anyio
import pytest

from orchestrator import fakes
from orchestrator.models import MapRequest, SearchRequest
from orchestrator.pipeline import run_search
from orchestrator.resource_policy import (
    CapacityUnavailable,
    ResourcePolicy,
    RouteDeadlineExceeded,
    RuntimeAdmission,
)
from orchestrator.site_pipeline import run_map


def _policy(**overrides):
    values = ResourcePolicy().__dict__ | overrides
    return ResourcePolicy(**values)


def test_search_admission_rejects_without_leaking_the_slot():
    admission = RuntimeAdmission(_policy(max_inflight_searches=1, admission_wait_s=0.001))

    async def exercise():
        async with admission.search_slot():
            with pytest.raises(CapacityUnavailable) as exc_info:
                async with admission.search_slot():
                    raise AssertionError("unreachable")
            assert exc_info.value.retry_after_s == 1
            assert admission.active_searches == 1
        async with admission.search_slot():
            assert admission.active_searches == 1
        assert admission.active_searches == 0

    anyio.run(exercise)


def test_fetch_admission_is_independent_from_search_admission():
    admission = RuntimeAdmission(
        _policy(max_inflight_searches=1, max_inflight_fetches=1)
    )

    async def exercise():
        async with admission.search_slot():
            async with admission.fetch_slot():
                assert admission.active_searches == 1
                assert admission.active_fetches == 1

    anyio.run(exercise)


def test_map_and_crawl_admission_are_independent_and_release_slots():
    admission = RuntimeAdmission(
        _policy(max_inflight_maps=1, max_inflight_crawls=1)
    )

    async def exercise():
        async with admission.map_slot():
            async with admission.crawl_slot():
                assert admission.active_maps == 1
                assert admission.active_crawls == 1
        assert admission.active_maps == 0
        assert admission.active_crawls == 0

    anyio.run(exercise)


def test_search_route_deadline_cancels_work_and_releases_admission():
    cancelled = asyncio.Event()

    class BlockingPlanner:
        async def plan(self, _query):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

    deps = fakes.deps(planner=BlockingPlanner())
    deps.resource_policy = _policy(search_route_deadline_s=0.01)
    deps.admission = RuntimeAdmission(deps.resource_policy)

    async def exercise():
        with pytest.raises(RouteDeadlineExceeded) as exc_info:
            await run_search(SearchRequest(query="x"), deps)
        assert exc_info.value.route == "search"
        assert exc_info.value.reason == "deadline_cancelled"
        assert cancelled.is_set()
        assert deps.admission.active_searches == 0

    anyio.run(exercise)


def test_caller_cancellation_releases_search_admission():
    started = asyncio.Event()
    cancelled = asyncio.Event()

    class BlockingPlanner:
        async def plan(self, _query):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

    deps = fakes.deps(planner=BlockingPlanner())

    async def exercise():
        task = asyncio.create_task(run_search(SearchRequest(query="x"), deps))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert cancelled.is_set()
        assert deps.admission.active_searches == 0

    anyio.run(exercise)


def test_map_route_deadline_cancels_fetch_and_releases_admission():
    cancelled = asyncio.Event()

    class BlockingExtractor:
        supported_capabilities = frozenset(
            {"markdown", "javascript", "links", "metadata", "raw_html"}
        )

        async def fetch(self, _urls, _capabilities, _include_raw_html):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

    deps = fakes.deps(extractor=BlockingExtractor())
    deps.resource_policy = _policy(map_route_deadline_s=0.01)
    deps.admission = RuntimeAdmission(deps.resource_policy)

    async def exercise():
        with pytest.raises(RouteDeadlineExceeded) as exc_info:
            await run_map(
                MapRequest(
                    url="https://a.test/article",
                    sitemap="skip",
                    max_pages=1,
                ),
                deps,
            )
        assert exc_info.value.route == "map"
        assert cancelled.is_set()
        assert deps.admission.active_maps == 0

    anyio.run(exercise)
