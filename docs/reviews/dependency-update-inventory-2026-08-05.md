# Phase 4 Dependency Update Inventory

## Scope and decision boundary

This is the Part A review snapshot requested on 2026-08-05. It inventories the
Python, container, model, build-action, and deployment-tool dependencies that
form Thorondor's reproducible stack. It does not approve or apply any update.

No dependency pin, lock entry, image reference, model source, workflow, source
file, running container, or deployment was changed while producing this review.
Part B must contain only items the user explicitly approves by identifier or
named group.

The review preserves Thorondor's autonomous discovery boundary: SearXNG remains
self-hosted and invokes public search-engine websites directly. No hosted search
API, search-provider API key, or vendor-operated discovery service is proposed.

## Recommended approval list

### Low risk

| ID | Current | Candidate | Released | Recommendation and evidence |
|---|---|---|---|---|
| L1 | `mcp==1.27.2` | `mcp==1.28.1` | 2026-06-26 | Approve the v1 security backport. `pip-audit` reports `PYSEC-2026-3483` / `CVE-2026-59950`, fixed in 1.28.1. The advisory affects only the deprecated WebSocket server transport; Thorondor uses Streamable HTTP, so the current application path is not exposed. This is still the supported non-breaking remediation. [Advisory](https://github.com/modelcontextprotocol/python-sdk/security/advisories/GHSA-vj7q-gjh5-988w), [release](https://github.com/modelcontextprotocol/python-sdk/releases/tag/v1.28.1). License remains MIT; Python-only runtime support is unchanged. |
| L2 | `textual==8.2.7` | `8.2.8` | 2026-06-30 | Approve patch update with focused CLI/TUI tests. License remains MIT. [PyPI](https://pypi.org/project/textual/8.2.8/), [changelog](https://github.com/Textualize/textual/blob/main/CHANGELOG.md). |
| L3 | `pytest==9.1.0` in `uv.lock` | `9.1.1` | 2026-06-19 | Approve test-only patch update. The service development requirements are currently unpinned and already resolve 9.1.1. License remains MIT. [Changelog](https://docs.pytest.org/en/stable/changelog.html). |
| L4 | CLI lock leaf/transitive patches | Current stable patch set | 2026-06-19 to 2026-08-05 | Approve as one lock-only batch after L1: `anyio 4.14.0 -> 4.14.2`, `certifi 2026.6.17 -> 2026.7.22`, `click 8.4.1 -> 8.4.2`, `packaging 26.2 -> 26.3`, `platformdirs 4.10.0 -> 4.11.0`, `pydantic-settings 2.14.1 -> 2.14.2`, `rpds-py 2026.5.1 -> 2026.6.3`, `sse-starlette 3.4.4 -> 3.4.8`, and `typing-extensions 4.15.0 -> 4.16.0`. These versions are already present where applicable in the current service image resolution. License families and Python architecture support do not change. Authoritative version metadata: linked [PyPI projects](https://pypi.org/). |

`annotated-types 0.7.0 -> 0.8.0`, `cffi 2.0.0 -> 2.1.1`, and
`cryptography 49.0.0 -> 50.0.0` are not included in L4. Despite being transitive,
they contain a minor or major boundary and should travel only with the dependency
that requires them after its resolver output and tests are reviewed.

### Medium risk

| ID | Current | Candidate | Released | Recommendation and evidence |
|---|---|---|---|---|
| M1 | `fastapi==0.136.3` | `0.141.1` | 2026-07-29 | Approve only with REST, MCP, health, and full offline suites. FastAPI is pre-1.0 and the update spans several releases; it can alter validation and OpenAPI behavior. License remains MIT and supported Python platforms remain compatible. [Release notes](https://fastapi.tiangolo.com/release-notes/). |
| M2 | `uvicorn[standard]==0.49.0` | `0.52.1` | 2026-08-01 | Approve with startup, health, Streamable HTTP, and shutdown tests. The runtime server and several optional transport packages can change together. License remains BSD-3-Clause. [Release notes](https://uvicorn.dev/release-notes). |
| M3 | `numpy==2.4.6` | `2.5.1` | 2026-07-04 | Approve with semantic chunking golden/edge tests and both target-architecture image builds. Numeric changes can affect clustering boundaries. License family remains BSD. [Release notes](https://numpy.org/doc/stable/release.html). |
| M4 | `trafilatura==2.1.0` | `2.2.0` | 2026-07-31 | Defer until a representative extraction regression set is selected. Extraction output changes propagate into chunks, reranking, citations, and answer evidence even if the HTTP contract is stable. License remains Apache-2.0. [Changelog](https://github.com/adbar/trafilatura/blob/master/HISTORY.md). |
| M5 | `ruff==0.15.17` in `uv.lock` | `0.16.1` | 2026-07-30 | Approve as a separate developer-tool batch. It is runtime-independent but pre-1.0 lint behavior and formatting checks may change. License remains MIT. [Changelog](https://github.com/astral-sh/ruff/blob/main/CHANGELOG.md). |
| M6 | Three mutable `python:3.13-slim` bases | `python:3.13-slim@sha256:bf503bb2243c5aad0aa951544dd60d165f992646441d35dea90893703fc26251` (`3.13.14-slim-trixie`) | 2026-08-05 | Approve digest pinning, then rebuild all three first-party images for amd64 and arm64. The source already floats to this image on the next build, but pinning makes that input reproducible. License remains PSF plus Debian package licenses. Registry manifest includes amd64, arm64, arm/v5, arm/v7, 386, ppc64le, riscv64, and s390x. [Official image](https://hub.docker.com/_/python). |
| M7 | Unlocked service transitives and unpinned dev requirements | Add service lock/constraint files from reviewed resolver output | Snapshot 2026-08-05 | Approve as reproducibility hardening, not a library update. Current builds can silently change transitives. This needs a deliberate lock format, release-guard enforcement, and rebuilt images. No license or architecture change is implied until the generated locks are reviewed. |
| M8 | GitHub Actions major versions listed below | Latest stable majors listed below | 2026-03-11 to 2026-07-29 | Approve as one CI-only batch after reading every migration note. It affects validation and publication rather than the Thorondor runtime, but a failure can block releases or publish incorrect artifacts. Action licenses remain MIT for `actions/*` and Apache-2.0 for `docker/*`. |
| M9 | SonarScanner CLI `8.0.1.6346` | `8.1.0.6389` | 2026-04-21 | Approve as a separate analysis-only update on the self-hosted runner. License remains LGPL-3.0. [Release](https://github.com/SonarSource/sonar-scanner-cli/releases/tag/8.1.0.6389). |

### High risk

| ID | Current | Candidate | Released | Recommendation and evidence |
|---|---|---|---|---|
| H1 | SearXNG `2026.6.8-f3fab143b`, `sha256:02d441bbb647b7be422d21041420115cddadac4644368f67c7c7f407bbe72e22` | `2026.8.4-c63835bd2`, `sha256:f4c8e59de166ed71f6380c0847c312ca51f0d41996e31d0559163b6b09ecde52` | 2026-08-04 | Consider after Phase 3 establishes the corrected health-traffic baseline. The candidate is 179 commits ahead and changes engines, settings, and the container. It includes directly relevant Qwant access-denied and captcha fixes, but those improvements do not guarantee removal of upstream IP restrictions. License remains AGPL-3.0-or-later; both manifests support linux/amd64, linux/arm64, and linux/arm/v7. [Compare](https://github.com/searxng/searxng/compare/f3fab143be3069bbcdcec9169bcf6ee030437a61...c63835bd2a5133b30b3752a20eac6b443a918f41). |
| H2 | Crawl4AI `0.8.9`, `sha256:b243f684ad20f71ee108ab3fc3f31f3349eb5b31a9947b9e563d868417141aad` | `0.9.2`, `sha256:bd36741e7bdd35ddc1a05d9183e1d6d8cefb61dd640d944a25d026b76e917690` | 2026-07-15 | Defer until its `/crawl` server contract and representative JavaScript/non-JavaScript pages are tested. This is a large pre-1.0 jump in the extraction boundary. License remains Apache-2.0; both manifests support linux/amd64 and linux/arm64. [Release](https://github.com/unclecode/crawl4ai/releases/tag/v0.9.2). |
| H3 | TEI rolling image revision `5bc4d889`, `sha256:b3e0169969c0dc4b22ab6bf6ad5699374d4cb720fc43fb66868a679586ea806f` | Rolling revision `4150561`, `sha256:af92a3852c965393cbdd111865c3a72445d2b430c7daf84269ffdb5cf178f4eb` | 2026-07-08 | Defer. The repository pins an immutable digest from a rolling `latest` lineage, while the newest semantic release is `v1.9.3` from 2026-03-23. The candidate changes CUDA/runtime content without a matching stable tag. License remains Apache-2.0. Both current and candidate manifests are linux/amd64 only, so they cannot satisfy the documented ARM64 claim. [Latest stable release](https://github.com/huggingface/text-embeddings-inference/releases/tag/v1.9.3). |
| H4 | llama.cpp server `b9570`, `sha256:4c52f549b6612fc1b4aee696c4cfb4a9dceecb10216bb7e677cf97db909e1b4a` | Published `server` image `b10276`, `sha256:bde659bfc300ee7d4d2e558e8a97e06211bc2bf079e31d22b61497f4f2cd85b1`; source release `b10280` | 2026-08-05 | Defer until the image tag catches the release and embedding/reranking quality plus CLI compatibility are tested on both Spark architectures. The project moves rapidly and the jump can change inference output and flags. License remains MIT. Both manifests support linux/amd64, linux/arm64, and linux/s390x. [Release](https://github.com/ggml-org/llama.cpp/releases/tag/b10280). |
| H5 | `mcp==1.27.2` | `mcp==2.0.0` | 2026-07-28 | Do not combine with L1. This is an explicit major migration with API and transport removals; upstream recommends staying on `mcp>=1.28,<2` when not ready to migrate. License remains MIT and platform support is unchanged. [Release](https://github.com/modelcontextprotocol/python-sdk/releases/tag/v2.0.0). |
| H6 | TEI model IDs and GGUF downloads follow mutable `main` | Pin model revisions and verified file checksums | No newer model revision observed | Defer until immutable GGUF metadata can be authenticated and verified. The public model API confirmed `BAAI/bge-m3@5617a9f61b028005a4858fdac845db406aefb181` (MIT) and `BAAI/bge-reranker-v2-m3@953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e` (Apache-2.0), but the two GGUF API requests returned HTTP 401. No weights were downloaded. Changing revisions or quantizations can change ranking quality and model provenance. |

## GitHub Actions candidates for M8

| Action | Current | Candidate | Released | Release notes |
|---|---:|---:|---|---|
| `actions/checkout` | v4 | v7.0.1 | 2026-07-20 | [v7.0.1](https://github.com/actions/checkout/releases/tag/v7.0.1) |
| `actions/setup-python` | v5 | v7.0.0 | 2026-07-20 | [v7.0.0](https://github.com/actions/setup-python/releases/tag/v7.0.0) |
| `docker/setup-qemu-action` | v3 | v4.2.0 | 2026-07-01 | [v4.2.0](https://github.com/docker/setup-qemu-action/releases/tag/v4.2.0) |
| `docker/setup-buildx-action` | v3 | v4.2.0 | 2026-07-02 | [v4.2.0](https://github.com/docker/setup-buildx-action/releases/tag/v4.2.0) |
| `docker/login-action` | v3 | v4.6.0 | 2026-07-29 | [v4.6.0](https://github.com/docker/login-action/releases/tag/v4.6.0) |
| `docker/metadata-action` | v5 | v6.2.0 | 2026-07-02 | [v6.2.0](https://github.com/docker/metadata-action/releases/tag/v6.2.0) |
| `docker/build-push-action` | v6 | v7.3.0 | 2026-07-01 | [v7.3.0](https://github.com/docker/build-push-action/releases/tag/v7.3.0) |
| `actions/upload-artifact` | v4 | v7.0.1 | 2026-04-10 | [v7.0.1](https://github.com/actions/upload-artifact/releases/tag/v7.0.1) |
| `actions/download-artifact` | v4 | v8.0.1 | 2026-03-11 | [v8.0.1](https://github.com/actions/download-artifact/releases/tag/v8.0.1) |

`ubuntu-latest`, `python-version: "3.13"`, `pip install --upgrade pip`, and the
unpinned service development requirements are also mutable build inputs. They do
not have a single current immutable version in the repository.

## No-update review

- Direct Python dependencies already current: `httpx 0.28.1`, `pydantic 2.13.4`,
  `rich 15.0.0`, and `respx 0.23.1`.
- Model repositories have no newer `main` commit than the revisions recorded in
  H6. This is a provenance-hardening proposal, not a model-version update.
- First-party `thorondor-orchestrator`, `thorondor-chunker`, and
  `thorondor-egress-proxy` references remain at mutable tag `0.1.0`. Replacing
  them with corrected immutable release digests belongs to Phase 3, not this
  dependency update phase.
- No Node, Java, Rust, Go, or operating-system package manifest is maintained by
  this repository. Such transitives are inherited through reviewed container
  digests.

## Python inventory

### Direct and development requirements

| Surface | Declared dependencies |
|---|---|
| CLI runtime | `textual>=8.2.7,<9`, `rich>=15,<16`, `httpx==0.28.1`, `pydantic==2.13.4`, `mcp==1.27.2` |
| CLI development | `pytest>=8`, `respx>=0.22`, `ruff>=0.8` |
| CLI build | `setuptools>=68` (latest stable `83.0.0`, 2026-07-04; currently unlocked) |
| Orchestrator runtime | `fastapi==0.136.3`, `uvicorn[standard]==0.49.0`, `httpx==0.28.1`, `pydantic==2.13.4`, `mcp==1.27.2`, `trafilatura==2.1.0` |
| Orchestrator development | Unpinned `pytest`, `anyio`, `coverage`; resolver snapshot is `pytest 9.1.1`, `anyio 4.14.2`, `coverage 7.15.3` |
| Chunker runtime | `fastapi==0.136.3`, `uvicorn[standard]==0.49.0`, `httpx==0.28.1`, `numpy==2.4.6`, `pydantic==2.13.4` |
| Chunker development | Unpinned `pytest`, `coverage`; resolver snapshot is `pytest 9.1.1`, `coverage 7.15.3` |

The latest stable direct candidates and dates were read from official PyPI JSON
metadata. No candidate changes the declared license family. Part B must still
confirm wheels/builds on linux/amd64 and linux/arm64 rather than infer support
from metadata alone.

### Complete CLI lock inventory

`uv.lock` contains 46 third-party packages. `current = latest` means no update
was available on 2026-08-05.

```text
annotated-types 0.7.0 -> 0.8.0
anyio 4.14.0 -> 4.14.2
attrs 26.1.0 = latest
certifi 2026.6.17 -> 2026.7.22
cffi 2.0.0 -> 2.1.1
click 8.4.1 -> 8.4.2
colorama 0.4.6 = latest
cryptography 49.0.0 -> 50.0.0
h11 0.16.0 = latest
httpcore 1.0.9 = latest
httpx 0.28.1 = latest
httpx-sse 0.4.3 = latest
idna 3.18 = latest
iniconfig 2.3.0 = latest
jsonschema 4.26.0 = latest
jsonschema-specifications 2025.9.1 = latest
linkify-it-py 2.1.0 = latest
markdown-it-py 4.2.0 = latest
mcp 1.27.2 -> 1.28.1 security backport or 2.0.0 major
mdit-py-plugins 0.6.1 = latest
mdurl 0.1.2 = latest
packaging 26.2 -> 26.3
platformdirs 4.10.0 -> 4.11.0
pluggy 1.6.0 = latest
pycparser 3.0 = latest
pydantic 2.13.4 = latest
pydantic-core 2.46.4 -> 2.47.0; tied to a compatible pydantic release
pydantic-settings 2.14.1 -> 2.14.2
pygments 2.20.0 = latest
pyjwt 2.13.0 = latest
pytest 9.1.0 -> 9.1.1
python-dotenv 1.2.2 = latest
python-multipart 0.0.32 = latest
pywin32 312 = latest
referencing 0.37.0 = latest
respx 0.23.1 = latest
rich 15.0.0 = latest
rpds-py 2026.5.1 -> 2026.6.3
ruff 0.15.17 -> 0.16.1
sse-starlette 3.4.4 -> 3.4.8
starlette 1.3.1 -> 1.4.0
textual 8.2.7 -> 8.2.8
typing-extensions 4.15.0 -> 4.16.0
typing-inspection 0.4.2 = latest
uc-micro-py 2.0.0 = latest
uvicorn 0.49.0 -> 0.52.1
```

`thorondor 0.0.0` is the local project and is not an external dependency.

### Current service transitive snapshots

The service requirements do not lock transitives. The following are the exact
installed versions in the running local images inspected on 2026-08-05; they are
evidence of the current deployment, not reproducible source pins.

Orchestrator:

```text
annotated-doc 0.0.5; annotated-types 0.8.0; anyio 4.14.2; attrs 26.1.0;
babel 2.18.0; certifi 2026.7.22; cffi 2.1.1; charset-normalizer 3.4.9;
click 8.4.2; courlan 1.4.0; cryptography 50.0.0; dateparser 1.4.2;
fastapi 0.136.3; h11 0.16.0; htmldate 1.10.0; httpcore 1.0.9;
httptools 0.8.0; httpx 0.28.1; httpx-sse 0.4.3; idna 3.18;
jsonschema 4.26.0; jsonschema-specifications 2025.9.1; jusText 3.0.2;
lxml 6.1.1; lxml_html_clean 0.4.5; mcp 1.27.2; pycparser 3.0;
pydantic 2.13.4; pydantic-settings 2.14.2; pydantic-core 2.46.4;
PyJWT 2.13.0; python-dateutil 2.9.0.post0; python-dotenv 1.2.2;
python-multipart 0.0.32; pytz 2026.3.post1; PyYAML 6.0.3;
referencing 0.37.0; regex 2026.7.19; rpds-py 2026.6.3; six 1.17.0;
sse-starlette 3.4.8; starlette 1.4.0; tld 0.13.2; trafilatura 2.1.0;
typing-inspection 0.4.2; typing-extensions 4.16.0; tzlocal 5.4.4;
urllib3 2.7.0; uvicorn 0.49.0; uvloop 0.22.1; watchfiles 1.2.0;
websockets 17.0.1
```

Chunker:

```text
annotated-doc 0.0.5; annotated-types 0.8.0; anyio 4.14.2;
certifi 2026.7.22; click 8.4.2; fastapi 0.136.3; h11 0.16.0;
httpcore 1.0.9; httptools 0.8.0; httpx 0.28.1; idna 3.18;
numpy 2.4.6; pydantic 2.13.4; pydantic-core 2.46.4;
python-dotenv 1.2.2; PyYAML 6.0.3; starlette 1.4.0;
typing-inspection 0.4.2; typing-extensions 4.16.0; uvicorn 0.49.0;
uvloop 0.22.1; watchfiles 1.2.0; websockets 17.0.1
```

The base image also supplies `pip 26.1.2`; `26.2.1` is available. It should not
be upgraded independently of M6/M7 because the Dockerfiles do not currently pin
the installer.

## Image and model synchronized references

Any approved image update must change every listed copy atomically:

| Dependency | Synchronized references |
|---|---|
| SearXNG, Crawl4AI, TEI | `docker-compose.yml`, `.env.example`, `.env.production.example`, `thorondor_cli/assets/docker-compose.yml`, `thorondor_cli/templates/env.example`, `thorondor_cli/templates/env.production.example`, `THIRD-PARTY-NOTICES.md`, dependency/deployment docs, release-guard assertions where applicable |
| llama.cpp | `.env.llamacpp.example`, `thorondor_cli/templates/env.llamacpp.example`, both llama.cpp Compose overlays through `LLAMACPP_IMAGE`, `README.md`, `THIRD-PARTY-NOTICES.md`, dependency/deployment/configuration docs |
| Python base | `orchestrator/Dockerfile`, `semantic-chunking-service/Dockerfile`, `ssrf-proxy/Dockerfile`, `THIRD-PARTY-NOTICES.md`, dependency docs |
| First-party images | `.env.example`, `.env.production.example`, both packaged templates, production Compose variables, configuration/deployment docs, publication workflow defaults |
| TEI model IDs | `.env.example`, packaged env template, CLI catalog/state defaults, README and architecture docs, notices |
| GGUF models | `thorondor_cli/models.py`, CLI model tests, dependency docs, local filenames and llama.cpp env/templates |
| GitHub Actions | `.github/workflows/release-guard.yml`, `.github/workflows/sonar.yml`, `.github/workflows/publish-images.yml` as applicable |

Registry inspection also found two pre-existing documentation mismatches:

- The pinned Crawl4AI digest supports linux/amd64 and linux/arm64, while
  `docs/architecture/dependencies.md` lists only amd64.
- The pinned TEI digest is linux/amd64 only, while that document and deployment
  guidance claim amd64 and arm64.

These corrections should be made in Part B with whichever image decision is
approved; they do not require or justify changing an image by themselves.

## Verification performed

- Cross-checked tracked Dockerfiles, Compose manifests, env examples, packaged
  CLI assets/templates, model sources, notices, and all GitHub workflows with
  `git grep`.
- Read the exact `uv.lock` graph with `uv tree --outdated --universal`.
- Resolved the currently unpinned development requirements without writing lock
  files.
- Inspected installed service package snapshots and their outdated lists in the
  already-running containers; no container was restarted or changed.
- Audited both runtime requirement sets: one MCP advisory in the orchestrator,
  none in the chunker.
- Resolved current and candidate OCI manifest digests, labels, creation dates,
  licenses, and platforms from Docker Hub/GHCR.
- Verified package versions/dates from PyPI, releases and security details from
  upstream repositories, model revisions/licenses from Hugging Face, and action
  releases from their official repositories.
- Confirmed the worktree diff contains only this review and the existing plan
  update; dependency manifests and runtime configuration remain unchanged.

## Approval format

Approve individual identifiers or groups, for example `L1-L4 and M6`, and list
anything explicitly rejected. Unmentioned items remain deferred.
