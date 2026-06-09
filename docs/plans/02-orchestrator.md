# Orchestrator — Implementation Plan

> **Execution.** Phase-by-phase, implementation agent → independent verification
> agent per the workspace workflow. Tasks are checkboxes; tick only with evidence.
> **Depends on Plan 01** for the live `/chunk` contract, but every external
> dependency is faked in tests, so this plan is implementable and fully testable
> on its own.

## Goal

Build `orchestrator/` — the first-party FastAPI app that owns the pipeline state
machine and exposes it two ways: REST `POST /search` and an MCP `web_search`
tool, both delegating to one shared async `run_search(...)`. Every pipeline stage
sits behind a Python `Protocol` with a real implementation and a deterministic
fake, so the cost/quality policy (selection, assembly) and the failure posture
are unit-tested with no network.

## Architecture decisions (lock these once; reused by every phase)

- **Async pipeline.** `run_search` is `async`; the SearXNG/Crawl4AI/chunker/reranker
  clients use `httpx.AsyncClient`. Pure stages are plain sync functions. Tests use
  `pytest` + `anyio` (or `pytest-asyncio`).
- **Dependency injection via a `PipelineDeps` dataclass** holding the 7 stage impls
  plus the resolved config. `run_search(req, deps)` takes it explicitly so tests
  inject fakes; `build_deps_from_settings()` constructs the real ones. No globals
  in the pipeline.
- **Pydantic v2** for the wire models (`models.py`); **plain dataclasses** for
  internal pipeline intermediates (`types.py`) — they never cross the HTTP boundary.

### Wire models — `orchestrator/models.py` (defined in Phase 1, referenced everywhere)

```python
class Passage(BaseModel):     text: str; score: float; token_count: int; citation_id: int
class Citation(BaseModel):    id: int; url: str; title: str; published: str | None = None
class RawMarkdown(BaseModel): citation_id: int; markdown: str
class SearchStats(BaseModel):
    sub_queries: list[str] = []; urls_discovered: int = 0; urls_selected: int = 0
    urls_crawled_ok: int = 0; chunks_produced: int = 0; chunks_reranked: int = 0
    reranked: bool = False; tokens_returned: int = 0; elapsed_ms: int = 0
    reason: str | None = None
class SearchRequest(BaseModel):
    query: str
    token_budget: int | None = None; max_urls: int | None = None; max_passages: int | None = None
    decompose: bool = True; freshness: Literal["day", "week", "month", "year"] | None = None
    domains: list[str] | None = None; exclude_domains: list[str] | None = None
    include_raw_markdown: bool = False
class SearchResponse(BaseModel):
    query: str; passages: list[Passage]; citations: list[Citation]; stats: SearchStats
    raw_markdown: list[RawMarkdown] | None = None
```

### Internal types — `orchestrator/types.py`

```python
@dataclass
class DiscoveryResult: title: str; url: str; snippet: str; engine: str; score: float
@dataclass
class Page:    url: str; title: str; markdown: str
@dataclass
class Chunk:   text: str; token_count: int; source_url: str; title: str; position: int
@dataclass
class ScoredChunk: chunk: "Chunk"; score: float
```

### Stage interfaces — `orchestrator/interfaces.py` (Protocols)

```python
class QueryPlanner(Protocol):     async def plan(self, query: str) -> list[str]: ...
class SearchDiscovery(Protocol):  async def search(self, subquery: str, freshness: str | None = None) -> list[DiscoveryResult]: ...
class SelectionPolicy(Protocol):  def select(self, results: list[DiscoveryResult], max_urls: int, blocklist: set[str], allowlist: set[str] | None = None) -> list[DiscoveryResult]: ...
class ContentExtractor(Protocol): async def extract(self, urls: list[str]) -> list[Page]: ...   # partial-tolerant
class SemanticChunker(Protocol):  async def chunk(self, pages: list[Page]) -> list[Chunk]: ...
class Reranker(Protocol):         async def rerank(self, query: str, chunks: list[Chunk]) -> list[ScoredChunk]: ...
class ResultAssembler(Protocol):  def assemble(self, scored: list[ScoredChunk], token_budget: int, max_passages: int | None) -> tuple[list[Passage], list[Citation]]: ...
```

## References

