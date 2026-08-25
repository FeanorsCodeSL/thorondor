# Third-Party Notices

This file records the third-party components intentionally referenced by the
Thorondor source distribution. It is engineering inventory, not legal advice.
The repository does not vendor Python wheels, third-party container image
layers, or model weights; binary or container distributors should attach their
own generated SBOM for the exact artifacts they ship.

## Runtime Images

| Component | Runtime reference | License | Upstream |
|---|---|---|---|
| SearXNG | `searxng/searxng@sha256:11a9b34cdc0b1ec2b991470a2762ecb5a1a531898289fb51dcd015260450729e` | AGPL-3.0 | https://github.com/searxng/searxng |
| Crawl4AI | `unclecode/crawl4ai@sha256:bd36741e7bdd35ddc1a05d9183e1d6d8cefb61dd640d944a25d026b76e917690` | Apache-2.0 | https://github.com/unclecode/crawl4ai |
| Hugging Face Text Embeddings Inference | revision `4150561`, `ghcr.io/huggingface/text-embeddings-inference@sha256:af92a3852c965393cbdd111865c3a72445d2b430c7daf84269ffdb5cf178f4eb` | Apache-2.0 | https://github.com/huggingface/text-embeddings-inference |
| llama.cpp server | build `b10454`, `ghcr.io/ggml-org/llama.cpp:server@sha256:2244d2b223b49912c7e0c31b4a3654e5e62cf19899f1f36ad30e785eac171a31` | MIT | https://github.com/ggml-org/llama.cpp |
| Python base image | `python:3.13-slim@sha256:7e3a6aca9d74f93cca21a91d86a8dad8c34749afd5b4a98ee481c9c47b9f5ed4` | Python Software Foundation License + bundled OS package licenses | https://hub.docker.com/_/python |

### SearXNG — AGPL-3.0 notice

SearXNG is consumed as an **unmodified upstream image** and configured through
`searxng/settings.yml`; it is not built, vendored, patched, or distributed in
this repository. The Compose stack references the official Docker Hub image.

AGPL-3.0 requires that the full source of a modified version be available to
users who interact with the program over a network. Because Thorondor does not
modify or redistribute SearXNG, operator obligation is limited to ensuring the
upstream image remains unmodified. Operators who build a custom SearXNG image
must comply with AGPL-3.0's distribution terms. See
https://www.gnu.org/licenses/agpl-3.0.html for the full license text.

### Crawl4AI — Apache-2.0

Crawl4AI is consumed as a **public upstream self-hosted Docker API over HTTP**.
Its source is not vendored or patched in this repository. Apache-2.0 license:
https://www.apache.org/licenses/LICENSE-2.0

### Production mirroring note

`docker-compose.production.yml` keeps SearXNG and Crawl4AI as image-reference
variables so operators can mirror the pinned upstream images into GHCR when a
production host is not allowed to pull Docker Hub directly. Mirroring an
unmodified image does not make it first-party Thorondor source; keep upstream
license notices, source-availability obligations, and generated SBOMs attached
to the exact mirrored artifacts.

### Hugging Face Text Embeddings Inference — Apache-2.0

Referenced image license verified against upstream LICENSE file at time of pin.
Apache-2.0: https://www.apache.org/licenses/LICENSE-2.0

### llama.cpp — MIT

MIT license: https://github.com/ggml-org/llama.cpp/blob/master/LICENSE

## Direct Runtime Python Dependencies

| Package | Version | License | SPDX | Used by |
|---|---:|---|---|---|
| FastAPI | 0.141.1 | MIT | MIT | orchestrator, chunker |
| Uvicorn | 0.52.1 | BSD-3-Clause | BSD-3-Clause | orchestrator, chunker |
| httpx | 0.28.1 | BSD-3-Clause | BSD-3-Clause | orchestrator, chunker |
| Pydantic | 2.13.4 | MIT | MIT | orchestrator, chunker |
| MCP Python SDK | 2.0.0 | MIT | MIT | orchestrator MCP surface, `thorondor-mcp` |
| Textual | 8.2.8 | MIT | MIT | `thorondor` Textual configurator |
| Rich | 15.0.0 | MIT | MIT | CLI and TUI formatting |
| Trafilatura | 2.2.0 | Apache-2.0 | Apache-2.0 | orchestrator HTML-to-Markdown extraction |
| NumPy | 2.5.1 | BSD-3-Clause + bundled permissive notices | BSD-3-Clause | chunker |

