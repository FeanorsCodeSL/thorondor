#!/usr/bin/env python3

from __future__ import annotations

import argparse
import asyncio

import orchestrator.app as appmod
import uvicorn
from fastapi.responses import JSONResponse
from orchestrator import fakes, mcp_server
from orchestrator.resource_policy import ResourcePolicy


class BlockingOnceExtractor:
    supported_capabilities = fakes.FakeExtractor.supported_capabilities

    def __init__(self) -> None:
        self.started = False
        self.cancelled = False
        self._first = True
        self.release_requested = asyncio.Event()

    async def extract(self, urls):
        return await fakes.FakeExtractor().extract(urls)

    async def fetch(self, urls, capabilities, include_raw_html):
        if self._first:
            self._first = False
            self.started = True
            try:
                await self.release_requested.wait()
            finally:
                self.cancelled = True
        return await fakes.FakeExtractor().fetch(urls, capabilities, include_raw_html)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--block-fetch", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    extractor = BlockingOnceExtractor() if args.block_fetch else None
    policy = (
        ResourcePolicy(max_inflight_fetches=1, admission_wait_s=0)
        if extractor
        else ResourcePolicy()
    )
    runtime_deps = fakes.deps(
        extractor=extractor or fakes.FakeExtractor(),
        resource_policy=policy,
    )
    appmod.deps = runtime_deps
    mcp_server.set_deps(runtime_deps)

    async def state():
        return JSONResponse(
            {
                "fetch_started": bool(extractor and extractor.started),
                "fetch_cancelled": bool(extractor and extractor.cancelled),
                "active_fetches": runtime_deps.admission.active_fetches,
            }
        )

    async def release():
        if extractor:
            extractor.release_requested.set()
        return JSONResponse({"released": bool(extractor)})

    appmod.app.add_api_route(
        "/__conformance__/state",
        state,
        methods=["GET"],
        include_in_schema=False,
    )
    appmod.app.add_api_route(
        "/__conformance__/release",
        release,
        methods=["POST"],
        include_in_schema=False,
    )
    uvicorn.run(appmod.app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
