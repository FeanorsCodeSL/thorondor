"""Preset endpoint catalog for model services Thorondor can use."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

DependencyKind = Literal["embedding", "reranker", "llm"]


@dataclass(frozen=True)
class CatalogEntry:
    key: str
    display_name: str
    kind: DependencyKind
    default_base_url: str
    default_model: str
    health_path: str = "/health"
    rerank_path: str = "/rerank"
    needs_token: bool = False


CATALOG: tuple[CatalogEntry, ...] = (
    CatalogEntry(
        key="tei-embedding",
        display_name="Hugging Face TEI embedding",
        kind="embedding",
        default_base_url="http://embedding:80",
        default_model="BAAI/bge-m3",
    ),
    CatalogEntry(
        key="tei-reranker",
        display_name="Hugging Face TEI reranker",
        kind="reranker",
        default_base_url="http://reranker:80",
        default_model="BAAI/bge-reranker-v2-m3",
    ),
    CatalogEntry(
        key="llamacpp-embedding",
        display_name="llama.cpp embedding server",
        kind="embedding",
        default_base_url="http://embedding:8080",
        default_model="bge-m3",
    ),
    CatalogEntry(
        key="llamacpp-reranker",
        display_name="llama.cpp reranker server",
        kind="reranker",
        default_base_url="http://reranker:8080",
        default_model="bge-reranker-v2-m3",
        rerank_path="/reranking",
    ),
    CatalogEntry(
        key="infinity-embedding",
        display_name="Infinity embedding",
        kind="embedding",
        default_base_url="http://localhost:7997",
        default_model="BAAI/bge-m3",
    ),
    CatalogEntry(
        key="infinity-reranker",
        display_name="Infinity reranker",
        kind="reranker",
        default_base_url="http://localhost:7997",
        default_model="BAAI/bge-reranker-v2-m3",
    ),
    CatalogEntry(
        key="vllm-llm",
        display_name="vLLM OpenAI-compatible chat",
        kind="llm",
        default_base_url="http://localhost:8000/v1",
        default_model="",
    ),
    CatalogEntry(
        key="ollama-llm",
        display_name="Ollama OpenAI-compatible chat",
        kind="llm",
        default_base_url="http://localhost:11434/v1",
        default_model="llama3.1",
    ),
    CatalogEntry(
        key="openai-llm",
        display_name="OpenAI chat completions",
        kind="llm",
        default_base_url="https://api.openai.com/v1",
        default_model="gpt-4.1-mini",
        needs_token=True,
    ),
)


def entries_for(kind: DependencyKind) -> list[CatalogEntry]:
    return [entry for entry in CATALOG if entry.kind == kind]


def get_entry(key: str) -> CatalogEntry | None:
    return next((entry for entry in CATALOG if entry.key == key), None)
