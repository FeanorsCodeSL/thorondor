# 02 - Chunker Review (semantic-chunking-service)

**Severity tally:** Critical 0 | High 3 | Medium 6 | Low 4

Scope: semantic-chunking-service/chunking/ (cluster_semantic.py, recursive_splitter.py, embedding_function.py, base.py, settings.py, strategies.py, textprep.py, models.py, app.py) and semantic-chunking-service/tests/. Read-only audit. The DP algorithm is credited to **Chroma Research** (ClusterSemanticChunker, July 2024) in the module header (cluster_semantic.py:4) and CLAUDE.md - invariant upheld, not represented as novel.

Verdict in brief: the DP recurrence, reward objective, similarity matrix, all three safety valves, the OOM guard, memory release, the bounded reward LRU, strategy versioning, and the /chunk contract are all implemented correctly and match the design. The findings below are about drift exposure (gaps in the test net that let boundaries change silently), the word-count token proxy vs token_budget semantics, and a handful of edge-case correctness risks. No finding reaches Critical. The High findings are all "a boundary-affecting change could ship green."

---

### CHUNK-1 - Golden-file parity test does not exercise the DP path it claims to protect
- **Severity:** High
- **Category:** Testing
- **File:** semantic-chunking-service/tests/test_parity.py:11-16; golden tests/golden/cluster_semantic@1.json
- **What is wrong:** The single golden case is the string "The eagle soared." repeated 30x, a blank line, then "Markets fell sharply." repeated 30x, with max_chunk_size=60, min_chunk_size=10. Because the text is two blocks of identical repeated sentences, the recursive splitter produces segments whose grouping is dominated by the hard max_chunk_size cap, not by semantic reward - the golden output (48/42/48/42 tokens) is exactly what greedy max-cap packing produces. The fake embedding (conftest.py:10-16, sha256 to 16 bytes) gives near-identical vectors within each repeated block, so the DP reward surface is flat and boundaries are decided by the size constraint alone. The DP distinguishing behaviour - choosing a non-greedy boundary because it raises total intra-chunk similarity - is never asserted. A regression that broke the reward computation, the parent backtrack, or the min_chunk_size branch could still emit this exact golden output.
- **Why it matters:** The golden test is the project stated anti-drift guarantee (prove parity with a deterministic stub embedding function before changing anything else, CLAUDE.md). As written it mainly pins the size-packing path, not the semantic-optimization path. Drift in the core DP could pass.
- **Recommendation:** Add a second golden case with mixed, interleaved content (e.g. alternating eagle/market sentences) and a fake embedder returning clearly separable vectors per topic, sized so more than one admissible segmentation exists under the constraints. Assert the DP picks the topic-coherent boundary, not the greedy one. Keep the existing case as the size-cap golden.
- **Acceptance Criteria:** A golden case exists where the DP boundary differs from _greedy_fallback_chunking boundary on the same segments+lengths, and the parity test pins it.
- **Regression Tests:** golden-file parity (new mixed-topic golden); unit test asserting _dynamic_programming_chunking returns a different grouping than _greedy_fallback_chunking for a constructed similarity matrix.

---

