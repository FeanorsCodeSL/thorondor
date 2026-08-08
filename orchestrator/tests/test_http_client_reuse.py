import json

import anyio
import httpx

from orchestrator.clients.chunker_client import ChunkerClient
from orchestrator.clients.crawl4ai_client import Crawl4aiExtractor
from orchestrator.clients.planner import LlmPlanner
from orchestrator.clients.reranker_client import RerankerClient
from orchestrator.clients.searxng_client import SearxngDiscovery
from orchestrator.types import Chunk, Page


def test_stage_clients_reuse_one_async_client_per_instance(monkeypatch):
    created = 0

    def handler(req):
        path = req.url.path
        if path == "/search":
            return httpx.Response(200, json={"results": [{"url": "https://a.test", "score": 1.0}]})
        if path == "/crawl":
            url = json.loads(req.content)["urls"][0]
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "results": [{"success": True, "url": url, "markdown": {"raw_markdown": "content"}}],
                },
            )
        if path == "/chunk":
            source_url = json.loads(req.content)["metadata"]["source_url"]
            return httpx.Response(
                200,
                json={
                    "chunks": [
                        {
                            "text": "chunk",
                            "token_count": 1,
                            "position": 0,
                            "metadata": {"source_url": source_url, "title": "T"},
                        }
                    ],
                    "strategy_version": "cluster-semantic@1",
                    "chunk_strategy": "cluster-semantic-dp",
                    "embedding_degraded": False,
                },
            )
        if path == "/rerank":
            return httpx.Response(200, json={"results": [{"index": 0, "score": 1.0}]})
        if path == "/v1/chat/completions":
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": json.dumps(["q1", "q2", "q3", "q4"])}}]},
            )
        return httpx.Response(404, json={})

    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient

    def client_factory(*args, **kwargs):
        nonlocal created
        created += 1
        return real_async_client(transport=transport)

    monkeypatch.setattr(httpx, "AsyncClient", client_factory)

    async def exercise():
        discovery = SearxngDiscovery("http://searxng:8080")
        extractor = Crawl4aiExtractor(
            "http://crawl4ai:11235",
            2,
            1,
            respect_robots_txt=True,
            per_host_concurrency=1,
            crawler_user_agent="ThorondorBot/1.0 (+https://example.test/contact)",
            url_safety=lambda _url: True,
        )
        chunker = ChunkerClient("http://chunker:8000")
        reranker = RerankerClient(
            "http://reranker:80",
            "m",
            path="/rerank",
            batch_size=32,
            timeout_s=30.0,
        )
        planner = LlmPlanner("http://llm:80", "m")

        for _ in range(2):
            await discovery.search("q")
            await extractor.extract(["https://a.test"])
            await chunker.chunk([Page("https://a.test", "T", "content")])
            await reranker.rerank("q", [Chunk("chunk", 1, "https://a.test", "T", 0)])
            await planner.plan("q")

        for client in (discovery, extractor, chunker, reranker, planner):
            await client.aclose()

    anyio.run(exercise)

    assert created == 5
