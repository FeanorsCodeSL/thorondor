# 03 — Deployment

> **Thorondor** — Semantic Web Search Pipeline. Image tags / pinned versions below
> are representative — verify against current upstream docs before shipping.

Everything runs as containers under one `docker-compose.yaml`. Two compose
**profiles** decide how self-contained the stack is:

- **`core`** (default) — first-party services + SearXNG + Crawl4AI. You bring
  your own embedding and reranker endpoints.
- **`bundled-models`** — also starts default embedding + reranker servers, so a
  fresh `docker compose --profile bundled-models up` works with **zero external
  dependencies**.

---

## 1. Topology

```
            ┌────────────────────────────────────────────────┐
   :8080 ───▶  orchestrator      (first-party, FastAPI)        │  public
            └───┬───────┬───────┬───────────┬─────────────────┘
                │       │       │           │
                ▼       ▼       ▼           ▼
            searxng  crawl4ai  chunker   reranker        ─┐
            (8080)   (11235)   (8000)    (BYO/8081)        │  internal network
                                  │                        │
                                  ▼                        │
                              embedding (BYO/8082)        ─┘
```

Only the orchestrator port is published. SearXNG, Crawl4AI, the chunker, and the
model servers are reachable only on the internal compose network. Crawl4AI is a
public upstream Dockerized service (`github.com/unclecode/crawl4ai`) consumed
through its self-hosted HTTP API; Thorondor must not vendor or build Crawl4AI
source.

---

## 2. Dockerfiles (first-party services)

### `semantic-chunking-service/Dockerfile`

```dockerfile
FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY chunking/ ./chunking/

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/healthz').status==200 else 1)"

CMD ["uvicorn", "chunking.app:app", "--host", "0.0.0.0", "--port", "8000"]
```

### `orchestrator/Dockerfile`

```dockerfile
FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY orchestrator/ ./orchestrator/

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8080/healthz').status==200 else 1)"

# Serves both the REST API and the MCP server (see 04-api-and-agent-integration.md).
CMD ["uvicorn", "orchestrator.app:app", "--host", "0.0.0.0", "--port", "8080"]
```

---

## 3. `docker-compose.yaml`

```yaml
name: thorondor

x-internal: &internal
  restart: unless-stopped
  networks: [internal]

services:
  orchestrator:
    <<: *internal
    build: { context: ., dockerfile: orchestrator/Dockerfile }
    ports: ["8080:8080"]
    environment:
      SEARXNG_URL:        "http://searxng:8080"
      CRAWL4AI_URL:       "http://crawl4ai:11235"
      CHUNKER_URL:        "http://chunker:8000"
      # BYO model endpoints (overridden in the bundled-models profile below):
      RERANKER_ENDPOINT:  "${RERANKER_ENDPOINT}"
      RERANKER_MODEL:     "${RERANKER_MODEL}"
      # Optional query planner:
      LLM_ENDPOINT:       "${LLM_ENDPOINT:-}"
      LLM_MODEL:          "${LLM_MODEL:-}"
      # Pipeline knobs:
      MAX_URLS:           "${MAX_URLS:-6}"
      CRAWL_CONCURRENCY:  "${CRAWL_CONCURRENCY:-4}"
      CRAWL_PER_HOST_CONCURRENCY: "${CRAWL_PER_HOST_CONCURRENCY:-1}"
      CRAWL_TIMEOUT_S:    "${CRAWL_TIMEOUT_S:-15}"
      CRAWL_RESPECT_ROBOTS_TXT: "${CRAWL_RESPECT_ROBOTS_TXT:-true}"
      DEFAULT_TOKEN_BUDGET: "${DEFAULT_TOKEN_BUDGET:-4000}"
      CACHE_BACKEND:      "${CACHE_BACKEND:-memory}"
      DOMAIN_BLOCKLIST:   "${DOMAIN_BLOCKLIST:-}"
      DOMAIN_ALLOWLIST:   "${DOMAIN_ALLOWLIST:-}"
      ALLOWLIST_ONLY:     "${ALLOWLIST_ONLY:-false}"
      RERANKER_API_KEY:   "${RERANKER_API_KEY:-}"
      LLM_API_KEY:        "${LLM_API_KEY:-}"
      CRAWL4AI_API_KEY:   "${CRAWL4AI_API_KEY:-}"
      CHUNKER_API_KEY:    "${CHUNKER_API_KEY:-}"
    depends_on: [searxng, crawl4ai, chunker]

  chunker:
    <<: *internal
    build: { context: ./semantic-chunking-service, dockerfile: Dockerfile }
    environment:
      EMBEDDING_ENDPOINT: "${EMBEDDING_ENDPOINT}"   # OpenAI /v1/embeddings
      EMBEDDING_MODEL:    "${EMBEDDING_MODEL}"
      EMBEDDING_API_KEY:  "${EMBEDDING_API_KEY:-}"
      CHUNKER_MAX_SEGMENTS_DP: "${CHUNKER_MAX_SEGMENTS_DP:-10000}"
      REWARD_CACHE_MAX_SIZE:   "${REWARD_CACHE_MAX_SIZE:-100000}"

  searxng:
    <<: *internal
    image: searxng/searxng:latest          # AGPL-3.0 — run unmodified, opaque
    volumes:
      - ./searxng:/etc/searxng:ro           # settings.yml: enable json format + engines
    environment:
      SEARXNG_BASE_URL: "http://searxng:8080/"

  crawl4ai:
    <<: *internal
    # Public upstream self-hosted Docker API; keep as image-only dependency.
    image: unclecode/crawl4ai:0.8.9         # Apache-2.0
    environment:
      CRAWL4AI_API_TOKEN: "${CRAWL4AI_API_KEY:-}"
    shm_size: "1g"                          # headless browser needs shared memory

  # ---- bundled-models profile: optional, zero-dependency local defaults ----
  embedding:
    <<: *internal
    profiles: ["bundled-models"]
    image: ghcr.io/huggingface/text-embeddings-inference:latest   # verify tag/flags
    command: ["--model-id", "BAAI/bge-m3"]
    # GPU recommended; a CPU image variant exists for low-volume/dev use.

  reranker:
    <<: *internal
    profiles: ["bundled-models"]
    image: ghcr.io/huggingface/text-embeddings-inference:latest   # serves rerank models too
    command: ["--model-id", "BAAI/bge-reranker-v2-m3"]

networks:
  internal: {}
```