- `docs/foundational design/01-architecture.md` — §2 pipeline stages, §6 interfaces (the source of the Protocols above), §7 failure posture (the source of the `reason`/`reranked` semantics).
- `docs/foundational design/04-api-and-agent-integration.md` — §1 REST `/search` request/response & empty-result `reason` codes, §2 MCP `web_search` tool + mounting, §4 the agent-surface contract.
- `docs/foundational design/03-deployment.md` — §4 orchestrator env reference, §5 the `/rerank` contract + adapter note.
- `docs/foundational design/02-semantic-chunking-service.md` — §2 the `/chunk` request/response the chunker client speaks.

## Build & run

- **Containerized:** yes
- **Build command:** `docker build -t thorondor-orchestrator -f orchestrator/Dockerfile .`
- **Test command:** `python -m pytest orchestrator/tests -v`
- **Local dev:** `python -m pip install -r orchestrator/requirements.txt -r orchestrator/requirements-dev.txt`

`orchestrator/requirements.txt`: `fastapi`, `uvicorn[standard]`, `httpx`, `pydantic>=2`, `mcp`.
`orchestrator/requirements-dev.txt`: `pytest`, `anyio`.

---

## Phase 1 — Scaffold: models, internal types, interfaces, settings
**Status:** completed
**Kind:** logic

### Tasks
- [ ] Create `orchestrator/__init__.py`, `orchestrator/tests/__init__.py`, `orchestrator/requirements.txt`, `orchestrator/requirements-dev.txt`.
- [ ] Create `orchestrator/models.py` exactly as in "Wire models" above. Import `Literal` from `typing` for the `freshness` enum.
- [ ] Create `orchestrator/types.py` exactly as in "Internal types" above.
- [ ] Create `orchestrator/interfaces.py` exactly as in "Stage interfaces" above.
- [ ] Create `orchestrator/settings.py` reading the orchestrator env (doc 03 §4): `SEARXNG_URL`, `CRAWL4AI_URL`, `CHUNKER_URL`, `RERANKER_ENDPOINT`, `RERANKER_MODEL` (required); `LLM_ENDPOINT`/`LLM_MODEL` (optional → identity planner); `MAX_URLS=6`, `CRAWL_CONCURRENCY=4`, `CRAWL_TIMEOUT_S=15`, `DEFAULT_TOKEN_BUDGET=4000`, `CACHE_BACKEND=memory`, `DOMAIN_BLOCKLIST=""`. Provide a frozen `Settings` dataclass + `load_settings()`.
- [ ] Write `tests/test_models.py`: `SearchRequest(query="x")` applies all documented defaults (`decompose is True`, optionals `None`, `include_raw_markdown is False`); invalid `freshness` is rejected; `SearchStats()` defaults match doc 04 (`reranked is False`, `reason is None`).
- [ ] Write `tests/test_settings.py`: missing required var raises; `DOMAIN_BLOCKLIST="a.com, b.com"` parses to `{"a.com", "b.com"}`; numeric defaults applied when env unset.

### Verification
- [ ] `python -m pytest orchestrator/tests/test_models.py orchestrator/tests/test_settings.py -v` → **PASS** (defaults + env parsing proven).

---

## Phase 2 — Pure stages: merge/dedup, selection, content-dedup, assembly
**Status:** completed
**Kind:** logic

> These four hold the cost/quality policy and have **zero** network. Test them hard.