### CHUNK-2 - DP-no-solution fallback (valve 3) has no test; reachable via param overrides
- **Severity:** High
- **Category:** Testing
- **File:** cluster_semantic.py:436-442 (dp[n] == -inf -> _greedy_fallback_chunking); _evaluate_chunk_candidate:500-514
- **What is wrong:** Of the three documented safety valves, valve 1 (OOM -> greedy-semantic) and valve 2 (embedding-failure -> token fallback) have explicit tests (test_cluster_semantic.py:21,29). Valve 3 (DP finds no admissible segmentation) has no test. It is hard to trigger under registered params because the recursive splitter caps each segment at initial_segment_size <= max_chunk_size. But ChunkParams lets a caller override initial_segment_tokens > max_chunk_tokens (models.py:13-17, strategies.py:34-38 apply overrides with no cross-validation), making individual segments exceed max_chunk_size, so every candidate hits chunk_tokens > max_chunk_size (line 502 breaks), dp[n] stays -inf, and valve 3 fires. Untested code on a failure path a public request can reach.
- **Why it matters:** A safety valve never executed in tests is a latent bug surface; the no-solution path bypasses all semantic ordering and is the worst-quality output, so it must be correct and bounded.
- **Recommendation:** Add a unit test forcing dp[n] == -inf (e.g. initial_segment_size=400, max_chunk_size=10) asserting non-empty, token-bounded chunks. Separately validate overrides in resolve_strategy (reject initial_segment_tokens > max_chunk_tokens and min_chunk_tokens > max_chunk_tokens) with HTTP 400, so the no-solution path is only reachable by genuine pathology.
- **Acceptance Criteria:** A forced no-solution test is green; override validation rejects inconsistent params with 400.
- **Regression Tests:** forced-fallback unit test for valve 3; param-validation test (initial > max -> 400).

---

### CHUNK-3 - No memory-bound assertion or container mem_limit guarding the 400 MB default ceiling
- **Severity:** High
- **Category:** Performance
- **File:** cluster_semantic.py:171-183, 220, 260-262; settings.py:22; docker-compose.yml:36-45 (no mem_limit); 03-deployment.md:245
- **What is wrong:** The matrix bound is N^2 * 4 bytes. At the default CHUNKER_MAX_SEGMENTS_DP=10000 that is ~400 MB for the matrix alone (doc 03-deployment.md:245: 10k segments ~ 400 MB); peak is higher because np.dot(emb, emb.T) transiently holds the float32 embedding matrix plus the result, and _calculate_chunk_reward slices sub-matrices. The chunker container has no mem_limit in docker-compose.yml, and there is no test asserting that (a) len(segments) > MAX_SEGMENTS_FOR_DP takes the greedy path before any NxN allocation, or (b) the documented 400 MB ceiling holds. The guard is correct in code - checked at :171 before _compute_similarity_matrix at :226, and the greedy path (:631) only allocates O(N) (adjacent_sims, :691) - but nothing locks this ordering in. A refactor that moved matrix work above the guard would silently reintroduce the OOM.
- **Why it matters:** The OOM guard is a named load-bearing invariant. Cannot-be-bypassed must be enforced by a test, not reviewer inspection, and the documented sizing should be paired with a container limit so a runaway is contained.
- **Recommendation:** (1) Add a unit test monkeypatching MAX_SEGMENTS_FOR_DP low and asserting _compute_similarity_matrix is never called (patch it to raise) while a valid result still returns via the greedy path. (2) Add a memory-bound test: for N just under the cap, assert the matrix is float32 and N*N*4 bytes. (3) Set mem_limit on the chunker in compose sized to the documented peak.
- **Acceptance Criteria:** Above-cap input provably never allocates the NxN matrix; matrix dtype/size asserted; container has an explicit memory limit.
- **Regression Tests:** memory-bound assertions (matrix dtype/byte-size); guard-cannot-be-bypassed test (matrix builder patched to raise above cap).

---

### CHUNK-4 - Token proxy is word count, not the embedding/LLM tokenizer; token_budget is silently word-budget end-to-end
- **Severity:** Medium
- **Category:** Chunker
- **File:** app.py:21-24 (_length = len(text.split())); cluster_semantic.py:95; recursive_splitter.py:49
- **What is wrong:** Every token count is a whitespace word count. max_chunk_tokens=400, min_chunk_tokens=50, and the orchestrator token_budget are therefore word budgets. Real subword tokenizers (the bge-m3 default, docker-compose.yml:43) produce ~1.3-1.6 tokens per English word and far more for code, URLs, CJK, or agglutinative text. A chunk reported as 400 tokens can be 600+ real tokens, so max_chunk_size does not actually bound what a downstream model receives, and the assembler token_budget (the budget-not-count invariant) is systematically under-counted.
- **Why it matters:** The primary assembly control is token_budget; an inaccurate proxy means the contract (token-budgeted set of passages) is honoured only approximately, and the error is content-dependent (worst for code/non-Latin). Known divergence (architecture map D9) - flagged here as a quantified risk.
- **Recommendation:** Either (a) document loudly in the /chunk contract and token_budget docs that token means word and size budgets are approximate, or (b) wire a real tokenizer behind _length (a tiktoken proxy is cheap and tracks subword counts far better). Any swap of _length changes every boundary -> must be a strategy_version bump (cluster-semantic@2) with regenerated goldens (CHUNK-8).
- **Acceptance Criteria:** Token semantics documented as word-based or replaced by a real tokenizer under a new strategy version with new goldens.
- **Regression Tests:** golden-file parity regenerated under the new strategy_version; test asserting token_count of a known code/CJK string is within tolerance of the chosen tokenizer.