When the `bundled-models` profile is active, point the BYO vars at the bundled
servers (e.g. in a `docker-compose.override.yaml` or a profile-specific `.env`):

```env
EMBEDDING_ENDPOINT=http://embedding:80
EMBEDDING_MODEL=BAAI/bge-m3
RERANKER_ENDPOINT=http://reranker:80
RERANKER_MODEL=BAAI/bge-reranker-v2-m3
```

> **Embedding endpoint shape.** The chunker treats `EMBEDDING_ENDPOINT` as a full
> base URL. It preserves scheme, port, and path, then calls `/v1/embeddings`
> unless the configured endpoint already ends in `/embeddings`. Set
> `EMBEDDING_API_KEY` to send `Authorization: Bearer ...`.

---

## 4. Environment reference

### Orchestrator

| Var | Required | Default | Meaning |
|---|---|---|---|
| `SEARXNG_URL` | yes | — | SearXNG JSON API base URL |
| `CRAWL4AI_URL` | yes | — | Crawl4AI HTTP API base URL |
| `CHUNKER_URL` | yes | — | Chunking service base URL |
| `RERANKER_ENDPOINT` | yes | — | Reranker `/rerank` base URL |
| `RERANKER_MODEL` | yes | — | Reranker model id |
| `LLM_ENDPOINT` | no | _(unset → identity planner)_ | OpenAI `/v1/chat/completions` for query decomposition/expansion |
| `LLM_MODEL` | no | — | Chat model id for the planner |
| `MAX_URLS` | no | `6` | Stage-4 selection cap (URLs crawled per call) |
| `CRAWL_CONCURRENCY` | no | `4` | Max parallel Crawl4AI fetches |
| `CRAWL_PER_HOST_CONCURRENCY` | no | `1` | Max parallel Crawl4AI fetches per target host |
| `CRAWL_TIMEOUT_S` | no | `15` | Per-URL crawl timeout; failures are non-fatal |
| `CRAWL_RESPECT_ROBOTS_TXT` | no | `true` | Passed to Crawl4AI as `check_robots_txt` |
| `DEFAULT_TOKEN_BUDGET` | no | `4000` | Assembly budget when caller omits `token_budget` |
| `CACHE_BACKEND` | no | `memory` | `memory` \| `redis` \| `none` |
| `REDIS_URL` | if redis | — | Cache backend connection |
| `DOMAIN_BLOCKLIST` | no | empty | Comma-separated domains to drop at selection |
| `DOMAIN_ALLOWLIST` | no | empty | Operator allowlist used when `ALLOWLIST_ONLY=true` |
| `ALLOWLIST_ONLY` | no | `false` | Restrict all crawls to `DOMAIN_ALLOWLIST` |
| `*_API_KEY` | no | empty | Optional bearer tokens for SearXNG, Crawl4AI, chunker, reranker, LLM, and embedding seams |

