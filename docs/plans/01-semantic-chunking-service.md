# Semantic Chunking Service — Implementation Plan

> **Execution.** Implement phase-by-phase. Per the workspace workflow, the
> orchestrator spawns an implementation agent per phase, then an independent
> verification agent runs the documented commands before the phase is marked
> `completed`. Tasks are checkboxes; tick them only with evidence.

## Goal

Stand up `semantic-chunking-service/` — a standalone, stateless FastAPI
microservice that turns text/markdown into globally-coherent passages via the
**ClusterSemanticChunker** (DP) algorithm, exactly as specified in
`docs/foundational design/02-semantic-chunking-service.md`, with golden-file
**parity** tests, full **fallback** coverage, and a container image. This is the
first thing built because the orchestrator depends on its `/chunk` contract.

## References

- `docs/foundational design/02-semantic-chunking-service.md` — **authoritative
  source**. §4–§8 contain the *verbatim* implementation of every `chunking/*.py`
  file; §9 has the parity/fallback test approach. Reproduce that source
  logic-for-logic; do not re-derive it.
- `docs/foundational design/03-deployment.md` §2 — the service Dockerfile (verbatim).
- `docs/foundational design/01-architecture.md` §7 — failure posture this service
  must honor (embedding down → degrade, not crash).

## Build & run

- **Containerized:** yes
- **Build command:** `docker build -t thorondor-chunker ./semantic-chunking-service`
- **Test command:** `cd semantic-chunking-service && python -m pytest -v`
- **Local dev:** `python -m pip install -r requirements.txt -r requirements-dev.txt`
- **Run locally:** `EMBEDDING_ENDPOINT=http://host:port EMBEDDING_MODEL=BAAI/bge-m3 uvicorn chunking.app:app --port 8000`

All tests run **offline**: the embedding endpoint is always stubbed/monkeypatched, never called over the network.

---

## Phase 1 — Scaffold, core types, settings
**Status:** completed
**Kind:** logic

### Tasks
- [x] Create the package skeleton: `semantic-chunking-service/chunking/__init__.py`, `semantic-chunking-service/tests/__init__.py`.
- [x] Create `semantic-chunking-service/requirements.txt` verbatim from doc 02 §8 (`fastapi`, `uvicorn[standard]`, `httpx`, `numpy`, `pydantic>=2`).
- [x] Create `semantic-chunking-service/requirements-dev.txt` with `pytest`.
- [x] Create `chunking/base.py` verbatim from doc 02 §4 (`ChunkResult` dataclass + `BaseChunker` Protocol).
- [x] Create `chunking/settings.py` verbatim from doc 02 §8 (`require_env`, `_int_env`, `CHUNKER_MAX_SEGMENTS_DP`, `REWARD_CACHE_MAX_SIZE`).
- [x] Add `tests/conftest.py` that sets dummy embedding env vars at module import time so module imports never raise. Do **not** put these env defaults only in a fixture: `chunking.app` creates `_embedder = EmbeddingFunction()` during import.
  ```python
  import os

  os.environ.setdefault("EMBEDDING_ENDPOINT", "http://localhost:9")
  os.environ.setdefault("EMBEDDING_MODEL", "test-model")
  ```
- [x] Write `tests/test_base.py`:
  ```python
  from chunking.base import ChunkResult

  def test_chunkresult_len_and_repr():
      c = ChunkResult(text="hello world", start_index=0, end_index=11, token_count=2)
      assert len(c) == 11
      assert "tokens=2" in repr(c)
  ```
- [x] Write `tests/test_settings.py` asserting `require_env` raises `RuntimeError` on a blank var and returns a stripped value, and that `_int_env` honors defaults.

### Verification
- [x] `C:\Users\Sergio\AppData\Local\Programs\Python\Python313\python.exe -m pytest tests/test_base.py tests/test_settings.py -v -s -p no:cacheprovider` → **PASS** (4 passed; default `python` lacks pytest, and capture/cache were disabled because pytest could not allocate sandbox temp files).
- [x] `python -c "import chunking.base, chunking.settings"` exits 0.