### Tasks
- [ ] Create `orchestrator/normalize.py` with `normalize_url(url: str) -> str` (lowercase host, strip default ports, strip trailing slash, drop `utm_*`/fragment) — used by merge and citations.
- [ ] Create `orchestrator/merge.py`: `merge_dedup(result_sets: list[list[DiscoveryResult]]) -> list[DiscoveryResult]` — union all sub-query results, dedup by `normalize_url`, keep the highest-scoring instance, return sorted by score desc.
- [ ] Create `orchestrator/selection.py`: `SelectionPolicyImpl.select(results, max_urls, blocklist, allowlist=None)` — drop results whose host ∈ blocklist, drop results outside `allowlist` when provided, then take the top `max_urls` by score. Pure; deterministic tie-break by url.
- [ ] Create `orchestrator/content_dedup.py`: `content_dedup(pages: list[Page]) -> list[Page]` — drop near-duplicate bodies by a normalized-text hash (lowercase, collapse whitespace, hash first N=2000 chars). Keep the first occurrence. (Heuristic by design — YAGNI; document it.)
- [ ] Create `orchestrator/assembly.py`: `ResultAssemblerImpl.assemble(scored, token_budget, max_passages)` — sort by score desc; greedily add passages while cumulative `token_count <= token_budget` (and `<= max_passages` when set); assign `citation_id` per unique `normalize_url(source_url)`; return `(passages, citations)` where citations are deduped and `id`-aligned.
- [ ] Write `tests/test_merge.py`: duplicate URLs across two sub-query sets collapse to one (highest score kept); output sorted desc.
- [ ] Write `tests/test_selection.py`: blocklisted host removed; allowlist keeps only matching hosts; caps to `max_urls`; stable order.
- [ ] Write `tests/test_content_dedup.py`: two `Page`s with whitespace-only differences collapse to one; genuinely different bodies both survive.
- [ ] Write `tests/test_assembly.py`:
  ```python
  from orchestrator.types import Chunk, ScoredChunk
  from orchestrator.assembly import ResultAssemblerImpl

  def _sc(score, tokens, url, pos=0):
      return ScoredChunk(Chunk(text="x " * tokens, token_count=tokens, source_url=url, title="T", position=pos), score)

  def test_budget_truncates_by_tokens_not_count():
      scored = [_sc(0.9, 300, "u1"), _sc(0.8, 300, "u2"), _sc(0.7, 300, "u3")]
      passages, _ = ResultAssemblerImpl().assemble(scored, token_budget=650, max_passages=None)
      assert sum(p.token_count for p in passages) <= 650 and len(passages) == 2

  def test_citations_dedupe_and_align():
      scored = [_sc(0.9, 100, "https://a.test/x"), _sc(0.8, 100, "https://a.test/x")]
      passages, citations = ResultAssemblerImpl().assemble(scored, token_budget=4000, max_passages=None)
      assert len({c.id for c in citations}) == len(citations) == 1
      assert all(p.citation_id == citations[0].id for p in passages)

  def test_ordered_by_score_desc():
      scored = [_sc(0.2, 10, "u1"), _sc(0.9, 10, "u2")]
      passages, _ = ResultAssemblerImpl().assemble(scored, token_budget=4000, max_passages=None)
      assert [p.score for p in passages] == [0.9, 0.2]
  ```

### Verification
- [ ] `python -m pytest orchestrator/tests/test_merge.py orchestrator/tests/test_selection.py orchestrator/tests/test_content_dedup.py orchestrator/tests/test_assembly.py -v` → **PASS**: budget-not-count truncation, citation dedup/alignment, score ordering, URL-dedup, blocklist, allowlist, content dedup.

---

## Phase 3 — Discovery & extraction clients (SearXNG, Crawl4AI)
**Status:** completed
**Kind:** logic

### Tasks
- [ ] Create `orchestrator/clients/searxng_client.py`: `SearxngDiscovery(base_url)` implementing `SearchDiscovery`. `search(subquery, freshness=None)` GETs `{base_url}/search?q=...&format=json`, adds `time_range=<freshness>` when provided, maps each result to `DiscoveryResult` (`title`, `url`, `snippet`=`content`, `engine`, `score`). Uses `httpx.AsyncClient`. Define `class DiscoveryUnavailable(Exception)` and raise it on transport error / non-200 so the pipeline can hard-fail mandatory discovery outages.
- [ ] Create `orchestrator/clients/crawl4ai_client.py`: `Crawl4aiExtractor(base_url, concurrency, timeout_s)` implementing `ContentExtractor`. `extract(urls)` fans out with an `asyncio.Semaphore(concurrency)`, per-URL `timeout_s`; **failures and timeouts are swallowed** (logged, dropped) so the result is partial-tolerant; maps OK responses to `Page(url, title, markdown)`.
- [ ] Write `tests/test_searxng_client.py` with `httpx.MockTransport`:
  ```python
  import httpx, anyio
  from orchestrator.clients.searxng_client import SearxngDiscovery

  def _handler(req):
      return httpx.Response(200, json={"results": [
          {"title": "T", "url": "https://a.test", "content": "snip", "engine": "brave", "score": 1.0}]})

  def test_parses_searxng_json(monkeypatch):
      transport = httpx.MockTransport(_handler)
      real_async_client = httpx.AsyncClient
      monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: real_async_client(transport=transport))
      out = anyio.run(SearxngDiscovery("http://searxng:8080").search, "q")
      assert out[0].url == "https://a.test" and out[0].engine == "brave"
  ```
