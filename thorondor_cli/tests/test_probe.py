import httpx
import pytest
from thorondor_cli.probe import (
    list_models,
    probe_embedding,
    probe_llm,
    probe_reranker,
    rewrite_host_for_docker,
)

respx = pytest.importorskip("respx")


@respx.mock
def test_embedding_probe_maps_success_and_auth_error():
    respx.post("http://models.test/v1/embeddings").mock(return_value=httpx.Response(200, json={}))
    assert probe_embedding("http://models.test", "bge", "secret").status == "ok"

    respx.post("http://auth.test/v1/embeddings").mock(return_value=httpx.Response(401))
    result = probe_embedding("http://auth.test", "bge", "secret")
    assert result.status == "auth_error"
    assert "secret" not in result.detail


@respx.mock
def test_reranker_probe_sends_production_body_and_flags_bad_model():
    route = respx.post("http://rank.test/rerank").mock(
        return_value=httpx.Response(400, text="bad model")
    )

    result = probe_reranker("http://rank.test", "/rerank", "bad-model", "token")

    assert result.status == "model_not_found"
    assert route.calls.last.request.headers["authorization"] == "Bearer token"
    assert route.calls.last.request.read() == (
        b'{"query":"ping","documents":["ping"],"model":"bad-model"}'
    )


@respx.mock
def test_list_models_parses_openai_and_bare_list_shapes():
    respx.get("http://openai.test/models").mock(
        return_value=httpx.Response(200, json={"data": [{"id": "a"}, {"id": "b"}]})
    )
    assert list_models("http://openai.test") == ["a", "b"]

    respx.get("http://bare.test/models").mock(return_value=httpx.Response(200, json=["x", "y"]))
    assert list_models("http://bare.test") == ["x", "y"]

    respx.get("http://bad.test/models").mock(return_value=httpx.Response(200, json={"no": "data"}))
    assert list_models("http://bad.test") is None


@respx.mock
def test_llm_probe_transport_error_maps_unreachable():
    respx.post("http://llm.test/chat/completions").mock(side_effect=httpx.ConnectError("nope"))
    assert probe_llm("http://llm.test", "model").status == "unreachable"


def test_host_rewrite_preserves_scheme_port_path_query():
    rewritten, changed = rewrite_host_for_docker("http://localhost:8082/v1?x=1")
    assert changed is True
    assert rewritten == "http://host.docker.internal:8082/v1?x=1"
    assert rewrite_host_for_docker("https://example.com:443/v1") == (
        "https://example.com:443/v1",
        False,
    )