---

## Phase 2 — Segmenter (`recursive_splitter.py`)
**Status:** completed
**Kind:** logic

### Tasks
- [x] Create `chunking/recursive_splitter.py` verbatim from doc 02 §5 (`RecursiveCharacterTextSplitter`).
- [x] Write `tests/test_recursive_splitter.py` covering the segmenter's contract (default length function = word count):
  ```python
  from chunking.recursive_splitter import RecursiveCharacterTextSplitter

  def test_empty_or_whitespace_returns_empty():
      assert RecursiveCharacterTextSplitter().split_text("") == []
      assert RecursiveCharacterTextSplitter().split_text("   \n  ") == []

  def test_paragraphs_become_separate_segments():
      s = RecursiveCharacterTextSplitter(chunk_size=5)
      out = s.split_text("alpha beta gamma\n\ndelta epsilon zeta")
      assert len(out) >= 2
      # round-trips the source words (separators are kept)
      assert "".join(out).split() == "alpha beta gamma delta epsilon zeta".split()

  def test_oversized_token_runs_force_split_by_size():
      s = RecursiveCharacterTextSplitter(chunk_size=3)
      out = s.split_text("one two three four five six seven eight")
      assert all(len(seg.split()) <= 3 for seg in out)

  def test_word_with_no_separators_is_char_split():
      s = RecursiveCharacterTextSplitter(chunk_size=2)
      out = s.split_text("x" * 50)   # no separators present
      assert out and "".join(out) == "x" * 50
  ```

### Verification
- [x] `C:\Users\Sergio\AppData\Local\Programs\Python\Python313\python.exe -m pytest tests/test_recursive_splitter.py -v -s -p no:cacheprovider` → **PASS** (4 passed; covers happy path, oversize fallback, no-separator char split, empty input).

---

## Phase 3 — Embedding client (`embedding_function.py`)
**Status:** completed
**Kind:** logic

### Tasks
- [x] Create `chunking/embedding_function.py` verbatim from doc 02 §7 (`EmbeddingFunction`, `_get_embedding_endpoint`, `_get_embedding_model`, `DEFAULT_EMBEDDING_BATCH_SIZE`). Keep the carried-over `host:port` limitation noted in doc 02 §7 — **do not** "fix" it here; it is out of scope.
- [x] Write `tests/test_embedding_function.py` using a fake `httpx.Client` so no network is touched:
  ```python
  import httpx, pytest, chunking.embedding_function as ef

  class _Resp:
      status_code = 200
      def __init__(self, payload): self._p = payload
      def json(self): return self._p
      @property
      def text(self): return ""

  class _FakeClient:
      """Records POSTs; returns OpenAI-shaped embeddings echoing input order."""
      posts = []
      def __init__(self, *a, **k): pass
      def __enter__(self): return self
      def __exit__(self, *a): return False
      def post(self, url, json):
          _FakeClient.posts.append(json)
          data = [{"index": i, "embedding": [float(len(t)), 1.0]} for i, t in enumerate(json["input"])]
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
      assert out[3][0] == 4.0           # "dddd" length echoed → order preserved
      assert len(fake_http.posts) == 3  # ceil(5/2) batches

  def test_non_200_raises(monkeypatch):
      class _Err(_FakeClient):
          def post(self, url, json):
              r = _Resp({}); r.status_code = 500
              object.__setattr__(r, "_p", {}); return r
      monkeypatch.setattr(httpx, "Client", _Err)
      with pytest.raises(RuntimeError):
          ef.EmbeddingFunction(model="m", host="h", port="1")(["x"])

  def test_endpoint_parsing_requires_host_and_port(monkeypatch):
      monkeypatch.setenv("EMBEDDING_ENDPOINT", "http://just-host")  # no port
      with pytest.raises(ValueError):
          ef._get_embedding_endpoint()
  ```
  > Note: `_Resp.status_code` must be settable — adjust the `_Err` stub to a simple object exposing `status_code`, `text`, `json()` if the frozen attribute pattern is awkward in your runtime.

