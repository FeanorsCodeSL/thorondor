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

    def post(self, url, json):
        _FakeClient.posts.append(json)
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


def test_non_200_raises(monkeypatch):
    class _Err(_FakeClient):
        def post(self, url, json):
            return _Resp({}, status_code=500, text="boom")

    monkeypatch.setattr(httpx, "Client", _Err)

    with pytest.raises(RuntimeError):
        ef.EmbeddingFunction(model="m", host="h", port="1")(["x"])


def test_endpoint_parsing_requires_host_and_port(monkeypatch):
    monkeypatch.setenv("EMBEDDING_ENDPOINT", "http://just-host")

    with pytest.raises(ValueError):
        ef._get_embedding_endpoint()