---

### CHUNK-5 - Reward objective is size-biased; min_chunk_size is a soft floor the final chunk and small docs may violate
- **Severity:** Medium
- **Category:** Chunker
- **File:** cluster_semantic.py:371-389 (_calculate_chunk_reward); :490-514 (_evaluate_chunk_candidate); :147-158
- **What is wrong:** Reward for a group of k segments is the sum of its k(k-1)/2 pairwise similarities ((sum(sub) - trace)/2, :388). Because real cosine sims are predominantly positive, total reward grows roughly quadratically with chunk size, so the DP is biased toward fewer, larger chunks capped only by max_chunk_size. This is faithful to the Chroma formulation (by design, not a bug), but it means max_chunk_size is the dominant boundary driver. The min_chunk_size handling (:505-507) is correct but soft: a sub-min chunk is permitted when i == n (final chunk) or dp[j] == -inf. A whole document under min_chunk_size also short-circuits to one chunk (:147-158). None of this is wrong, but the docstring (min_chunk_size described as Minimum number of tokens per chunk, :84) overstates it as a hard guarantee.
- **Why it matters:** Callers relying on min_chunk_tokens as a hard floor will be surprised by sub-floor final chunks; auditors of the honors-min_chunk_tokens claim need the soft semantics stated.
- **Recommendation:** Document min_chunk_size as a soft floor (final chunk and degenerate cases exempt) in the docstring and /chunk contract. No code change needed if soft semantics are intended (they match Chroma). Optionally merge a sub-min trailing chunk into its predecessor when the merge stays under max_chunk_size - but that is boundary-affecting -> strategy bump.
- **Acceptance Criteria:** min_chunk_size documented as soft; behaviour matches docs.
- **Regression Tests:** unit test asserting a short single-paragraph doc returns one sub-min chunk (documented behaviour), not an error.

---

### CHUNK-6 - start_index/end_index are approximate and can be wrong on repeated text
- **Severity:** Medium
- **Category:** Chunker
- **File:** cluster_semantic.py:319-345 (_calculate_segment_positions), :560-561 (offsets), :555 (merged text); models.py:31-32
- **What is wrong:** Offsets are recovered by text.find(segment_stripped, current_pos) (:334) and chunk text is rebuilt by joining stripped segments with a single space (:555). Consequences: (1) the chunk text is a whitespace-normalized reconstruction, not a verbatim slice of text[start_index:end_index], so text[start:end] != chunk.text whenever the source had non-single-space whitespace (always for WEB_MARKDOWN with newlines). (2) On repeated/duplicated content, find from current_pos can land on the wrong occurrence, or when a segment is not found (idx == -1, :335) it falls back to current_pos + len(segment) which drifts. test_token_offsets_are_within_input (test_cluster_semantic.py:39-42) only checks 0 <= start <= end <= len(text), not that the slice matches the chunk text. The /chunk response advertises these as if exact (models.py:31-32).
- **Why it matters:** Any consumer that re-slices the original by start_index:end_index (highlighting/citation offsets) gets mismatched text; it is presented as a precise contract field but is best-effort.
- **Recommendation:** Document start_index/end_index as approximate character offsets (not byte-exact, may not round-trip after whitespace normalization), or make the chunk text a verbatim text[char_start:char_end] slice (text-affecting -> strategy bump if it changes text).
- **Acceptance Criteria:** Contract states offsets are approximate, or a test asserts text[start:end] round-trips to chunk text.
- **Regression Tests:** unit test on duplicated-text input asserting offsets are monotonically non-decreasing across chunks.

