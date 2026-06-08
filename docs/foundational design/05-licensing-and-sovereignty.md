# 05 — Licensing & Sovereignty

> **Thorondor** — Semantic Web Search Pipeline. **This is engineering guidance, not
> legal advice.** Verify every license against the exact version/tag you pin before
> distributing or publishing, and consult counsel for your jurisdiction.

The project is designed to be **self-hostable and publishable**. That goal only
holds if the license boundaries are respected — one component (SearXNG) carries
network copyleft, and a couple of model servers have shifted licenses across
versions. This document records the boundaries so they are not discovered the
hard way.

---

## 1. Component licenses

| Component | Ships as | License | Obligation for this project |
|---|---|---|---|
| Orchestrator | First-party | Your choice (MIT/Apache-2.0 recommended) | None — you own it |
| Semantic Chunking Service | First-party | Your choice | None — you own it (see §2 on attribution) |
| **SearXNG** | Bundled container | **AGPL-3.0** | **Network copyleft.** Run unmodified as an opaque dependency. See §3. |
| Crawl4AI | Bundled container | Apache-2.0 | Permissive — attribution/NOTICE only |
| Embedding model server | BYO / bundled | **varies by server** | Verify the server you bundle. See §4. |
| Reranker model server | BYO / bundled | **varies by server** | Verify the server you bundle. See §4. |
| Embedding model (e.g. BGE-M3) | weights | permissive (MIT on the BAAI card) | Verify model card; attribution |
| Reranker model (e.g. bge-reranker-v2-m3) | weights | permissive (MIT/Apache on card) | Verify model card; attribution |
| FastAPI / Uvicorn / httpx / numpy / Pydantic | pip deps | MIT / BSD / Apache-2.0 | Permissive |

The **core profile** (first-party + SearXNG + Crawl4AI) plus permissive Python
deps is clean to self-host and to publish, provided the SearXNG boundary in §3
is honored. The **bundled-models profile** adds model servers whose license you
must confirm (§4).

---

## 2. The chunking algorithm — attribution

The `ClusterSemanticChunker` implements the algorithm described by **Chroma
Research** ("Evaluating Chunking Strategies for Retrieval", July 2024). The
*idea* (dynamic-programming, globally-optimal, reward-maximizing semantic
chunking) is published research; the *implementation* in this project is
first-party and independently written. There is no license obligation from
reusing a published algorithm, but **crediting Chroma Research in your README
and code header is the right thing to do** and signals provenance to users. Do
not represent the algorithm as novel to this project.

---

## 3. SearXNG and the AGPL boundary (read before publishing)

SearXNG is **AGPL-3.0**. The AGPL's distinguishing clause is *network copyleft*:
if you **modify** the software and **make the modified version available to
users over a network**, you must offer those users the corresponding source of
your modified version.

What this means concretely:

- ✅ **Run the official SearXNG image unmodified, on your own network, as an
  opaque dependency that your orchestrator calls.** This does not trigger any
  obligation to release your first-party code. Your orchestrator and chunker are
  separate programs communicating over HTTP; they are not a derivative work of
  SearXNG.
- ✅ **Configure it** via its own `settings.yml` (enabling JSON output, choosing
  engines, adding an API key). Configuration is not modification.
- ⚠️ **Do not fork SearXNG, patch its source, and ship that modified version**
  as part of a hosted product without offering the modified source to your
  users. That is exactly what the AGPL network clause governs.
- ⚠️ If you redistribute the bundle, keep SearXNG as a **referenced upstream
  image**, not a vendored/modified copy, and document that it is AGPL-3.0.

The safe posture: **SearXNG stays a black box you pull and configure, never a
codebase you modify.** Everything in this project is built around that
assumption, which is why discovery is behind a clean `SearchDiscovery` interface
(swap SearXNG for another discovery backend without touching the pipeline).

---

## 4. Model servers — license drift warning

The model *weights* commonly used here (BGE-M3 embeddings, bge-reranker-v2-m3)
are permissively licensed on their model cards — verify the current card.

The *inference servers* are the trap:

- **Hugging Face `text-embeddings-inference` (TEI)** has changed license across
  releases (Apache-2.0 in early versions; a more restrictive custom license was
  applied to later versions). **Pin a tag and verify that tag's `LICENSE`**
  before bundling it in something you distribute.
- **Infinity** (`michaelf34/infinity`) is MIT-licensed and serves both
  OpenAI-compatible embeddings and reranking — a permissive alternative if the
  TEI license does not fit your distribution model.
- **vLLM** is Apache-2.0 and can serve embedding/pooling models.

Because both model endpoints are **bring-your-own** (the bundled servers are an
optional convenience profile, not a hard dependency), you can always choose a
server whose license matches your distribution goals, or point at a server you
already operate. The first-party code never embeds a model.

---

## 5. Web-crawling posture (operational & legal)

This tool fetches publicly accessible web pages on demand. For a publishable,
compliance-facing product:

- **Respect `robots.txt` and rate limits.** Configure Crawl4AI accordingly and
  keep `CRAWL_CONCURRENCY` conservative. The selection gate (stage 4) exists
  partly to keep crawl volume low and polite.
- **Honor site terms of service.** Some sites prohibit automated access; provide
  `DOMAIN_BLOCKLIST` / `exclude_domains` so operators can comply, and an
  allowlist mode for locked-down deployments.
- **The crawled corpus is ephemeral.** Content is chunked, reranked, returned,
  and discarded — there is no persistent web index (a hard non-goal). The only
  optional state is a short-TTL cache. This minimizes data-retention surface for
  privacy regimes (e.g. GDPR) but does **not** by itself make any particular use
  lawful — the operator is responsible for compliance in their jurisdiction and
  for what their agents do with retrieved content.
- **Discovery via API keys.** If you configure SearXNG with a commercial search
  API (e.g. Brave Search API) for reliability, that key is subject to that
  provider's terms — and is the one place the "core" stack reaches an external
  service. Document it as an explicit, optional operator choice.

---

## 6. Sovereignty summary

| Concern | Posture |
|---|---|
| Mandatory external SaaS | **None** in the core stack (besides the websites being searched). Embedding/reranker can be fully local. |
| Where queries/content are processed | Entirely on your hardware; nothing is sent to a hosted "search-for-agents" vendor. |
| Optional outbound | A commercial search API key (discovery reliability) and/or your own remote model endpoints — both operator choices. |
| Data at rest | Ephemeral; optional short-TTL cache only. No persistent corpus. |
| Publishability | First-party code is yours to license; keep SearXNG unmodified (§3) and verify bundled model-server licenses (§4). |

This is the whole point of the project: the capability of a hosted
search-for-agents API, kept inside your own perimeter, assembled from components
whose licenses you can satisfy.