### Verification
- [x] `C:\Users\Sergio\AppData\Local\Programs\Python\Python313\python.exe -m pytest tests/test_embedding_function.py -v -s -p no:cacheprovider` → **PASS** (3 passed; batching+order, non-200 → `RuntimeError`, malformed endpoint → `ValueError`).

---

## Phase 4 — ClusterSemanticChunker DP core (`cluster_semantic.py`)
**Status:** completed
**Kind:** logic

### Tasks
- [x] Create `chunking/cluster_semantic.py` verbatim from doc 02 §6 (`ClusterSemanticChunker` plus `MAX_SEGMENTS_FOR_DP = CHUNKER_MAX_SEGMENTS_DP`). Preserve every fallback, the `del`/`gc.collect()` memory discipline, and the LRU reward cache exactly.
- [x] Add a shared deterministic stub embedder to `tests/conftest.py`:
  ```python
  import hashlib, numpy as np
  def fake_embed(texts):
      out = []
      for t in texts:
          h = hashlib.sha256(t.encode()).digest()
          v = np.frombuffer(h, dtype=np.uint8).astype(np.float32)[:16]
          out.append((v / (np.linalg.norm(v) or 1)).tolist())
      return out
  ```
  (Expose it via a fixture or import it directly in the chunker tests.)
- [x] Write `tests/test_cluster_semantic.py`:
  ```python
  import numpy as np, chunking.cluster_semantic as cs
  from chunking.cluster_semantic import ClusterSemanticChunker
  from tests.conftest import fake_embed

  def test_single_segment_returns_one_chunk():
      out = ClusterSemanticChunker(fake_embed, max_chunk_size=400).split_text_with_metadata("one short line")
      assert len(out) == 1

  def test_determinism():
      ch = ClusterSemanticChunker(fake_embed, max_chunk_size=40)
      text = "Alpha beta gamma. " * 50
      a = [c.text for c in ch.split_text_with_metadata(text)]
      b = [c.text for c in ch.split_text_with_metadata(text)]
      assert a == b and len(a) >= 2

  def test_oom_guard_uses_greedy_semantic(monkeypatch):
      monkeypatch.setattr(cs, "MAX_SEGMENTS_FOR_DP", 3)   # force the O(N) path
      out = ClusterSemanticChunker(fake_embed, max_chunk_size=40).split_text_with_metadata("word " * 200)
      assert len(out) >= 1            # produced chunks via greedy-semantic, no OOM

  def test_embedding_failure_falls_back_to_token_chunking():
      def boom(_): raise RuntimeError("embed down")
      out = ClusterSemanticChunker(boom, max_chunk_size=40).split_text_with_metadata("word " * 100)
      assert len(out) >= 1            # token-based fallback still returns usable chunks

  def test_token_offsets_are_within_input():
      text = "Alpha beta gamma. " * 20
      for c in ClusterSemanticChunker(fake_embed, max_chunk_size=40).split_text_with_metadata(text):
          assert 0 <= c.start_index <= c.end_index <= len(text)
  ```
- [x] Write `tests/test_parity.py` — golden-file parity. The test must fail if the golden file is missing; generate the golden file deliberately in the task below, not as a side effect of a passing test.
  ```python
  import json, pathlib
  from chunking.cluster_semantic import ClusterSemanticChunker
  from tests.conftest import fake_embed

  GOLDEN = pathlib.Path(__file__).parent / "golden" / "cluster_semantic@1.json"

  def _run():
      text = "The eagle soared. " * 30 + "\n\n" + "Markets fell sharply. " * 30
      ch = ClusterSemanticChunker(fake_embed, max_chunk_size=60, min_chunk_size=10)
      return [{"text": c.text, "tokens": c.token_count} for c in ch.split_text_with_metadata(text)]

  def test_golden_parity():
      assert GOLDEN.exists(), "Missing golden file; generate it deliberately before running parity."
      out = _run()
      assert out == json.loads(GOLDEN.read_text()), "Chunking drifted from the golden file — re-chunk deliberately, do not silently update."
  ```