---

### CHUNK-7 - Embedding seam drops scheme + path and requires an explicit port (diverges from the generic base-URL contract)
- **Severity:** Medium
- **Category:** Chunker
- **File:** embedding_function.py:21-31 (_get_embedding_endpoint), :73 (rebuilds http host:port), :156 (/v1/embeddings appended)
- **What is wrong:** EMBEDDING_ENDPOINT is parsed to (hostname, port) and rebuilt as http://HOST:PORT/v1/embeddings. This forces http (silently downgrades an https endpoint), requires an explicit port (ValueError if absent - so https://api.vendor.com/v1 fails with no port, and http://just-host is rejected, confirmed by test_embedding_function.py:71-75), and drops any path prefix (a gateway at http://gw/embeddings-svc/ loses its path). Every orchestrator client uses full base URLs verbatim; this seam is the lone exception. The design doc acknowledges it (02 sec.7, 03-deployment.md:161-165) and offers a one-line generalization, so it is a known, documented constraint - but it breaks the BYO-seams-stay-HTTP-endpoints invariant for TLS, path-prefixed, or port-implied endpoints.
- **Why it matters:** A common BYO target - a TLS-terminated managed embedding endpoint behind a path prefix and implicit 443 - cannot be configured without modifying code, undermining contract-over-implementation.
- **Recommendation:** Use the full URL: strip a trailing slash from the configured endpoint and POST to ENDPOINT/v1/embeddings (or treat a URL already ending in /embeddings as the full URL). Preserve scheme and path; default the port from the scheme. Behaviour-neutral for the bundled http://embedding:80 default.
- **Acceptance Criteria:** https, implicit-port, and path-prefixed endpoints reach the correct URL with the correct scheme.
- **Regression Tests:** unit tests for https://host/v1 (no explicit port), http://host:8080/prefix, and the existing http://host:port all producing the correct POST URL.

---

### CHUNK-8 - Golden regeneration undocumented; no guard ties a _length/algorithm change to a strategy_version bump
- **Severity:** Medium
- **Category:** Testing
- **File:** strategies.py:13-21; tests/test_parity.py; tests/golden/cluster_semantic@1.json; app.py:34,60
- **What is wrong:** strategy_version is correctly stamped on every chunk (app.py:60) and the response (app.py:64), and unknown versions 400 (app.py:38-39, tested). But there is no documented procedure for regenerating the golden file, and no test asserting the registered cluster-semantic@1 params (max=400/min=50/initial=50) are unchanged - so an edit to StrategyParams defaults, _length, the separators list, or the reward formula would change boundaries while still being version 1, and the parity test would just be edited to match (its own message warns against this but cannot enforce it). The rule that any boundary-altering change is High+ unless gated by a strategy_version bump has no mechanical backstop.
- **Why it matters:** Reproducibility (byte-for-byte with a stubbed embedding) depends on @1 being frozen. Without a pinned-params test and a documented bump ritual, drift is a one-line diff away.
- **Recommendation:** (1) Add a test pinning the cluster-semantic@1 registry entry to exact values, so changing them forces a conscious new entry @2. (2) Add a Regenerating-goldens note (a scripts/gen_golden.py or an update-goldens flag) so updates are deliberate and reviewable. (3) Make the CHUNK-1 mixed-topic golden part of the frozen @1 set.
- **Acceptance Criteria:** Editing @1 params fails a test; golden regeneration is a documented, single, reviewable command.
- **Regression Tests:** strategy-params pin test; golden parity (existing + mixed-topic).

---

