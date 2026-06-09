# 06 — Licensing & Sovereignty (as-built review)

> **Engineering guidance, not legal advice.** Every license/tag cited below was confirmed from the source noted, but the operator MUST re-verify each license against the **exact pinned version/tag they ship** and consult counsel for their jurisdiction. License terms change across releases — see LIC-1 and LIC-3 for concrete cases where they already have.

**Severity tally:** 0 Critical | 3 High | 4 Medium | 3 Low | 2 Info

The good news up front: the **AGPL boundary is structurally sound**. SearXNG is pulled as an upstream image, run unmodified, configured only via a read-only `settings.yml`, and called purely over HTTP behind the `SearchDiscovery` Protocol (`searxng_client.py`). `searxng/` tracks **only** `settings.yml` — no fork, no patch, no Dockerfile, no vendored source. Chroma Research is credited accurately in the chunker source header. The hard non-goals (no persistent index, stateless per call, no mandatory SaaS) hold in code. The remaining risk is almost entirely **publishability hygiene**: floating `:latest` tags defeat license verification, there is no `LICENSE` for the first-party code and no `NOTICE`/attribution file for permissive deps, and the README omits both the Chroma credit and the sovereignty/outbound disclosures that the design doc promises a publisher would advertise.

---

## Licensing findings

### LIC-1 — SearXNG bundled at floating `:latest`, defeating the AGPL "pin a verified version" posture
- **Severity:** High
- **Category:** Licensing
- **Evidence:** `docker-compose.yml:49` — `image: searxng/searxng:latest`.
- **Problem:** SearXNG is **AGPL-3.0** (confirmed from the upstream repo; operator must re-verify against the digest they actually pull). The boundary is respected *structurally* (unmodified, opaque, HTTP-only — see LIC-2), but `:latest` means the bundle pulls whatever upstream publishes at deploy time. A redistributed Compose file with `:latest` is not reproducible: two operators get different SearXNG builds, and you cannot point to "the AGPL-3.0 version we ship" because there isn't one fixed version.
- **Impact:** Undermines the "referenced upstream image, run unmodified" claim that keeps the first-party code out of AGPL network-copyleft scope. If upstream ever ships a tag whose terms differ, an unpinned bundle inherits it silently. Also a reproducibility/supply-chain gap.
- **Recommendation:** Pin to a specific release tag and ideally a digest, e.g. `searxng/searxng:2025.x.x@sha256:...`. Record the pinned tag + that it is AGPL-3.0 in a NOTICE/README licensing section.
- **Acceptance Criteria:** `searxng` image is pinned to a tag (preferably digest); the pinned tag is documented as AGPL-3.0 in a NOTICE or README section; CI/release notes reference the exact tag.

### LIC-2 — (Confirmation, not a defect) SearXNG AGPL network-copyleft boundary is honored in code
- **Severity:** Info
- **Category:** Licensing
- **Evidence:** `docker-compose.yml:47-53` (image + read-only `./searxng:/etc/searxng:ro` mount, no `build:` context); `searxng/settings.yml` is the only tracked file under `searxng/` (`git ls-files searxng/`); `orchestrator/clients/searxng_client.py:11-38` calls SearXNG only via `httpx` GET `/search` behind the `SearchDiscovery` Protocol (`interfaces.py:12-13`).
- **Problem:** None — this is the safe posture. SearXNG runs as an opaque, unmodified upstream image; configuration via its own `settings.yml` is configuration, not modification; the orchestrator is a separate program communicating over HTTP, not a derivative work. Discovery is swappable without touching the pipeline.
- **Impact:** Confirms the AGPL trigger (modify + serve over network) is not pulled. Keep it this way.
- **Recommendation:** Preserve the invariant. Never add a `build:` context, Dockerfile, or vendored/patched SearXNG source. If discovery customization is ever needed, do it through `settings.yml` or by swapping the `SearchDiscovery` backend — not by patching SearXNG.
- **Acceptance Criteria:** No `build:` stanza or Dockerfile for the `searxng` service ever appears; `searxng/` contains config only; release checklist asserts this.