- [ ] Add SearXNG tests for `freshness` mapping to `time_range` and non-200/transport error → `DiscoveryUnavailable`.
- [ ] Write `tests/test_crawl4ai_client.py`: one URL returns 200 → `Page`; one URL raises/times out → dropped; assert the good page survives and the call does **not** raise (partial tolerance). Assert concurrency is bounded (e.g. a counting semaphore probe never exceeds the configured limit).

### Verification
- [ ] `python -m pytest orchestrator/tests/test_searxng_client.py orchestrator/tests/test_crawl4ai_client.py -v` → **PASS**: JSON parsing; `freshness`→`time_range`; discovery hard-fail exception; partial-crawl tolerance (one failure does not sink the batch); bounded concurrency.

---

## Phase 4 — Chunker, reranker & planner clients
**Status:** completed
**Kind:** logic

### Tasks
- [ ] Create `orchestrator/clients/chunker_client.py`: `ChunkerClient(base_url)` implementing `SemanticChunker`. `chunk(pages)` POSTs each page to `{base_url}/chunk` with `source_type="WEB_MARKDOWN"` and `metadata={"source_url","title"}` (doc 02 §2), flattening responses to `Chunk` objects carrying provenance + `position`. Define `class ChunkerUnavailable(Exception)` and raise it on transport error / non-200 so the pipeline can hard-fail mandatory chunker outages.
- [ ] Create `orchestrator/clients/reranker_client.py`: `RerankerClient(endpoint, model)` implementing `Reranker`. POSTs `{query, documents, model}` to `{endpoint}/rerank`, parses `{"results":[{"index","score"}]}` (doc 03 §5), returns `ScoredChunk`s. Define `class RerankerUnavailable(Exception)`; raise it on transport error / non-200 so the pipeline can degrade gracefully (it must **not** swallow internally).
- [x] Extend `RerankerClient` with configurable request path support (`RERANKER_PATH`) and compatible score parsing for llama.cpp-style reranking responses.
- [ ] Create `orchestrator/clients/planner.py`: `IdentityPlanner` (`plan(q) -> [q]`, the default) and `LlmPlanner(endpoint, model)` that POSTs `/v1/chat/completions` and parses sub-queries; on any error `LlmPlanner` falls back to `[query]` (identity) per doc 01 §7.
- [ ] Write `tests/test_chunker_client.py` (MockTransport): two pages → flattened chunks with correct `source_url`/`title`/`position`; non-200/transport error → `ChunkerUnavailable`.
- [ ] Write `tests/test_reranker_client.py` (MockTransport): scores mapped back by `index`; non-200 → `RerankerUnavailable`.
- [ ] Write `tests/test_planner.py`: `IdentityPlanner.plan("q") == ["q"]`; `LlmPlanner` with a failing transport falls back to `["q"]`.

### Verification
- [ ] `python -m pytest orchestrator/tests/test_chunker_client.py orchestrator/tests/test_reranker_client.py orchestrator/tests/test_planner.py -v` → **PASS**: chunk provenance, chunker hard-fail exception, reranker index-mapping + `RerankerUnavailable` on failure, identity-planner default + LLM fallback.

---

## Phase 5 — Pipeline `run_search` + failure posture
**Status:** completed
**Kind:** logic

### Tasks
- [ ] Create `orchestrator/pipeline.py` with `@dataclass PipelineDeps` (the 7 interface impls + `default_token_budget`, `default_max_urls`, `blocklist`) and `async def run_search(req: SearchRequest, deps: PipelineDeps) -> SearchResponse`. Define `SearchDependencyUnavailable(dependency: str, reason: str)` for mandatory dependency outages. Wire the stages in order (doc 01 §3): plan → discover (parallel sub-queries, passing `req.freshness`) → merge_dedup → select (using `DOMAIN_BLOCKLIST ∪ req.exclude_domains` and `req.domains` as an allowlist) → extract → content_dedup → chunk → rerank → assemble. Populate `SearchStats` at each stage. Honor the **failure posture** (doc 01 §7):
  - SearXNG transport/non-200 (`DiscoveryUnavailable`) → raise `SearchDependencyUnavailable("searxng", "searxng_unavailable")`.
  - SearXNG returns zero results → return empty with `reason="no_results_from_discovery"`.
  - All crawls fail → empty with `reason="all_crawls_failed"`.
  - No chunks after dedup → empty with `reason="no_chunks_after_dedup"`.
  - Chunker transport/non-200 (`ChunkerUnavailable`) → raise `SearchDependencyUnavailable("chunker", "chunker_unavailable")`.
  - `RerankerUnavailable` → wrap chunks in fallback `ScoredChunk`s preserving current order, set `stats.reranked=False`, continue (do not fail).
  - If `req.decompose` is true, call the injected `deps.planner.plan(req.query)`; otherwise use `[req.query]`. Do not type-check for a concrete planner class.
  - If `req.include_raw_markdown` is true, attach `SearchResponse.raw_markdown` for citations that appear in the assembled response; otherwise leave it `None`.
  - Empty/degraded outcomes return **HTTP-200-shaped** responses (empty lists + `reason`), never raise. Mandatory dependency outages raise `SearchDependencyUnavailable` for the API layer to expose as HTTP 503.