### CHUNK-9 - Sync def endpoint runs in the threadpool (correct); shared blocking embedder + default thread cap bound concurrency
- **Severity:** Low
- **Category:** Performance
- **File:** app.py:32-33 (def chunk); app.py:18 (_embedder module-global); embedding_function.py:154 (sync httpx.Client per batch)
- **What is wrong:** chunk is a plain def, so Starlette runs it in the AnyIO worker threadpool - the blocking httpx.Client calls and numpy/DP work do not stall the event loop. Invariant upheld; /healthz is also sync and offloaded. Caveats: (1) the default Starlette threadpool is capped (~40 threads), so high fan-in serializes beyond that; the orchestrator already calls /chunk strictly per-page sequentially (architecture map D8), so per-request concurrency is low. (2) The shared _embedder is stateless except _progress_callback (embedding_function.py:64,87-92), never set in the FastAPI path, so no cross-request contamination today - but a future caller setting it would mutate process-global state shared across threads. Otherwise correctly stateless and horizontally scalable.
- **Why it matters:** Correctness is fine; a throughput/footgun note, not a stall risk.
- **Recommendation:** Keep the sync def. Do not add a set_progress_callback call in the request path (would introduce shared mutable state). If throughput matters, document horizontal scaling (compose replicas) per 03-deployment.md:251.
- **Acceptance Criteria:** No shared mutable per-request state on _embedder; scaling guidance documented.
- **Regression Tests:** a concurrency test issuing N parallel /chunk calls and asserting independent, correct results.

---

### CHUNK-10 - Reward LRU is correct and per-run; document that it must not be hoisted to module scope
- **Severity:** Low
- **Category:** Performance
- **File:** cluster_semantic.py:425-428 (get_reward lru_cache(maxsize=REWARD_CACHE_MAX_SIZE)), :459-464 (cache_clear); settings.py:25
- **What is wrong:** The reward cache is a closure inside _dynamic_programming_chunking (:426), keyed on the segment range, and cache_clear() runs at the end of each DP run (:460). This is correct and safe: the closure captures the run similarity_matrix, so rewards cannot leak across documents, and it is cleared per call - genuinely bounded and stateless per request (no adversarial unbounded growth). The default REWARD_CACHE_MAX_SIZE=100000 entries is generous (small floats, ~MBs, acceptable) and rarely binds because the reverse-and-break window limits distinct ranges.
- **Why it matters:** No correctness/unboundedness issue; documenting the per-call lifetime prevents a future optimization that hoists the cache to module scope (which would break statelessness and mix matrices).
- **Recommendation:** Add a one-line comment that the cache is intentionally per-DP-run (captures similarity_matrix) and must not be hoisted to module/class scope. Optionally lower the default bound to the realistic working set.
- **Acceptance Criteria:** Comment documents per-run lifetime; a test asserts get_reward results never cross two different matrices.
- **Regression Tests:** unit test running DP on two different matrices back-to-back and asserting rewards reflect each matrix (no stale cache).

---

### CHUNK-11 - avg_similarity log hard-codes the diagonal as n; fragile if normalization changes
- **Severity:** Low
- **Category:** Chunker
- **File:** cluster_semantic.py:230
- **What is wrong:** The avg_similarity log line subtracts a literal n for the diagonal and divides by n*(n-1). That holds only because _compute_similarity_matrix unit-normalizes rows (:361-363), making the diagonal 1. The zero-vector guard (norms == 0 -> 1, :362) means a genuine zero embedding has diagonal 0, so the subtraction over-corrects in that edge case. Log-only statistic (no algorithmic effect), so cosmetic - but it couples a display metric to a normalization invariant via a magic constant.
- **Why it matters:** Purely diagnostic; flagged for accuracy and to prevent copy-paste into a load-bearing path.
- **Recommendation:** Use np.trace(similarity_matrix) instead of the literal n, or drop the stat. No behaviour change.
- **Acceptance Criteria:** Diagonal correction uses np.trace, or the line is removed.
- **Regression Tests:** none required (log-only); covered incidentally by the existing determinism test.

---

