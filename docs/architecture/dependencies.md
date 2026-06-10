# Dependencies

## 1. Service Dependency Graph

```mermaid
graph LR
    Client([Client / Agent])

    Client -->|REST or MCP| Orchestrator

    Orchestrator -->|GET /search| SearXNG
    Orchestrator -->|POST /crawl| Crawl4AI
    Orchestrator -->|POST /chunk| Chunker
    Orchestrator -->|POST RERANKER_PATH| Reranker
    Orchestrator -->|POST /v1/chat/completions\noptional| LLMPlanner[LLM Planner]

    Crawl4AI -->|HTTP CONNECT via| EgressProxy[SSRF Egress Proxy]
    EgressProxy -->|filtered egress| Internet([Internet])
    SearXNG -->|engine queries| Internet

    Chunker -->|POST /v1/embeddings| Embedding

    style EgressProxy fill:#f9a,stroke:#c66
    style LLMPlanner fill:#eee,stroke:#999,stroke-dasharray:5
```

Dashed border = optional component (not started unless `LLM_ENDPOINT` is configured or the models profile activates it).

## 2. Docker Image Inventory

| Image | Pinned Tag / SHA | Architecture | License | Purpose |
|---|---|---|---|---|
| `searxng/searxng` | `@sha256:02d441bbb647b7be422d21041420115cddadac4644368f67c7c7f407bbe72e22` | amd64, arm64 | AGPL-3.0 | Multi-engine URL discovery |
| `unclecode/crawl4ai` | `0.8.9` (`sha256:b243f684...`) | amd64 | Apache-2.0 | JavaScript-capable page crawling |
| `ghcr.io/huggingface/text-embeddings-inference` | `@sha256:b3e0169969c0dc4b22ab6bf6ad5699374d4cb720fc43fb66868a679586ea806f` | amd64, arm64 | Apache-2.0 | Embedding + reranking (`bundled-models` profile) |
| `ghcr.io/ggml-org/llama.cpp:server` | `@sha256:4c52f549b6612fc1b4aee696c4cfb4a9dceecb10216bb7e677cf97db909e1b4a` | amd64, arm64 | MIT | Embedding + reranking via GGUF (`llamacpp-models` profile) |
| `python:3.12-slim` | latest slim at build time | amd64, arm64 | PSF License | Base for orchestrator Dockerfile |
| `python:3.13-slim` | latest slim at build time | amd64, arm64 | PSF License | Base for chunker Dockerfile |

> Note: `python:3.12-slim` and `python:3.13-slim` are not pinned by SHA. For reproducible production builds, pin to a specific digest.

## 3. Python Package Inventory

### Orchestrator (`orchestrator/requirements.txt`)

| Package | Version | License | Purpose |
|---|---|---|---|
| FastAPI | 0.136.3 | MIT | REST API framework |
| Uvicorn[standard] | 0.48.0 | BSD-3-Clause | ASGI server |
| httpx | 0.28.1 | BSD-3-Clause | Async HTTP client for all downstream seams |
| Pydantic | 2.13.4 | MIT | Request/response wire models, settings validation |
| MCP Python SDK | 1.27.2 | MIT | MCP `web_search` tool surface |
| Trafilatura | 2.1.0 | Apache-2.0 | HTML-to-Markdown content extraction |

### Semantic Chunking Service (`semantic-chunking-service/requirements.txt`)

| Package | Version | License | Purpose |
|---|---|---|---|
| FastAPI | 0.136.3 | MIT | REST API framework |
| Uvicorn[standard] | 0.48.0 | BSD-3-Clause | ASGI server |
| httpx | 0.28.1 | BSD-3-Clause | HTTP client for embedding server calls |
| NumPy | 2.4.6 | BSD-3-Clause | Similarity matrix computation, cosine similarity |
| Pydantic | 2.13.4 | MIT | Request/response models |

### Dev / Test dependencies

| Package | File | Purpose |
|---|---|---|
| pytest + plugins | `orchestrator/requirements-dev.txt` | Test runner |
| pytest + plugins | `semantic-chunking-service/requirements-dev.txt` | Test runner |

<!-- TODO: run `pip-licenses` against both virtualenvs to generate the full transitive dependency list. -->

## 4. Model Artifacts

These files are not part of the repository. They must be downloaded separately and placed under `models/` before starting the `llamacpp-models` profile.

| Model file | Format | Purpose | License | Source |
|---|---|---|---|---|
| `bge-m3.gguf` | GGUF (GGML) | Text embeddings (1024-dim, multilingual) | MIT | https://huggingface.co/BAAI/bge-m3 |
| `bge-reranker-v2-m3.gguf` | GGUF (GGML) | Cross-encoder passage reranking | MIT | https://huggingface.co/BAAI/bge-reranker-v2-m3 |

Approximate sizes: `bge-m3.gguf` is ~570 MB in Q8 quantization; `bge-reranker-v2-m3.gguf` is ~570 MB in Q8 quantization. Actual sizes depend on quantization level.

> Always verify current model license terms on HuggingFace Hub before downloading weights into a production environment.

When using the `bundled-models` (TEI) profile, model weights are downloaded from HuggingFace Hub inside the container on first start. The same model IDs apply: `BAAI/bge-m3` for embeddings and `BAAI/bge-reranker-v2-m3` for reranking.

## 5. External Runtime Calls

| Destination | When called | Optional | Data sent |
|---|---|---|---|
| SearXNG (`SEARXNG_URL`) | Every search, for each sub-query | No (hard dependency) | Sub-query text, optional time_range, X-Request-ID, X-Real-IP header |
| Crawl4AI (`CRAWL4AI_URL`) | Every search, for each selected URL | Degrades (no pages if unavailable) | URL, crawler config (robots.txt flag), X-Request-ID |
| Chunking service (`CHUNKER_URL`) | Every search, for each crawled page | No (hard dependency) | Page markdown, source URL, title, source_id |
| Embedding server (`EMBEDDING_ENDPOINT`) | Every chunk request with ≥1 segment | Degrades (token-based fallback) | Segment texts, model name |
| Reranker (`RERANKER_ENDPOINT`) | Every search after chunking | Degrades (position ordering if unavailable) | Query text, chunk texts (in batches), model name |
| LLM planner (`LLM_ENDPOINT`) | Every search if configured and `decompose=true` | Yes (IdentityPlanner fallback) | Original query, system prompt |
| SearXNG upstream search engines | Via SearXNG, not directly by Thorondor | Depends on SearXNG config | User query (after SearXNG's routing logic) |
| Crawl target sites | Via Crawl4AI and the SSRF egress proxy | N/A | HTTP GET to discovered URLs |

## 6. Dev / CI Tooling

| Tool | Config | Purpose |
|---|---|---|
| pytest | `orchestrator/tests/`, `semantic-chunking-service/tests/` | Unit and integration tests |
| PowerShell 7+ | `scripts/*.ps1` | Deploy, smoke, and release-guard scripts |
| Bash | `scripts/*.sh` | Linux equivalents of the PowerShell scripts |
| Docker Compose | `docker-compose.yml`, `docker-compose.llamacpp.yml` | Local stack management |
| GitHub Actions | `.github/workflows/release-guard.yml` | CI release guard (checks for uncommitted changes or test failures) |