- [ ] **Deferred — document, don't build:** the optional cosine **pre-filter** (doc 01 stage 8) and the optional **cache** (`CACHE_BACKEND=redis`, doc 01 §5) are off-by-default optimizations and are intentionally out of scope for this phase. Keep the injection seam so each can be added later without touching stage order; note the deferral in `pipeline.py`.
- [ ] Create `orchestrator/fakes.py` — deterministic fakes for all 7 interfaces (used by pipeline + app tests).
- [ ] Write `tests/test_pipeline.py`:
  ```python
  import anyio
  from orchestrator.models import SearchRequest
  from orchestrator.pipeline import run_search
  from orchestrator import fakes

  def test_happy_path_returns_cited_budgeted_passages():
      deps = fakes.deps()                      # all stages succeed
      resp = anyio.run(run_search, SearchRequest(query="eu ai act 2025"), deps)
      assert resp.passages and resp.citations
      assert resp.stats.reranked is True
      assert sum(p.token_count for p in resp.passages) <= 4000
      assert all(p.citation_id in {c.id for c in resp.citations} for p in resp.passages)

  def test_no_discovery_returns_reason():
      deps = fakes.deps(discovery=fakes.EmptyDiscovery())
      resp = anyio.run(run_search, SearchRequest(query="x"), deps)
      assert resp.passages == [] and resp.stats.reason == "no_results_from_discovery"

  def test_reranker_down_degrades_not_fails():
      deps = fakes.deps(reranker=fakes.DownReranker())
      resp = anyio.run(run_search, SearchRequest(query="x"), deps)
      assert resp.stats.reranked is False and resp.passages   # still returns ordered passages

  def test_all_crawls_failed_reason():
      deps = fakes.deps(extractor=fakes.EmptyExtractor())
      resp = anyio.run(run_search, SearchRequest(query="x"), deps)
      assert resp.stats.reason == "all_crawls_failed"
  ```
- [ ] Add pipeline tests for: `req.exclude_domains` merging with `DOMAIN_BLOCKLIST`; `req.domains` allowlist; `req.freshness` passed to discovery; a custom injected planner is called when `decompose=True`; `include_raw_markdown=True` attaches markdown for returned citations only; `DiscoveryUnavailable` and `ChunkerUnavailable` raise `SearchDependencyUnavailable`.

### Verification
- [ ] `python -m pytest orchestrator/tests/test_pipeline.py -v` → **PASS**: happy-path (cited, budgeted, `reranked=True`); request-scoped filters/freshness/raw-markdown behavior; each degraded path returns the correct `reason`/`reranked=False` without raising; mandatory dependency outages raise `SearchDependencyUnavailable`.

---

## Phase 6 — REST API (`app.py`)
**Status:** completed
**Kind:** logic