### Chunking service

| Var | Required | Default | Meaning |
|---|---|---|---|
| `EMBEDDING_ENDPOINT` | yes | — | OpenAI-compatible embedding server base URL |
| `EMBEDDING_MODEL` | yes | — | Embedding model id sent in the request body |
| `CHUNKER_MAX_SEGMENTS_DP` | no | `10000` | Above this segment count → greedy-semantic O(N) path |
| `REWARD_CACHE_MAX_SIZE` | no | `100000` | DP reward LRU cache bound |

Provide an `.env.example` enumerating all of the above with safe placeholders.

---

## 5. The reranker contract (BYO + adapter)

The orchestrator's `Reranker` client expects a minimal `/rerank` contract:

```jsonc
// POST {RERANKER_ENDPOINT}/rerank
{ "query": "user query", "documents": ["passage 1", "passage 2", ...], "model": "..." }

// 200
{ "results": [ { "index": 1, "score": 0.91 }, { "index": 0, "score": 0.42 } ] }
```

This matches Hugging Face TEI and Infinity rerank servers. For a server that
exposes a different shape (e.g. a raw cross-encoder scoring endpoint), implement
a thin adapter behind the same `Reranker` interface — the rest of the pipeline
is unaffected. The reranker is the **latency-critical** stage; batch the
`query × passage` pairs in one request and keep `documents` bounded via the
stage-8 pre-filter.

---

## 6. SearXNG configuration

SearXNG is an opaque dependency, but it needs two settings to be useful here
(set in `./searxng/settings.yml`, mounted read-only):

1. **Enable the JSON response format** so the orchestrator can parse results
   programmatically (SearXNG disables non-HTML formats by default).
2. **Prefer API-backed engines for autonomous-volume reliability.** SearXNG's
   default path screen-scrapes upstream engines, which rate-limit/CAPTCHA under
   agent-loop volume. Configuring an API-backed engine (e.g. a Brave Search API
   key) keeps SearXNG as the single discovery abstraction while making it
   reliable. This is an operator choice and does not affect any downstream
   stage — see [`05-licensing-and-sovereignty.md`](05-licensing-and-sovereignty.md).

---

## 7. Resource sizing

| Service | CPU | Memory | GPU | Notes |
|---|---|---|---|---|
| orchestrator | light | low | — | IO-bound coordination |
| chunker | moderate (numpy + DP) | **bursty** | — | Peak set by `CHUNKER_MAX_SEGMENTS_DP`: similarity matrix is `N²·4` bytes (10k segments ≈ 400 MB). Lower the cap to cap memory. |
| searxng | light | low | — | — |
| crawl4ai | moderate | moderate–high | — | Headless browser; set `shm_size`. Memory scales with `CRAWL_CONCURRENCY`. |
| embedding (bundled) | — | — | **recommended** | BGE-M3; CPU works for dev/low volume |
| reranker (bundled) | — | — | **recommended** | bge-reranker-v2-m3; latency-critical |

Scale the chunker and orchestrator horizontally (both stateless). The expensive
shared resources are the GPU model servers; co-locate or point all replicas at
the same embedding/reranker endpoints.

---

## 8. Healthchecks & startup order

- `chunker /healthz` reports embedding-endpoint reachability (`{"embedding": true}`).
- `orchestrator /healthz` should aggregate downstream reachability (SearXNG,
  Crawl4AI, chunker, reranker) and report per-dependency status, so the failure
  posture in [`01-architecture.md`](01-architecture.md) §7 is observable.
- `depends_on` orders startup but does not wait for readiness; the orchestrator
  must tolerate a not-yet-ready dependency and degrade per §7 rather than crash.

## 9. Retry posture

The v1 orchestrator does not automatically retry discovery, crawl, chunker, or
model calls. Timeouts and partial results are surfaced in the response stats and
`reason` field instead. This is intentional: autonomous agents can retry with a
refined query, while the service avoids multiplying load against search engines,
target sites, and BYO model endpoints during outages.
