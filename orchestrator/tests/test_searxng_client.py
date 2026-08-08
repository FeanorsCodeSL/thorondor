import anyio
import httpx
import pytest

from orchestrator.clients.searxng_client import DiscoveryUnavailable, SearxngDiscovery
from orchestrator.observability import reset_request_id, set_request_id


def test_parses_searxng_json_and_freshness(monkeypatch):
    seen = {}

    def handler(req):
        seen["query"] = str(req.url)
        return httpx.Response(200, json={"results": [
            {
                "title": "T",
                "url": "https://a.test",
                "content": "snip",
                "engine": "brave",
                "score": 1.0,
                "publishedDate": "2026-08-04T10:15:00Z",
            }
        ]})

    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: real_async_client(transport=transport))

    out = anyio.run(SearxngDiscovery("http://searxng:8080").search, "q", "week")

    assert out.results[0].url == "https://a.test" and out.results[0].engine == "brave"
    assert out.results[0].published_at == "2026-08-04T10:15:00Z"
    assert out.unresponsive_engines == []
    assert "time_range=week" in seen["query"]


def test_non_200_raises(monkeypatch):
    transport = httpx.MockTransport(lambda req: httpx.Response(500, json={}))
    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: real_async_client(transport=transport))

    with pytest.raises(DiscoveryUnavailable):
        anyio.run(SearxngDiscovery("http://searxng:8080").search, "q")


def test_transport_failure_does_not_expose_internal_url_or_query(monkeypatch):
    def handler(req):
        raise httpx.ConnectError(f"failed to connect to {req.url}", request=req)

    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: real_async_client(transport=transport))

    with pytest.raises(DiscoveryUnavailable) as exc:
        anyio.run(SearxngDiscovery("http://searxng:8080").search, "secret query")

    assert str(exc.value) == "transport_error"
    assert "secret" not in str(exc.value)
    assert "searxng" not in str(exc.value)


def test_zero_scores_fall_back_to_rank_order(monkeypatch):
    def handler(req):
        return httpx.Response(200, json={"results": [
            {"title": "first", "url": "https://first.test", "score": 0},
            {"title": "second", "url": "https://second.test", "score": 0},
            {"title": "third", "url": "https://third.test", "score": 0},
        ]})

    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: real_async_client(transport=transport))

    out = anyio.run(SearxngDiscovery("http://searxng:8080").search, "q")

    assert [item.url for item in out.results] == [
        "https://first.test",
        "https://second.test",
        "https://third.test",
    ]
    assert [item.score for item in out.results] == [1.0, 0.5, 1 / 3]


def test_parses_unresponsive_engines(monkeypatch):
    def handler(req):
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "T",
                        "url": "https://a.test",
                        "engine": "bing",
                        "score": 1.0,
                    }
                ],
                "unresponsive_engines": [
                    ["mojeek", "access denied"],
                    ["startpage", "Suspended: CAPTCHA"],
                ],
            },
        )

    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: real_async_client(transport=transport))

    out = anyio.run(SearxngDiscovery("http://searxng:8080").search, "q")

    assert [(item.engine, item.reason) for item in out.unresponsive_engines] == [
        ("mojeek", "access denied"),
        ("startpage", "Suspended: CAPTCHA"),
    ]


def test_parses_plural_engines_positions_and_subquery_contributions(monkeypatch):
    def handler(req):
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "T",
                        "url": "https://a.test",
                        "engine": "engine-a",
                        "engines": ["engine-a", "engine-b"],
                        "positions": [1, 3],
                        "score": 0.91,
                    }
                ]
            },
        )

    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: real_async_client(transport=transport))

    out = anyio.run(SearxngDiscovery("http://searxng:8080").search, "query one")

    assert [
        (item.subquery, item.engine, item.position, item.score)
        for item in out.results[0].contributions
    ] == [
        ("query one", "engine-a", 1, 0.91),
        ("query one", "engine-b", 3, 0.91),
    ]


def test_invalid_plural_position_keeps_engine_alignment(monkeypatch):
    transport = httpx.MockTransport(
        lambda req: httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "T",
                        "url": "https://a.test",
                        "engines": ["engine-a", "engine-b", "engine-c"],
                        "positions": [1, -1, 3],
                        "score": 0.91,
                    }
                ]
            },
        )
    )
    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: real_async_client(transport=transport))

    out = anyio.run(SearxngDiscovery("http://searxng:8080").search, "query")

    assert [(item.engine, item.position) for item in out.results[0].contributions] == [
        ("engine-a", 1),
        ("engine-b", None),
        ("engine-c", 3),
    ]


def test_missing_plural_positions_are_not_fabricated(monkeypatch):
    transport = httpx.MockTransport(
        lambda req: httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "T",
                        "url": "https://a.test",
                        "engines": ["engine-a", "engine-b"],
                        "score": 0.91,
                    }
                ]
            },
        )
    )
    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: real_async_client(transport=transport))

    out = anyio.run(SearxngDiscovery("http://searxng:8080").search, "query")

    assert [(item.engine, item.position) for item in out.results[0].contributions] == [
        ("engine-a", None),
        ("engine-b", None),
    ]


def test_duplicate_plural_engines_are_reported_once(monkeypatch):
    transport = httpx.MockTransport(
        lambda req: httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "T",
                        "url": "https://a.test",
                        "engines": ["engine-a", "engine-a"],
                        "positions": [1, 2],
                        "score": 0.91,
                    }
                ]
            },
        )
    )
    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: real_async_client(transport=transport))

    out = anyio.run(SearxngDiscovery("http://searxng:8080").search, "query")

    assert [(item.engine, item.position) for item in out.results[0].contributions] == [
        ("engine-a", 1),
    ]


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="<html>upstream error</html>"),
        httpx.Response(200, json=[]),
        httpx.Response(200, json={"results": {"bad": "shape"}}),
        httpx.Response(200, json={"results": [{"url": "https://a.test", "score": "high"}]}),
    ],
)
def test_malformed_success_response_raises_closed_reason(monkeypatch, response):
    transport = httpx.MockTransport(lambda req: response)
    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: real_async_client(transport=transport))

    with pytest.raises(DiscoveryUnavailable, match="malformed_response"):
        anyio.run(SearxngDiscovery("http://searxng:8080").search, "secret query")


def test_api_key_is_sent_as_bearer_header(monkeypatch):
    seen = {}

    def handler(req):
        seen["authorization"] = req.headers.get("authorization")
        seen["x_real_ip"] = req.headers.get("x-real-ip")
        return httpx.Response(200, json={"results": []})

    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: real_async_client(transport=transport))

    anyio.run(SearxngDiscovery("http://searxng:8080", api_key="secret").search, "q")

    assert seen["authorization"] == "Bearer secret"
    assert seen["x_real_ip"] == "127.0.0.1"


def test_request_id_is_forwarded(monkeypatch):
    seen = {}

    def handler(req):
        seen["request_id"] = req.headers.get("x-request-id")
        return httpx.Response(200, json={"results": []})

    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: real_async_client(transport=transport))

    token = set_request_id("req-101")
    try:
        anyio.run(SearxngDiscovery("http://searxng:8080").search, "q")
    finally:
        reset_request_id(token)

    assert seen["request_id"] == "req-101"