Transitive Python dependencies are installed from the runtime and development
lock files beside each service's direct requirement files. Generate an SBOM for
the exact binary or container artifact before distribution.

### FastAPI — MIT

MIT license: https://github.com/fastapi/fastapi/blob/master/LICENSE

### Uvicorn — BSD-3-Clause

BSD-3-Clause license: https://github.com/encode/uvicorn/blob/master/LICENSE.md

### httpx — BSD-3-Clause

BSD-3-Clause license: https://github.com/encode/httpx/blob/master/LICENSE.md

### Pydantic — MIT

MIT license: https://github.com/pydantic/pydantic/blob/main/LICENSE

### MCP Python SDK — MIT

MIT license: https://github.com/modelcontextprotocol/python-sdk/blob/main/LICENSE

### Textual — MIT

MIT license: https://github.com/Textualize/textual/blob/main/LICENSE

### Rich — MIT

MIT license: https://github.com/Textualize/rich/blob/master/LICENSE

### Trafilatura — Apache-2.0

Apache-2.0 license: https://github.com/adbar/trafilatura/blob/main/LICENSE

### NumPy — BSD-3-Clause

BSD-3-Clause license plus bundled permissive notices from third-party code:
https://github.com/numpy/numpy/blob/main/LICENSE.txt

## Algorithm and Splitter Provenance

### ClusterSemanticChunker

`ClusterSemanticChunker` in `semantic-chunking-service/chunking/cluster_semantic.py`
implements a first-party version of the dynamic-programming semantic chunking
approach described by Chroma Research in:

> "Evaluating Chunking Strategies for Retrieval"
> Chroma Research, July 2024
> https://research.trychroma.com/evaluating-chunking

The algorithm — globally optimal chunk boundary search via dynamic programming
over a cosine-similarity matrix — is credited to that publication. The code in
this repository is an original implementation; no source code was copied from
Chroma. Chroma's published code is Apache-2.0 licensed.

### RecursiveCharacterTextSplitter

`RecursiveCharacterTextSplitter` in
`semantic-chunking-service/chunking/recursive_splitter.py` is a first-party,
LangChain-inspired splitter using the familiar recursive separator hierarchy.
It does not vendor LangChain source. LangChain is MIT-licensed:
https://github.com/langchain-ai/langchain/blob/master/LICENSE

## Model Weights

GGUF model files placed under `models/` are not part of this repository and
are not licensed by this project. Operators are responsible for complying with
the license terms of any model weights they download and use.

Reference model provenance at the time of documentation:

| Model artifact | Hugging Face revision | SHA-256 | License |
|---|---|---|---|
| BAAI/bge-m3 for TEI | `BAAI/bge-m3@5617a9f61b028005a4858fdac845db406aefb181` | Hub-managed files | MIT |
| BAAI/bge-reranker-v2-m3 for TEI | `BAAI/bge-reranker-v2-m3@953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e` | Hub-managed files | Apache-2.0 |
| bge-m3 Q8_0 GGUF | `gpustack/bge-m3-GGUF@2d48f1737679ad900d5c26c5aad5410e9c70fdca` | `950f4a8e5e19477a6d3c26d2f162233c20002c601f75e4b002e3239997821167` | MIT (BAAI/bge-m3) |
| bge-reranker-v2-m3 Q8_0 GGUF | `gpustack/bge-reranker-v2-m3-GGUF@3093af03b1a635e67b084b1d8c03c5f5e020fd05` | `a43c7c9b11a4c1517e5bf95151960e1621d1b72f7a493364b01e386cf1aaa1d3` | Apache-2.0 (BAAI/bge-reranker-v2-m3) |
