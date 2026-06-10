# Third-Party Notices

This file records the third-party components intentionally referenced by the
Thorondor source distribution. It is engineering inventory, not legal advice.
The repository does not vendor Python wheels, third-party container image
layers, or model weights; binary or container distributors should attach their
own generated SBOM for the exact artifacts they ship.

## Runtime Images

| Component | Runtime reference | License | Upstream |
|---|---|---|---|
| SearXNG | `searxng/searxng@sha256:02d441bbb647b7be422d21041420115cddadac4644368f67c7c7f407bbe72e22` | AGPL-3.0 | https://github.com/searxng/searxng |
| Crawl4AI | `unclecode/crawl4ai@sha256:b243f684ad20f71ee108ab3fc3f31f3349eb5b31a9947b9e563d868417141aad` | Apache-2.0 | https://github.com/unclecode/crawl4ai |
| Hugging Face Text Embeddings Inference | `ghcr.io/huggingface/text-embeddings-inference@sha256:b3e0169969c0dc4b22ab6bf6ad5699374d4cb720fc43fb66868a679586ea806f` | Apache-2.0 | https://github.com/huggingface/text-embeddings-inference |
| llama.cpp server | `ghcr.io/ggml-org/llama.cpp:server@sha256:4c52f549b6612fc1b4aee696c4cfb4a9dceecb10216bb7e677cf97db909e1b4a` | MIT | https://github.com/ggml-org/llama.cpp |
| Python base image | `python:3.13-slim` | Python Software Foundation License + bundled OS package licenses | https://hub.docker.com/_/python |

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

### Hugging Face Text Embeddings Inference — Apache-2.0

Referenced image license verified against upstream LICENSE file at time of pin.
Apache-2.0: https://www.apache.org/licenses/LICENSE-2.0

### llama.cpp — MIT

MIT license: https://github.com/ggml-org/llama.cpp/blob/master/LICENSE

## Direct Runtime Python Dependencies

| Package | Version | License | SPDX | Used by |
|---|---:|---|---|---|
| FastAPI | 0.136.3 | MIT | MIT | orchestrator, chunker |
| Uvicorn | 0.49.0 | BSD-3-Clause | BSD-3-Clause | orchestrator, chunker |
| httpx | 0.28.1 | BSD-3-Clause | BSD-3-Clause | orchestrator, chunker |
| Pydantic | 2.13.4 | MIT | MIT | orchestrator, chunker |
| MCP Python SDK | 1.27.2 | MIT | MIT | orchestrator MCP surface |
| Trafilatura | 2.1.0 | Apache-2.0 | Apache-2.0 | orchestrator HTML-to-Markdown extraction |
| NumPy | 2.4.6 | BSD-3-Clause + bundled permissive notices | BSD-3-Clause | chunker |

Transitive Python dependencies are resolved by pip from the pinned direct
requirements listed in `orchestrator/requirements.txt` and
`semantic-chunking-service/requirements.txt`. Generate a full lockfile or SBOM
before publishing a binary distribution.

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

Reference model licenses at the time of documentation:

| Model | HuggingFace Hub | License |
|---|---|---|
| BAAI/bge-m3 | https://huggingface.co/BAAI/bge-m3 | MIT |
| BAAI/bge-reranker-v2-m3 | https://huggingface.co/BAAI/bge-reranker-v2-m3 | Apache-2.0 |
