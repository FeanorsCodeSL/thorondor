import pytest
from fastapi.testclient import TestClient

import chunking.app as appmod
from tests.conftest import fake_embed


class _FakeEmbedder:
    def __call__(self, texts):
        return fake_embed(texts)

    def health_check(self):
        return True


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(appmod, "_embedder", _FakeEmbedder())
    return TestClient(appmod.app)


def test_chunk_happy_path_passes_metadata_and_version(client):
    r = client.post("/chunk", json={
        "text": "Alpha beta gamma. " * 30,
        "source_type": "WEB_MARKDOWN",
        "metadata": {"source_url": "https://x.test", "title": "X"},
    })
    assert r.status_code == 200
    body = r.json()
    assert body["chunk_count"] == len(body["chunks"]) >= 1
    first = body["chunks"][0]
    assert first["metadata"]["source_url"] == "https://x.test"
    assert first["metadata"]["strategy_version"] == "cluster-semantic@1"
    assert first["metadata"]["chunk_strategy"]
    assert first["metadata"]["embedding_degraded"] is False
    assert body["chunk_strategy"]
    assert body["embedding_degraded"] is False
    assert first["position"] == 0


def test_embedding_failure_surfaces_fallback_marker(monkeypatch):
    class DownEmbedder:
        def __call__(self, texts):
            raise RuntimeError("embedding refused")

        def health_check(self):
            return False

    monkeypatch.setattr(appmod, "_embedder", DownEmbedder())
    client = TestClient(appmod.app)

    r = client.post("/chunk", json={"text": "Alpha beta gamma. " * 80})

    assert r.status_code == 200
    body = r.json()
    assert body["chunk_strategy"] == "cluster-semantic-greedy-token"
    assert body["embedding_degraded"] is True
    assert body["chunks"][0]["metadata"]["chunk_strategy"] == "cluster-semantic-greedy-token"
    assert body["chunks"][0]["metadata"]["embedding_degraded"] is True


def test_unknown_strategy_version_is_400(client):
    r = client.post("/chunk", json={"text": "hi there friend", "strategy_version": "nope@9"})
    assert r.status_code == 400


def test_oversized_chunk_text_is_422(client):
    r = client.post("/chunk", json={"text": "x" * 200_001})
    assert r.status_code == 422


@pytest.mark.parametrize(
    "params",
    [
        {"max_chunk_tokens": 40, "initial_segment_tokens": 60},
        {"max_chunk_tokens": 40, "min_chunk_tokens": 60},
    ],
)
def test_invalid_chunk_param_relationships_are_400(client, params):
    r = client.post(
        "/chunk",
        json={
            "text": "Alpha beta gamma. " * 30,
            "params": params,
        },
    )

    assert r.status_code == 400


def test_web_markdown_preclean_strips_image_lines(client):
    r = client.post(
        "/chunk",
        json={
            "text": "Real prose here.\n\n![alt](http://img)\n\nMore prose.",
            "source_type": "WEB_MARKDOWN",
        },
    )
    assert "![alt]" not in r.json()["chunks"][0]["text"]


@pytest.mark.parametrize(
    "text",
    [
        "Alpha beta gamma.\n\nDelta epsilon.",
        "Español naïve e\u0301lan 東京.\n\nПривет мир.",
        "# Heading\r\n\r\nFirst paragraph.\r\n\r\nSecond paragraph.",
        "![diagram](https://example.test/image.png)\n\nEvidence below the image.",
        "Repeated paragraph.\n\nRepeated paragraph.\n\nDifferent ending.",
        "First paragraph.\n\nSecond paragraph.\n\nThird paragraph.",
    ],
)
def test_orchestrator_markdown_returns_exact_source_slices(client, text):
    response = client.post(
        "/chunk",
        json={
            "text": text,
            "source_type": "ORCHESTRATOR_MARKDOWN",
            "params": {"initial_segment_tokens": 3, "min_chunk_tokens": 1, "max_chunk_tokens": 6},
        },
    )

    assert response.status_code == 200
    chunks = response.json()["chunks"]
    assert chunks
    assert "".join(chunk["text"] for chunk in chunks) == text
    for chunk in chunks:
        assert chunk["verbatim"] is True
        assert chunk["text"] == text[chunk["start_index"]:chunk["end_index"]]


def test_orchestrator_markdown_fallback_preserves_exact_source_slices(monkeypatch):
    class DownEmbedder:
        def __call__(self, _texts):
            raise RuntimeError("embedding refused")

        def health_check(self):
            return False

    monkeypatch.setattr(appmod, "_embedder", DownEmbedder())
    client = TestClient(appmod.app)
    text = "Alpha beta gamma. " * 80

    response = client.post(
        "/chunk",
        json={"text": text, "source_type": "ORCHESTRATOR_MARKDOWN"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["embedding_degraded"] is True
    for chunk in body["chunks"]:
        assert chunk["verbatim"] is True
        assert chunk["text"] == text[chunk["start_index"]:chunk["end_index"]]


def test_healthz_reports_embedding(client):
    assert client.get("/healthz").json() == {"status": "ok", "embedding": True}


def test_request_id_header_is_echoed(client):
    response = client.get("/healthz", headers={"X-Request-ID": "chunk-req"})

    assert response.headers["X-Request-ID"] == "chunk-req"