- [x] Generate `tests/golden/cluster_semantic@1.json` with a one-off capture command or small helper, eyeball it for sanity, then leave it present in the working tree for review. Do not commit unless the user explicitly asks.

### Verification
- [x] `C:\Users\Sergio\AppData\Local\Programs\Python\Python313\python.exe -m pytest tests/test_cluster_semantic.py tests/test_parity.py -v -s -p no:cacheprovider` → **PASS**: 6 passed; determinism, OOM-guard greedy-semantic path, embedding-failure token fallback, offset invariants, and a present golden file.

---

## Phase 5 — Service shell (`strategies`, `textprep`, `models`, `app`)
**Status:** completed
**Kind:** logic

### Tasks
- [x] Create `chunking/strategies.py`, `chunking/textprep.py`, `chunking/models.py`, `chunking/app.py` verbatim from doc 02 §8.
- [x] Write `tests/test_app.py` with the FastAPI `TestClient`, monkeypatching the module-level embedder so no network is touched:
  ```python
  import pytest
  from fastapi.testclient import TestClient
  import chunking.app as appmod
  from tests.conftest import fake_embed

  class _FakeEmbedder:
      def __call__(self, texts): return fake_embed(texts)
      def health_check(self): return True

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
      assert first["position"] == 0

  def test_unknown_strategy_version_is_400(client):
      r = client.post("/chunk", json={"text": "hi there friend", "strategy_version": "nope@9"})
      assert r.status_code == 400

  def test_web_markdown_preclean_strips_image_lines(client):
      r = client.post("/chunk", json={"text": "Real prose here.\n\n![alt](http://img)\n\nMore prose.", "source_type": "WEB_MARKDOWN"})
      assert "![alt]" not in r.json()["chunks"][0]["text"]

  def test_healthz_reports_embedding(client):
      assert client.get("/healthz").json() == {"status": "ok", "embedding": True}
  ```

### Verification
- [x] `C:\Users\Sergio\AppData\Local\Programs\Python\Python313\python.exe -m pytest tests/test_app.py -v -s -p no:cacheprovider` → **PASS**: 4 passed; `/chunk` happy path + metadata passthrough + `strategy_version` stamping, unknown strategy → 400, WEB_MARKDOWN precleaning, `/healthz` shape.
- [x] `C:\Users\Sergio\AppData\Local\Programs\Python\Python313\python.exe -m pytest -v -s -p no:cacheprovider` (whole suite) → **PASS**: 21 passed, 1 StarletteDeprecationWarning from the installed FastAPI/TestClient stack.

---

## Phase 6 — Containerization
**Status:** in_progress
**Kind:** logic

### Tasks
- [x] Create `semantic-chunking-service/Dockerfile` verbatim from doc 03 §2 (`python:3.12-slim`, copy `chunking/`, healthcheck on `/healthz`, `uvicorn chunking.app:app --port 8000`).
- [x] Confirm the `requirements.txt` install layer matches the deps the code imports (`fastapi`, `uvicorn[standard]`, `httpx`, `numpy`, `pydantic>=2`).

### Verification
- [ ] `docker build -t thorondor-chunker ./semantic-chunking-service` → **succeeds**.
- [ ] `docker run --rm -e EMBEDDING_ENDPOINT=http://localhost:9 -e EMBEDDING_MODEL=test -p 8000:8000 thorondor-chunker` then `curl -s localhost:8000/healthz` → **HTTP 200** with `{"status":"ok","embedding":false}` (200 even with no reachable embedder — `embedding:false` is the expected degraded signal, proving the healthcheck is wired and the app boots).

Verification note: `docker build -t thorondor-chunker ./semantic-chunking-service`
was attempted. Inside the sandbox it failed on Docker buildx lock-file access;
outside the sandbox it failed because `dockerDesktopLinuxEngine` was not running.
Leave this phase `in_progress` until Docker Desktop is available and the build/run
checks pass.