### Tasks
- [ ] Create `orchestrator/app.py`: `FastAPI(title="thorondor")`; build `PipelineDeps` once at startup via `build_deps_from_settings()`; `POST /search` validates `SearchRequest`, applies server defaults (`token_budget`→`DEFAULT_TOKEN_BUDGET`, `max_urls`→`MAX_URLS`), calls `run_search`, returns `SearchResponse`, and maps `SearchDependencyUnavailable` to HTTP 503 with `{dependency, reason}` detail. `GET /healthz` aggregates per-dependency reachability (SearXNG, Crawl4AI, chunker, reranker) and returns per-dependency status (doc 04 §1, doc 03 §8).
- [x] Add configurable reranker health path support (`RERANKER_HEALTH_PATH`) and normalize URL joining so `http://reranker:8080/` + `health` probes `http://reranker:8080/health`.
- [ ] Make deps overridable in tests (a module-level `deps` the test monkeypatches, or FastAPI dependency override).
- [ ] Write `tests/test_app_rest.py` (TestClient, fake deps injected): `/search` happy path returns passages+citations+stats; `/search` with `include_raw_markdown=true` includes raw markdown; mandatory dependency outage → 503 with `{dependency, reason}` detail; missing `query` → 422; `/healthz` returns a per-dependency map.

### Verification
- [ ] `python -m pytest orchestrator/tests/test_app_rest.py -v` → **PASS**: `/search` contract (passages/citations/stats), optional raw markdown, 503 mapping for mandatory dependency outage, validation error on missing `query`, `/healthz` per-dependency shape.

---

## Phase 7 — MCP server (`web_search` tool)
**Status:** completed
**Kind:** logic

### Tasks
- [ ] Create `orchestrator/mcp_server.py`: `mcp = FastMCP("thorondor")` and `@mcp.tool() async def web_search(query, token_budget=4000, max_urls=6) -> dict` that constructs `SearchRequest(query=query, token_budget=token_budget, max_urls=max_urls)` and delegates to the shared `run_search(req, deps)` (doc 04 §2). Tool returns the `{passages, citations}` dict shape documented in the tool docstring and shares the same dependency override seam as REST tests.
- [ ] Mount it in `app.py`: `app.mount("/mcp", mcp.streamable_http_app())`.
- [ ] Add a console entry point `thorondor-mcp` that runs `mcp.run(transport="stdio")` for local agents (doc 04 §2).
- [ ] Write `tests/test_mcp.py`: call the `web_search` tool function directly with fake deps and assert it returns a dict with `passages` and `citations` keys; assert `app` exposes a `/mcp` mount. (Confirm the import surface against the pinned `mcp` SDK version — doc 04 header caveat.)

### Verification
- [ ] `python -m pytest orchestrator/tests/test_mcp.py -v` → **PASS**: tool delegates to `run_search` and returns the documented shape; `/mcp` is mounted.
- [ ] `python -m pytest orchestrator/tests -v` (whole suite) → **PASS**.

---

## Phase 8 — Containerization
**Status:** in_progress
**Kind:** logic

### Tasks
- [ ] Create `orchestrator/Dockerfile` based on doc 03 §2, but fix the requirements path for the repo-root build context:
  ```dockerfile
  FROM python:3.12-slim

  WORKDIR /app
  COPY orchestrator/requirements.txt ./requirements.txt
  RUN pip install --no-cache-dir -r requirements.txt

  COPY orchestrator/ ./orchestrator/

  EXPOSE 8080
  HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8080/healthz').status==200 else 1)"

  CMD ["uvicorn", "orchestrator.app:app", "--host", "0.0.0.0", "--port", "8080"]
  ```
- [ ] Ensure `orchestrator/requirements.txt` lists exactly the imported deps (`fastapi`, `uvicorn[standard]`, `httpx`, `pydantic>=2`, `mcp`).

### Verification
- [ ] `docker build -t thorondor-orchestrator -f orchestrator/Dockerfile .` → **succeeds**.
- [ ] `docker run --rm -p 8080:8080 -e SEARXNG_URL=... -e CRAWL4AI_URL=... -e CHUNKER_URL=... -e RERANKER_ENDPOINT=... -e RERANKER_MODEL=... thorondor-orchestrator` boots; `curl -s localhost:8080/healthz` → **HTTP 200** reporting per-dependency status (unreachable deps reported as down, app does **not** crash — proves the degrade-don't-crash startup posture from doc 03 §8).

Implementation report: `orchestrator/` is implemented through the REST and MCP
surfaces. `C:\Users\Sergio\AppData\Local\Programs\Python\Python313\python.exe -m
pytest semantic-chunking-service\tests orchestrator\tests -v -s -p
no:cacheprovider` passed with 64 tests and 1 FastAPI/TestClient deprecation
warning from the installed toolchain. Docker build was attempted, but Docker
Desktop's `dockerDesktopLinuxEngine` daemon is not running, so image build/run
verification remains open.