### LIC-3 — Bundled model servers (TEI, llama.cpp) at `:latest`; doc's TEI license claim is imprecise
- **Severity:** High
- **Category:** Licensing
- **Evidence:** `docker-compose.yml:63` and `:69` — `image: ghcr.io/huggingface/text-embeddings-inference:latest` (embedding + reranker, `bundled-models` profile); `docker-compose.llamacpp.yml:18,38` and `.env.llamacpp.example:4` — `ghcr.io/ggml-org/llama.cpp:server` (a floating tag). Doc claim: `docs/foundational design/05-licensing-and-sovereignty.md:85-88` says TEI "has changed license across releases (Apache-2.0 in early versions; a more restrictive custom license was applied to later versions)."
- **Problem:** Two issues. (1) **Unpinned tags defeat the doc's own core advice** ("Pin a tag and verify that tag's `LICENSE` before bundling it in something you distribute"). (2) The doc's TEI statement is **imprecise and backwards on direction**: per upstream, `text-embeddings-inference` (TEI) is currently **Apache-2.0** and was re-opened to Apache-2.0 (GitHub issue #232, "Text Embeddings Inference is now Open Source!"). It is **TGI** (`text-generation-inference`) that moved to the restrictive HFOIL 1.0 at v1.0 and stayed there. TEI had a brief HFOIL window before reverting; the doc conflates the two and implies TEI *became* restrictive, when it became *less* restrictive. `llama.cpp` is MIT (upstream `LICENSE`); the doc does not mention llama.cpp's license at all even though `docker-compose.llamacpp.yml` wires it. *(All licenses confirmed from upstream repos as of this review; operator MUST re-verify against the exact pinned tag — that is precisely the point.)*
- **Impact:** A distributor reading the doc may avoid TEI for the wrong reason, or ship a `:latest` TEI/llama.cpp whose terms they never verified. The doc's safety guidance is sound but its factual TEI claim could mislead a license decision.
- **Recommendation:** (a) Pin TEI to a specific tag and verify that tag's `LICENSE` is Apache-2.0 before distribution; pin `llama.cpp:server` to a release tag and note MIT. (b) Correct doc section 4: TEI is currently Apache-2.0 (re-opened per issue #232); the HFOIL drift is TGI; add llama.cpp = MIT to the table. (c) Keep both as optional convenience profiles only (already true — see LIC-4).
- **Acceptance Criteria:** Every bundled model-server image is tag-pinned; each pinned tag's `LICENSE` is recorded (TEI Apache-2.0, llama.cpp MIT, or whichever server is wired); doc section 4 distinguishes TEI (Apache-2.0) from TGI (HFOIL) and lists llama.cpp.

### LIC-4 — (Confirmation) First-party code embeds no model; bundled servers are an optional profile
- **Severity:** Info
- **Category:** Licensing
- **Evidence:** `embedding_function.py:1-8` (calls any `/v1/embeddings` server — "vLLM, TEI, Infinity, Ollama, ..."); BYO wiring env to Settings to `build_deps_from_settings` (per architecture map section 4); `docker-compose.yml:60-70` gate embedding/reranker behind `profiles: ["bundled-models"]`; llama.cpp behind `profiles: ["llamacpp-models"]`. No weights are vendored in first-party images (Dockerfiles copy only `orchestrator/` and `chunking/`).
- **Problem:** None — model endpoints are genuinely bring-your-own; bundled servers are not a hard dependency. This is what lets an operator pick a server whose license fits.
- **Impact:** Keeps the model-server license question entirely in the operator's hands.
- **Recommendation:** Preserve. Never embed weights in first-party images; keep model servers behind profiles.
- **Acceptance Criteria:** First-party Dockerfiles never `COPY`/download model weights; model servers remain profile-gated optional services.

