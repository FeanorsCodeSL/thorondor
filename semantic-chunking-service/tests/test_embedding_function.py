import httpx
import pytest

import chunking.embedding_function as ef
from chunking.observability import reset_request_id, set_request_id


class _Resp:
    def __init__(self, payload, status_code=200, text=""):
        self._payload = payload
        self.status_code = status_code
        self._text = text

    def json(self):
        return self._payload

    @property
    def text(self):
        return self._text


class _FakeClient:
    """Records POSTs; returns OpenAI-shaped embeddings echoing input order."""
    posts = []

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def post(self, url, json, headers=None):
        _FakeClient.posts.append({"url": url, "json": json, "headers": headers})
        data = [
            {"index": i, "embedding": [float(len(text)), 1.0]}
            for i, text in enumerate(json["input"])
        ]
        return _Resp({"data": data})


@pytest.fixture
def fake_http(monkeypatch):
    _FakeClient.posts = []
    monkeypatch.setattr(httpx, "Client", _FakeClient)
    return _FakeClient


def test_batches_and_preserves_order(fake_http):
    fn = ef.EmbeddingFunction(endpoint="http://h:1", model="m", batch_size=2, timeout_s=60)

    out = fn(["a", "bb", "ccc", "dddd", "e"])

    assert len(out) == 5
    assert out[3][0] == 4.0
    assert len(fake_http.posts) == 3
    assert fake_http.posts[0]["url"] == "http://h:1/v1/embeddings"


def test_non_200_raises(monkeypatch):
    class _Err(_FakeClient):
        def post(self, url, json, headers=None):
            return _Resp({}, status_code=500, text="boom")

    monkeypatch.setattr(httpx, "Client", _Err)

    with pytest.raises(RuntimeError):
        ef.EmbeddingFunction(endpoint="http://h:1", model="m", batch_size=64, timeout_s=60)(["x"])


@pytest.mark.parametrize(
    ("endpoint", "expected_url"),
    [
        ("https://embed.example/v1", "https://embed.example/v1/embeddings"),
        ("http://embed.example:8080/prefix", "http://embed.example:8080/prefix/v1/embeddings"),
        ("http://embed.example:8080", "http://embed.example:8080/v1/embeddings"),
        ("https://embed.example/custom/embeddings", "https://embed.example/custom/embeddings"),
    ],
)
def test_endpoint_url_preserves_scheme_path_and_default_port(
    monkeypatch, fake_http, endpoint, expected_url
):
    fn = ef.EmbeddingFunction(endpoint=endpoint, model="m", batch_size=64, timeout_s=60)

    fn(["x"])

    assert fake_http.posts[0]["url"] == expected_url


def test_endpoint_parsing_requires_host(monkeypatch):
    with pytest.raises(ValueError):
        ef.EmbeddingFunction(endpoint="https:///missing-host", model="m", batch_size=64, timeout_s=60)


def test_embedding_api_key_is_sent_as_bearer_header(fake_http):
    ef.EmbeddingFunction(
        endpoint="http://h:1",
        model="m",
        batch_size=64,
        timeout_s=60,
        api_key="secret",
    )(["x"])

    assert fake_http.posts[0]["headers"] == {"Authorization": "Bearer secret"}


def test_embedding_request_id_is_forwarded(fake_http):
    token = set_request_id("req-embed")
    try:
        ef.EmbeddingFunction(endpoint="http://h:1", model="m", batch_size=64, timeout_s=60)(["x"])
    finally:
        reset_request_id(token)

    assert fake_http.posts[0]["headers"] == {"X-Request-ID": "req-embed"}


def test_embedding_error_log_omits_upstream_body(monkeypatch, caplog):
    class _Err(_FakeClient):
        def post(self, url, json, headers=None):
            return _Resp({}, status_code=500, text="secret upstream body")

    monkeypatch.setattr(httpx, "Client", _Err)

    with pytest.raises(RuntimeError):
        ef.EmbeddingFunction(
            endpoint="http://user:pass@example.test:1",
            model="m",
            batch_size=64,
            timeout_s=60,
        )(["x"])

    error_messages = [record.getMessage() for record in caplog.records if record.levelname == "ERROR"]
    assert error_messages
    assert "secret upstream body" not in "\n".join(error_messages)
    assert "user:pass" not in "\n".join(error_messages)
