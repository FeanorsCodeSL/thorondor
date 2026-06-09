import httpx
import pytest

import chunking.embedding_function as ef


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
    fn = ef.EmbeddingFunction(model="m", host="h", port="1", batch_size=2)

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
        ef.EmbeddingFunction(model="m", host="h", port="1")(["x"])


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
    monkeypatch.setenv("EMBEDDING_ENDPOINT", endpoint)
    fn = ef.EmbeddingFunction(model="m")

    fn(["x"])

    assert fake_http.posts[0]["url"] == expected_url


def test_endpoint_parsing_requires_host(monkeypatch):
    monkeypatch.setenv("EMBEDDING_ENDPOINT", "https:///missing-host")

    with pytest.raises(ValueError):
        ef._get_embedding_endpoint()


def test_embedding_api_key_is_sent_as_bearer_header(fake_http):
    ef.EmbeddingFunction(model="m", host="h", port="1", api_key="secret")(["x"])

    assert fake_http.posts[0]["headers"] == {"Authorization": "Bearer secret"}