### LIC-5 — No `LICENSE` file for the first-party code (orchestrator + chunker)
- **Severity:** High
- **Category:** Licensing
- **Evidence:** No `LICENSE`/`COPYING` tracked anywhere in the repo (`git ls-files | grep -iE 'license|copying'` -> no matches). Doc `05-licensing-and-sovereignty.md:19-20` recommends "Your choice (MIT/Apache-2.0)."
- **Problem:** The first-party services have **no declared license**. By default, "no license" means all rights reserved — the opposite of the project's stated open-source/publishable goal. You cannot publish a sovereign, self-hostable OSS tool without naming its license.
- **Impact:** Blocks publication. Contributors and operators have no grant; the AGPL "separate programs over HTTP" argument is cleaner when your own code carries an explicit permissive license (MIT/Apache-2.0) rather than being unlicensed.
- **Recommendation:** Add a top-level `LICENSE` (MIT or Apache-2.0 per the doc's recommendation) covering `orchestrator/` and `semantic-chunking-service/`. State it in the README.
- **Acceptance Criteria:** A `LICENSE` file exists at repo root; README names the first-party license; the choice is compatible with running AGPL SearXNG as a separate networked service (any permissive license is — the boundary is process/network separation, not license compatibility).

### LIC-6 — No `NOTICE`/attribution file for permissive bundled and pip dependencies
- **Severity:** Medium
- **Category:** Licensing
- **Evidence:** No `NOTICE`/`AUTHORS`/attribution file tracked (`git ls-files` -> none). Permissive deps in play: Crawl4AI (`docker-compose.yml:57`, `unclecode/crawl4ai:latest`, Apache-2.0 — re-verify pinned tag); FastAPI/Uvicorn/httpx/Pydantic/numpy/`mcp` (`orchestrator/requirements.txt`, `semantic-chunking-service/requirements.txt` — MIT/BSD/Apache-2.0). Doc table `05-...:22,27` lists these as "attribution/NOTICE only."
- **Problem:** Apache-2.0 requires preserving copyright/license notices and any upstream `NOTICE` contents that apply to the exact artifact; Crawl4AI and Apache-2.0 pip deps must be checked tag-by-tag before redistribution. There is no central `NOTICE`/third-party-notices file aggregating these credits, and the doc explicitly flags "attribution/NOTICE only" as the obligation — yet no such file exists.
- **Impact:** Redistributing the bundle without the required Apache-2.0 attributions is a license-compliance gap for a publishable artifact.
- **Recommendation:** Add a `NOTICE` (or `THIRD-PARTY-NOTICES.md`) listing each bundled component and pip dependency with pinned version, license, upstream link, and any required upstream `NOTICE`/attribution text, including Crawl4AI (Apache-2.0), SearXNG (AGPL-3.0, as a referenced upstream image), TEI/llama.cpp (whichever bundled), and the Python deps.
- **Acceptance Criteria:** A `NOTICE`/third-party-notices file enumerates bundled images + pip deps with licenses; required notices for the exact pinned Crawl4AI artifact are preserved; the file is referenced from the README.

### LIC-7 — Chroma Research credited in code, but **missing from the README**
- **Severity:** Medium
- **Category:** Licensing
- **Evidence:** Credit present and accurate in `semantic-chunking-service/chunking/cluster_semantic.py:4-6` ("implements the ClusterSemanticChunker algorithm from Chroma Research (July 2024)...") and `:17` ("from Chroma Research"). The project `CLAUDE.md` and doc `05-...:38-45` both require crediting Chroma in **README and code header**. `README.md` (full file read) contains **no** mention of Chroma Research or the chunking-strategies paper.
- **Problem:** The code header satisfies half the obligation; the README — the public-facing provenance surface — does not credit Chroma. The CLAUDE.md instruction explicitly says README *and* code header.
- **Impact:** Provenance is under-disclosed publicly; risks the algorithm appearing novel to a reader who only sees the README. (No *license* obligation — published algorithm — but the project committed to the credit.)
- **Recommendation:** Add a short "Credits / Attribution" section to `README.md` crediting Chroma Research, "Evaluating Chunking Strategies for Retrieval" (July 2024), for the ClusterSemanticChunker algorithm, stating the implementation is first-party and independently written.
- **Acceptance Criteria:** README contains a Chroma Research credit naming the paper and date, and does not represent the algorithm as novel.

### LIC-8 — `RecursiveCharacterTextSplitter` derives from LangChain naming/design without attribution
- **Severity:** Low
- **Category:** Licensing
- **Evidence:** `semantic-chunking-service/chunking/recursive_splitter.py:15` class `RecursiveCharacterTextSplitter` with the LangChain-characteristic separator hierarchy (`:24-34`: paragraph/line/sentence/word/character cascade). The name and separator-cascade design match LangChain's text splitter of the same name (LangChain is MIT — re-verify).
- **Problem:** The class name and approach are strongly associated with LangChain. Even if independently written, the identical name + design pattern reads as derived. LangChain's MIT license is attribution-only, so the obligation (if it is a derivation) is light, but it is undisclosed.
- **Impact:** Minor provenance/attribution gap; no copyleft risk.
- **Recommendation:** If the implementation was influenced by LangChain, add a one-line credit in the module docstring (and the NOTICE from LIC-6). If it is genuinely independent, the distinct origin is worth a note to avoid the appearance of an unattributed port.
- **Acceptance Criteria:** `recursive_splitter.py` docstring and/or NOTICE clarifies the splitter's provenance (LangChain-inspired/MIT, or independently written).

---

## Sovereignty findings

### SOV-1 — Sovereignty / optional-outbound disclosures absent from the README
- **Severity:** Medium
- **Category:** Sovereignty
- **Evidence:** `docs/foundational design/05-...:118-133` documents the outbound surface (a commercial search API key in SearXNG `settings.yml`; remote model endpoints) and the sovereignty summary. `searxng/settings.yml:12-13` comments that operators "can configure API-backed engines here (for example Brave Search with an API key)." But `README.md` (the artifact a publisher ships and an operator reads) contains **no** sovereignty section and **no disclosure** of these optional outbound calls. The only outbound mention in README is the generic model-host examples in `.env.example:26-32`.
- **Problem:** An operator running Thorondor as a "sovereign, no mandatory SaaS" tool cannot tell from the README what optionally phones home. The two optional egress points (commercial search API for discovery reliability; remote BYO model endpoints) are operator choices that must be disclosed so "sovereign" is an informed claim.
- **Impact:** The headline sovereignty promise is under-substantiated at the public surface; an operator could enable a Brave API engine without realizing it is the one place the "core" stack reaches an external vendor.
- **Recommendation:** Add a "Sovereignty & outbound network" section to `README.md` summarizing doc section 5/6: core stack reaches only the websites being searched; optional outbound = (1) commercial search API key in `settings.yml`, (2) remote model endpoints — both explicit operator choices; data at rest is ephemeral.
- **Acceptance Criteria:** README discloses the two optional outbound paths as operator choices and states the core stack has no mandatory external SaaS.

### SOV-2 — (Confirmation) Hard non-goals hold: no persistent index, stateless per call, evidence not prose
- **Severity:** Info
- **Category:** Sovereignty
- **Evidence:** Architecture map section 7 — no cache class, no vector store, no persistent corpus; `run_search` builds a fresh `SearchStats()` per call (`pipeline.py:56`); only process-global state is lazily-built singleton deps and the per-process embedder. `CACHE_BACKEND`/`REDIS_URL` are dead settings (`.env.example:22-23`) — no cache is implemented (see SOV-3). Response is passages + citations + provenance, no summarization LLM hop (map section 1 stage 11, section 5).
- **Problem:** None — the sovereignty-relevant invariants (no persistent index; ephemeral data; evidence not prose) are upheld in code, which is exactly what a publisher would advertise.
- **Impact:** Supports the "search tool, not knowledge base" and GDPR-minimal-retention claims.
- **Recommendation:** Preserve. Do not add a vector store to the hot path.
- **Acceptance Criteria:** No persistent corpus/vector store is introduced; per-call statelessness remains.

### SOV-3 — Documented "optional short-TTL cache" does not exist; sovereignty doc overstates data-at-rest design
- **Severity:** Low
- **Category:** Sovereignty
- **Evidence:** `05-...:114,132` ("The only optional state is a short-TTL cache"); `.env.example:22-23` expose `CACHE_BACKEND`/`REDIS_URL`. Architecture map section 7 + D1: no cache class, no injection point, no SearXNG/crawl caching anywhere; `CACHE_BACKEND`/`REDIS_URL` are dead settings.
- **Problem:** A doc/code drift that cuts in the *safe* direction for sovereignty (even less data at rest than documented), but it is still inaccurate: the doc and `.env.example` advertise a cache that isn't there, and `REDIS_URL` implies optional Redis egress that no code uses.
- **Impact:** Low — over-promises a feature; could mislead an operator into thinking a TTL cache is bounding retention when nothing is cached at all. Dead `REDIS_URL` could confuse a sovereignty audit (looks like an external dependency that doesn't exist).
- **Recommendation:** Either implement the documented short-TTL cache, or mark the cache as "not yet implemented" in the doc and remove/clearly comment the dead `CACHE_BACKEND`/`REDIS_URL` settings until then. Whichever, keep the README sovereignty section (SOV-1) accurate to code.
- **Acceptance Criteria:** Cache state in docs/`.env.example` matches code reality; no dead env var implies phantom outbound/at-rest state.

### SOV-4 — Crawl client sets no robots.txt / politeness controls; doc promises "respect robots.txt"
- **Severity:** Medium
- **Category:** Sovereignty (crawl posture / publishability)
- **Evidence:** `orchestrator/clients/crawl4ai_client.py:18-38` posts `{"url": url}` to Crawl4AI `/crawl` with **no** robots-respect or rate-limit option in the request; concurrency is the only throttle (`CRAWL_CONCURRENCY`, default 4, `docker-compose.yml:26`). Doc `05-...:106-111` states a publishable product must "Respect `robots.txt` and rate limits" and "Honor site terms of service," and that the selection gate exists to keep crawl polite. The selection/blocklist gate is implemented (`selection.py`, `DOMAIN_BLOCKLIST`), but no robots.txt honoring is wired into the crawl request.
- **Problem:** The "crawl politely / respect robots.txt" promise that a compliance-facing publisher would advertise is not enforced in the extraction call. Whether robots is honored depends entirely on Crawl4AI defaults, which the request does not set.
- **Impact:** Operational/compliance exposure for a publishable crawler-adjacent tool; the documented posture is not demonstrably enforced. (Note: invariant "not a crawler framework" still holds — selection caps volume — but politeness is under-enforced.)
- **Recommendation:** Pass an explicit robots-respect / rate-limit option to Crawl4AI in the `/crawl` request (per Crawl4AI's API — verify the exact flag for the pinned version), or document clearly that robots compliance is delegated to Crawl4AI's default and which default that is. Keep `CRAWL_CONCURRENCY` conservative.
- **Acceptance Criteria:** The crawl request explicitly honors robots.txt (or the README states the delegated default and verified behavior); the documented polite-crawl posture is demonstrably enforced or accurately scoped.

---

## Publishability checklist (pre-release)

- [ ] **SearXNG unmodified + documented** — image stays a referenced upstream image, no `build:` context/Dockerfile/vendored source; `searxng/` is config-only; pin the tag (LIC-1) and document it as AGPL-3.0 in NOTICE/README. *(Boundary confirmed clean today — LIC-2.)*
- [ ] **SearXNG tag pinned** — replace `searxng/searxng:latest` with a specific tag/digest (LIC-1).
- [ ] **Bundled model-server tag pinned + LICENSE verified** — pin TEI and llama.cpp images (and any other wired server) to specific tags; record each pinned tag's LICENSE (TEI Apache-2.0, llama.cpp MIT — re-verify the pinned tag) (LIC-3).
- [ ] **Correct the TEI claim in doc section 4** — TEI is currently Apache-2.0 (re-opened, issue #232); the HFOIL drift was TGI; add llama.cpp = MIT (LIC-3).
- [ ] **First-party LICENSE chosen** — add a root `LICENSE` (MIT/Apache-2.0) and name it in the README (LIC-5).
- [ ] **NOTICE / attribution for permissive deps** — add `NOTICE`/third-party-notices covering Crawl4AI (Apache-2.0), the Python deps, the bundled servers, and the AGPL SearXNG reference; preserve any required upstream `NOTICE`/attribution text for exact pinned artifacts (LIC-6).
- [ ] **Chroma attribution in README** — credit Chroma Research (paper + July 2024); not novel (LIC-7). *(Code header already correct.)*
- [ ] **Splitter provenance note** — clarify `RecursiveCharacterTextSplitter` origin (LangChain-inspired/MIT or independent) (LIC-8).
- [ ] **Optional-outbound disclosures present** — README section: commercial search API key and remote model endpoints are explicit operator choices; core stack reaches only searched sites (SOV-1).
- [ ] **Pin Crawl4AI image tag** — replace `unclecode/crawl4ai:latest` with a pinned tag for reproducibility + license stability (covered under LIC-6 NOTICE work).
- [ ] **Cache claim matches code** — implement the short-TTL cache or mark it unimplemented and neutralize dead `CACHE_BACKEND`/`REDIS_URL` (SOV-3).
- [ ] **Polite-crawl posture enforced or scoped** — wire robots.txt honoring into the crawl request or document the delegated Crawl4AI default (SOV-4).
- [ ] **Non-goals preserved** — no persistent index / vector store added to the hot path; stateless per call retained (SOV-2 confirmed — keep it).

> Re-verification reminder: SearXNG (AGPL-3.0), Crawl4AI (Apache-2.0), TEI (Apache-2.0 on `main`), llama.cpp (MIT), and the Python deps were all confirmed from upstream sources at the time of this review, but licenses change across releases. Confirm each against the **exact tag you pin** before distributing, and consult counsel for your jurisdiction.