### CHUNK-12 - Greedy-semantic threshold and single-oversized-segment handling untested for boundary correctness
- **Severity:** Low
- **Category:** Testing
- **File:** cluster_semantic.py:575-605 (_greedy_merge_by_similarity), :698 (25th-percentile), :516-541 (_greedy_fallback_chunking), :668-670 (nested embedding-failure check)
- **What is wrong:** test_oom_guard_uses_greedy_semantic only asserts the result is non-empty - it does not check that the 25th-percentile break threshold splits at low-similarity adjacencies, that chunks respect max_chunk_size, or the min_chunk_size interaction (:592). _greedy_fallback_chunking has an edge case where a single segment alone exceeds max_chunk_size: the current_start < i guard (:526) prevents an empty chunk, so the oversized segment is emitted as its own chunk that violates max_chunk_size (acceptable - cannot split further - but unverified). The greedy-semantic path also re-embeds inside the fallback (:668) and re-checks the embedding-failure valve (:669-670); that nested fallback is untested.
- **Why it matters:** These are the lowest-quality output paths; if a refactor breaks size accounting, only a non-empty check would catch it.
- **Recommendation:** Strengthen the OOM-guard test to assert all greedy-semantic chunks satisfy token_count <= max_chunk_size (except a lone oversized segment) and that a clear topic boundary becomes a chunk boundary. Add a test for _greedy_fallback_chunking with one oversized segment.
- **Acceptance Criteria:** Greedy-semantic and greedy-fallback paths have size-respecting assertions and a boundary-placement assertion.
- **Regression Tests:** forced-fallback unit tests (valve 1 nested embedding failure; greedy size bounds; oversized-segment handling).

---

## Invariants check (chunker scope)

| Invariant | Status | Note |
|---|---|---|
| Loads no embedding model (BYO /v1/embeddings only) | UPHELD | embedding_function.py is pure HTTP; no model weights; requirements.txt has no ML model deps |
| CPU-bound, stateless per call | UPHELD | No persistent state; per-call DP; _embedder stateless in the request path (CHUNK-9) |
| Horizontally scalable | UPHELD | Single shared embedder, no session/corpus; scale via replicas |
| Algorithm credited to Chroma Research, not novel | UPHELD | cluster_semantic.py:4, CLAUDE.md |
| metadata is passthrough, copied onto every chunk | UPHELD | app.py:60 merges req.metadata + strategy_version; tested test_app.py:32 |
| strategy_version stamped on every chunk + response | UPHELD | app.py:60,64; tested test_app.py:33 |
| Unknown strategy -> 400 | UPHELD | app.py:38-39; tested test_app.py:37-39 |
| source_type pre-cleaning light + reversible | PARTIAL | textprep.py: image-line strip is NOT reversible (drops content); blank-line collapse is lossy - minor, noted not as a separate finding |
| Endpoint is sync def -> threadpool, event loop not stalled | UPHELD | app.py:32 (CHUNK-9) |
| Similarity = emb @ emb.T on unit-normalized vectors | UPHELD | _compute_similarity_matrix:359-369 normalizes rows then dots |
| Reward = sum of intra-group pairwise sims | UPHELD | _calculate_chunk_reward:388 (sum(sub) - trace)/2 = upper triangle |
| OOM guard before NxN allocation | UPHELD | :171 precedes _compute_similarity_matrix:226 (not test-locked - CHUNK-3) |
| Matrix + embeddings del + gc.collect() after DP | UPHELD | :260-262 (DP path), :683,693-694 (greedy path) |
| Bounded LRU reward cache | UPHELD | :425 maxsize=REWARD_CACHE_MAX_SIZE, cleared per run (CHUNK-10) |
| Embedding-failure -> greedy token fallback | UPHELD | :206-207, 607-629; tested test_cluster_semantic.py:29 |
| DP-no-solution -> greedy grouping | UPHELD (untested) | :436-442; CHUNK-2 |
| Memory bound N^2*4 bytes matches doc sizing | UPHELD | 03-deployment.md:245 (10k ~ 400 MB); no mem_limit (CHUNK-3) |
