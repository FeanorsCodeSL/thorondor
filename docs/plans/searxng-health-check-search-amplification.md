# SearXNG Health Check Search Amplification

## Goal
Stop Thorondor health monitoring from generating public web searches, distinguish upstream search-engine failure from a genuine empty result, and verify the correction before deploying immutable Thorondor images to the demo Spark.

## Current status
The issue was reproduced on both Sparks 1 and Sparks 2 during demo preparation on 2026-07-31. Phase 1 implementation and offline verification completed locally on 2026-08-05. Live-container verification remains pending, so the phase is still in progress. Phase 2 is completed. Phase 3 is pending. Phase 4 Part A is completed. Every approved Part B update is implemented and verified locally; Phase 4 awaits user acceptance and a separately authorized deployment smoke check.

## Problem
The orchestrator Docker health check calls `GET /healthz` every 30 seconds. The SearXNG dependency probe inside that endpoint calls:

```text
GET /search?q=health&format=json
```

This is not a passive health check. It causes SearXNG to submit a real public search to its configured engines on every probe. With the stack running on two machines behind the same public IP, routine container health monitoring produces continuous upstream traffic without any user search.

The health contract also has a false-positive failure mode. The orchestrator treats any HTTP success from SearXNG as healthy, even when SearXNG reports engine failures or returns no usable results. The search client then treats an HTTP 200 response with an empty `results` array as a genuine empty search and does not inspect `unresponsive_engines`. The pipeline consequently reports `no_results_from_discovery`, masking provider degradation as an absence of information.

## Verified evidence

The following observations were collected from the live Sparks 1 and Sparks 2 deployments:

| Evidence | Sparks 1 | Sparks 2 |
|---|---:|---:|
| Automatic `q=health` searches in one hour | 118 | 115 |
| Docker health interval | 30 seconds | 30 seconds |
| Thorondor/SearXNG image and settings | Identical | Identical |
| Public egress IP | Shared | Shared |
| Freshness-constrained reproduction | Empty in 3-5 ms | Empty in 3-5 ms |

Additional evidence:

- SearXNG logged upstream HTTP 403 or access-denied failures for Mojeek, Qwant, and Yep, including temporary engine suspensions.
- Equivalent searches without the freshness constraint sometimes discovered ten URLs and continued through crawling and reranking. This shows that an empty result is dependent on engine availability and query constraints rather than the whole Thorondor pipeline being unavailable.
- The failing searches reproduced on both machines. This rules out a Sparks 1 image-packaging or deployment-only defect.
- The user had performed only a few manual searches, while the health probes generated hundreds of SearXNG searches per hour across the two machines.
- SearXNG can fan one query out to the five configured engines. The observed 233 health searches per hour therefore represent up to 1,165 upstream engine requests per hour before accounting for engines that SearXNG temporarily suspended.

The continuous synthetic traffic is sufficient to trigger or materially contribute to upstream rate limiting and anti-bot controls. The exact provider thresholds are opaque, so the health traffic should not be described as the sole proven cause of every HTTP 403. It is, however, the dominant self-generated search load and must be removed regardless of provider behavior.

## Root cause

1. `orchestrator/Dockerfile` invokes the orchestrator `/healthz` endpoint every 30 seconds.
2. `orchestrator/app.py` implements the SearXNG dependency check by issuing a real `/search?q=health` request.
3. SearXNG fans that request out to public search engines.
4. Both Spark units repeat the same probe while sharing one public egress IP.
5. Search engines reject or suspend the resulting traffic.
6. `orchestrator/clients/searxng_client.py` ignores SearXNG's `unresponsive_engines` response field.
7. `orchestrator/pipeline.py` maps the resulting empty discovery set to `no_results_from_discovery`.
8. `/healthz` can still report SearXNG as healthy because it checks HTTP reachability rather than the payload's engine state.

This conflates three different operational concerns:

- **Liveness:** whether the Thorondor process is running.
- **Readiness:** whether required local dependencies are reachable and able to accept work.
- **Synthetic search:** whether a complete public-web search succeeds through external providers.

A high-frequency Docker liveness or readiness probe must never perform the synthetic-search function.

## User-visible impact

- Normal container monitoring can consume public search-engine quotas and trigger anti-bot blocking.
- Search failures are reported as “no results” instead of dependency degradation.
- The same public-IP reputation affects both development and demo units.
- A deployment can appear healthy while discovery is unable to obtain evidence.
- Tengwar may receive no sources or citations and can still produce an unsupported answer unless the caller fails closed.
- Restarting or redeploying containers does not guarantee recovery from an upstream IP-based restriction.

## References

- `orchestrator/Dockerfile` - invokes `/healthz` as the recurring container health check.
- `orchestrator/app.py` - implements the SearXNG health probe as a real search.
- `orchestrator/clients/searxng_client.py` - sends freshness constraints and currently ignores `unresponsive_engines`.
- `orchestrator/pipeline.py` - maps an empty discovery set to `no_results_from_discovery`.
- `orchestrator/mcp_server.py` - defines the structured MCP search inputs, including `domains` and freshness.
- `orchestrator/tests/test_app_rest.py` - currently asserts the real-search health-check behavior.
- `searxng/settings.yml` - defines the public engines used by the bundled SearXNG service.
- `docs/architecture/pipeline-workflow.md` - documents the current health flow.
- `docs/architecture/deployment.md` - uses `/healthz` as a deployment and monitoring gate.
- `README.md` - documents the public health and search response contracts.
- `pyproject.toml` and `uv.lock` - define and lock the Python CLI dependencies.
- `orchestrator/requirements*.txt` and `semantic-chunking-service/requirements*.txt` - pin service runtime and development dependencies.
- Service Dockerfiles and Compose files - define base images and runtime service images.
- `thorondor_cli/assets/` - packages deployment manifests that must stay synchronized with the repository manifests.
- `.github/workflows/publish-images.yml` - defines image build and publication dependencies.
- `docs/reviews/dependency-update-inventory-2026-08-05.md` - records the Phase 4 Part A update candidates, risk classification, synchronized references, and verification evidence.

## Build & run

- **Containerized:** yes
- **Build command:** `docker compose --env-file .env.example --env-file .env.production.example -f docker-compose.production.yml config`
- **Test command:** `python -m pytest semantic-chunking-service/tests orchestrator/tests -v`

## Required health model

- The liveness check verifies only the Thorondor process and does not call SearXNG or any public service.
- The readiness check may verify local dependency reachability, but its SearXNG probe must not use `/search` or generate external traffic.
- A full end-to-end synthetic search, if retained, is a separate explicit operation with a low frequency, an identifiable query, independent alerting, and no role in Docker container liveness.
- Health responses must distinguish process failure, local dependency unavailability, and external search-provider degradation.

## Phase 1 - Stop health-check search traffic

**Status:** in_progress
**Kind:** logic

### Tasks

- [x] Replace the SearXNG portion of `/healthz` with a non-search local reachability or readiness probe.
- [x] Separate orchestrator liveness from dependency readiness if one endpoint cannot express both contracts clearly.
- [x] Preserve detection of connection failures, timeouts, and non-success HTTP responses.
- [x] Ensure the Docker health check uses the process-only liveness contract.
- [x] Update REST tests so repeated health requests prove that no SearXNG `/search` call occurs.
- [x] Update the README and architecture/deployment documentation to match the corrected contracts.

### Implementation report

- Added process-only `GET /livez` and pointed the orchestrator Docker health check to it.
- Kept `GET /healthz` as the dependency-readiness contract used by deployment tooling.
- Replaced SearXNG `/search?q=health` with the pinned image's passive local `/healthz` endpoint.
- Preserved the internal `X-Real-IP` header on the passive probe so SearXNG bot detection accepts it without logging a missing-client-IP error.
- Required successful 2xx responses from readiness probes instead of accepting all responses below 500.
- Added REST regression coverage for repeated passive probes, process-only liveness, timeouts, connection failures, and non-success HTTP responses.
- Updated the README and architecture documents to distinguish liveness from readiness.

### Verification

- [x] Run the focused orchestrator health tests.
- [x] Run the complete offline orchestrator test suite.
- [ ] Observe a running container for at least ten minutes and confirm that its health probes produce zero SearXNG search requests.
- [ ] Stop or disconnect SearXNG and confirm readiness reports it unavailable without generating public traffic.

### Verification report

- Focused REST health suite: 14 passed with one upstream deprecation warning.
- Complete offline chunker and orchestrator suites: 255 passed with three upstream deprecation warnings.
- Follow-up after restoring the internal SearXNG probe header: 211 orchestrator tests passed with three upstream deprecation warnings.
- Production Compose configuration validation passed.
- `git diff --check` passed, and static inspection confirmed Docker targets `/livez` while readiness targets SearXNG `/healthz`.
- Live-container observation and dependency-disconnection checks were not run because no deployment or running-stack mutation was authorized in this phase.

## Phase 2 - Report search-provider degradation accurately

**Status:** completed
**Kind:** logic

### Tasks

- [x] Parse and evaluate SearXNG's `unresponsive_engines` field.
- [x] Define a structured result that distinguishes a healthy empty search from partial engine degradation and complete discovery unavailability.
- [x] Prevent upstream engine failures from collapsing silently into `no_results_from_discovery`.
- [x] Preserve equivalent failure semantics for the REST and MCP interfaces.
- [x] Add tests for HTTP 200 responses with healthy empty results, partially failed engines, and failed engines with no usable results.
- [x] Document the new response reason or error contract.

### Implementation report

- Added an internal `DiscoveryOutcome` carrying both usable results and SearXNG engine failures.
- Parsed SearXNG's `[engine, reason]` entries and propagated them through concurrent sub-query discovery.
- Added `stats.discovery_status` with `ok`, `degraded`, and `unavailable` states plus structured `stats.unresponsive_engines` entries.
- Kept healthy empty searches as `no_results_from_discovery`; engine failures with no usable results now return `search_provider_unavailable`.
- Preserved usable results during partial engine degradation and exposed the same structured response through REST and MCP.
- Updated the response schema golden file, README, MCP contract text, and architecture flow documentation.

### Verification

- [x] Prove that a healthy SearXNG response with no matches remains a genuine empty result.
- [x] Prove that HTTP 200 with failed or suspended engines is surfaced as degraded or unavailable.
- [x] Prove that REST and MCP callers receive equivalent structured outcomes.

### Verification report

- Focused client, pipeline, model, REST/MCP parity, and investigation tests: 85 passed with three upstream deprecation warnings.
- Complete offline semantic-chunking and orchestrator suites: 260 passed with three upstream deprecation warnings.
- Healthy empty discovery remained `discovery_status=ok` with `reason=no_results_from_discovery`.
- Partial engine failure retained usable passages with `discovery_status=degraded`; failed engines with no usable results returned `discovery_status=unavailable` and `reason=search_provider_unavailable`.
- REST and MCP responses matched for both new degradation outcomes.
- Production Compose configuration validation and `git diff --check` passed.
- Separate code inspection found no Phase 2 defect requiring correction.

## Phase 3 - Deploy and verify the correction

**Status:** pending
**Kind:** logic

### Tasks

- [ ] Build and publish new first-party Thorondor images from the corrected commit.
- [ ] Record immutable image digests for the orchestrator, chunker, and egress proxy.
- [ ] Deploy and verify the images on Sparks 2 first.
- [ ] Allow existing SearXNG engine suspensions to expire or recover only after automatic search traffic has stopped.
- [ ] Deploy the same immutable image digests to Sparks 1 without copying or cloning Thorondor source code onto the demo unit.
- [ ] Preserve the existing separation between the Tengwar and Thorondor Compose projects.

### Verification

- [ ] Confirm both units use the recorded image digests and expected SearXNG configuration.
- [ ] Confirm ten minutes of container health checks on each unit produce zero public search requests.
- [ ] Confirm local dependency loss is still visible through readiness.
- [ ] Run one controlled domain-constrained search and verify that it returns cited evidence or an explicit provider-degradation response.
- [ ] Confirm Sparks 1 contains runtime images and configuration only, with no Thorondor source or test tree.

## Phase 4 - Review and update dependencies

**Status:** in_progress
**Kind:** logic

### Part A - Produce an update inventory for approval

#### Tasks

- [x] Inventory every direct and transitive Python dependency in `pyproject.toml`, `uv.lock`, and the runtime and development requirement files for each service.
- [x] Inventory every base, build, and runtime image referenced by Dockerfiles, Compose manifests, environment examples, packaged CLI assets, and publication workflows, including SearXNG, Crawl4AI, TEI, llama.cpp, and first-party Thorondor images.
- [x] Inventory externally versioned model artifacts, build actions, and deployment tooling that form part of the reproducible stack.
- [x] For each dependency, record the current immutable version or digest, the latest stable candidate, release date, authoritative changelog or security notice, license impact, architecture support, and every synchronized reference that would need to change.
- [x] Organize available updates into low-, medium-, and high-risk groups with a concrete compatibility and operational rationale for each item.
- [x] Present a no-change review list and wait for the user's explicit approval of individual updates or named groups.

#### Verification

- [x] Cross-check the inventory against manifests, lock files, Dockerfiles, environment examples, packaged assets, and build workflows so no dependency surface is omitted.
- [x] Verify candidate versions, digests, release notes, security advisories, licenses, and supported architectures from authoritative upstream sources.
- [x] Confirm that Part A changes no dependency pin, lock entry, image reference, model artifact, source file, or deployment configuration.

#### Part A report

- Added `docs/reviews/dependency-update-inventory-2026-08-05.md` with individually selectable low-, medium-, and high-risk update identifiers plus an explicit no-update list.
- Recorded all direct requirements, all 46 third-party CLI lock entries, the exact transitive packages installed in the running orchestrator and chunker images, and the unpinned service-development resolver results.
- Audited the runtime requirements: `mcp 1.27.2` has `PYSEC-2026-3483`, fixed in `1.28.1`; the deprecated affected WebSocket server transport is not used by Thorondor. The chunker requirements had no known advisory.
- Resolved the current and candidate OCI digests and architectures for SearXNG, Crawl4AI, TEI, llama.cpp, and the Python base image from their registries.
- Found two existing documentation errors: the pinned Crawl4AI manifest includes ARM64, while the pinned TEI manifest is AMD64-only.
- Confirmed that Part A changed only the plan and its review artifact. No dependency, image, model, workflow, container, or deployment was changed.
- The inventory was committed and pushed as `a232765` before Part B implementation began.

### Part B - Apply only approved updates

#### Tasks

- [x] Record the explicitly approved update set and retain rejected or deferred items unchanged.
- [x] Update only approved Python pins, lock entries, image references, model artifacts, packaged assets, examples, and related license or provenance documentation.
- [x] Keep duplicated manifests and packaged CLI assets synchronized, and use immutable image digests whenever the registry provides them.
- [x] Apply updates in reviewable logical batches with a clear rollback boundary and no unrelated refactoring.
- [x] Preserve SearXNG as an unmodified third-party dependency and preserve existing REST and MCP contracts unless a separately approved update requires a documented compatibility change.

#### Approved sequence

- Apply L1-L4 as one low-risk batch, then build and test.
- Apply M1-M9 as one medium-risk batch, then build and test.
- Apply and verify H1-H6 individually, in order.

#### Low-risk batch report

- Updated L1-L4: MCP 1.28.1, Textual 8.2.8, and the approved transitive CLI lock entries.
- Built the affected orchestrator image and confirmed the installed versions.
- Complete offline suite: 328 passed with three upstream deprecation warnings.
- Runtime dependency audits for the orchestrator and chunker reported no known vulnerabilities.
- `uv lock --check` and `git diff --check` passed.

#### Medium-risk batch report

- Updated M1-M9: FastAPI 0.141.1, Uvicorn 0.52.1, NumPy 2.5.1, Trafilatura 2.2.0, Ruff 0.16.1, the immutable Python 3.13.14 base-image digest, exact service transitive locks, GitHub Actions, and SonarScanner CLI 8.1.0.6389.
- Added automated lock synchronization checks and connected them to both release-guard scripts.
- Complete offline suite using the exact development locks: 330 passed with three upstream deprecation warnings.
- Built the orchestrator, chunker, and egress proxy for both AMD64 and ARM64; installed dependency versions matched the approved pins.
- Runtime dependency audits reported no known vulnerabilities. Bundled-model, llama.cpp, and production Compose validation passed, as did workflow YAML parsing, `uv lock --check`, the new requirement-lock check, and `git diff --check`.
- The repository-wide Ruff 0.16.1 run exposed 245 pre-existing lint findings; Ruff is not currently a CI gate and no unrelated code was rewritten.
- The full release guard still fails on pre-existing differences between repository environment examples and packaged templates. The new requirement-lock check itself passes; the unrelated template differences remain unchanged.
- Updated GitHub Action majors were syntax-checked locally but still require execution on the configured GitHub runners.

#### High-risk item reports

- **H1 SearXNG:** updated the unmodified upstream image to `2026.8.4-c63835bd2` at `sha256:f4c8e59de166ed71f6380c0847c312ca51f0d41996e31d0559163b6b09ecde52`. Registry inspection confirmed AMD64, ARM64, and ARM/v7 manifests. An isolated container using the repository configuration and no external network returned `200 OK` from `/healthz`; all Compose profiles rendered, all 330 offline tests passed with three upstream deprecation warnings, stale references were absent, and `git diff --check` passed. No deployed container was changed.
- **H2 Crawl4AI:** updated the upstream image to 0.9.2 at `sha256:bd36741e7bdd35ddc1a05d9183e1d6d8cefb61dd640d944a25d026b76e917690`; registry inspection confirmed AMD64 and ARM64 manifests. Isolated testing found that 0.9.2 no longer exposes `/crawl` to the Compose network without a configured token, so the deploy scripts and configurator now generate and preserve `CRAWL4AI_API_KEY`. With that key, a network-isolated container accepted the existing `/crawl` payload and returned both static and JavaScript-rendered content. Focused tests were 19 passed, the complete offline suite was 332 passed with three upstream deprecation warnings, all Compose profiles rendered, script parsing and stale-reference checks passed, and `git diff --check` passed. No deployed container was changed.
- **H3 TEI:** updated the rolling image from revision `5bc4d889` to `4150561` at `sha256:af92a3852c965393cbdd111865c3a72445d2b430c7daf84269ffdb5cf178f4eb`. Registry and image-config inspection confirmed the approved revision, Apache-2.0 metadata, AMD64-only architecture, CUDA runtime requirement, and continued `--model-id` CLI support. Documentation now states that bundled TEI requires an AMD64 NVIDIA host; the previous ARM64 claim was incorrect for both the old and new pins. All 332 offline tests passed with three upstream deprecation warnings, all Compose profiles rendered, stale-reference and `git diff --check` checks passed. Runtime inference could not be exercised on the ARM64 host because the image has no ARM64 manifest and requires NVIDIA runtime injection; no deployed container was changed.
- **H4 llama.cpp:** updated the server image from build `b9570` to the current `server` build `b10276` at `sha256:bde659bfc300ee7d4d2e558e8a97e06211bc2bf079e31d22b61497f4f2cd85b1`. Registry inspection confirmed AMD64, ARM64, and s390x manifests. The native ARM64 container reported version `10276 (6ea215d17)`, and its help output confirmed every CLI flag used by the Compose overlay, including embedding and reranking modes. All 332 offline tests passed with three upstream deprecation warnings, all Compose profiles rendered, packaged and repository llama.cpp env templates matched, stale references were absent, and `git diff --check` passed. Model-quality testing remains a deployment-time check because GGUF weights are not committed; no deployed container was changed.
- **H5 MCP Python SDK:** migrated the orchestrator and stdio proxy from `mcp 1.28.1` and `FastMCP` to `mcp 2.0.0` and `MCPServer`, including the renamed HTTP transport client and the v2 `httpx2` transport. The public endpoint remains `/mcp`, its stateless behavior and DNS-rebinding policy are preserved, and tests exercise both the legacy initialize handshake and the modern `server/discover` negotiation. Focused MCP tests were 18 passed; the complete offline suite was 332 passed. The native ARM64 orchestrator image built successfully and contained `mcp 2.0.0`; MCP's platform-independent wheels preserve AMD64 compatibility already established by the medium-risk multi-architecture build. The runtime audit reported no known vulnerabilities, all Compose profiles rendered, exact lock validation and source compilation passed, stale references were absent, and `git diff --check` passed. No deployed container was changed.
- **H6 immutable model artifacts:** replaced the two nonexistent mutable GGUF source URLs with public `gpustack` repositories pinned to exact commits, recorded their Hub LFS SHA-256 values, and made the downloader verify new and existing default files without overwriting a mismatch. Added exact BAAI model revisions to the bundled TEI commands and synchronized the source templates, managed Compose asset, CLI bundled-mode state, notices, and architecture documentation. Live Hub metadata matched both pinned revisions, SHA-256 values, and file sizes. Focused tests were 32 passed, the complete offline suite was 334 passed, and Ruff passed on every affected Python file. The CLI wheel and source distribution built and contained the revision and checksum metadata; all Compose profiles rendered, both runtime audits reported no known vulnerabilities, and lock, compilation, and `git diff --check` validation passed. The 1.27 GB of GGUF weights were not downloaded, and TEI inference could not run on this ARM64 host without the AMD64 NVIDIA runtime. No deployed container was changed.

#### Verification

- [x] Run focused tests appropriate to every approved update, then run the complete offline test suite.
- [x] Validate every supported Compose profile, including bundled models, llama.cpp, and production image deployments.
- [x] Build affected first-party images when an approved dependency or base-image update changes their contents.
- [x] Verify resolved image digests, supported architectures, model checksums, and license/provenance records for updated artifacts.
- [ ] Run live smoke checks only after separate deployment authorization, and report the applied versions, exact verification results, deferred updates, and residual risks.

## Acceptance criteria

- Ten minutes of normal Docker health probing generate zero `/search?q=health` requests and zero other public searches.
- Orchestrator liveness remains healthy when the process can serve requests.
- Readiness reports SearXNG unavailable when its container cannot be reached.
- SearXNG engine failures or suspensions are not reported as `no_results_from_discovery`.
- A healthy search with genuinely no matching results retains an explicit empty-result outcome.
- REST and MCP expose equivalent degradation semantics.
- Domain and freshness constraints are never silently weakened.
- A live constrained search either returns usable citations or explicitly reports that evidence is unavailable.
- The demo unit runs immutable production images without Thorondor source code or tests.

## Operational constraints

- No recurring health probe may generate public web traffic.
- Do not add a hosted search API, search-provider API key, or other vendor-operated discovery dependency; web discovery remains self-hosted through SearXNG invoking public search engines.
- Do not patch or redistribute modified SearXNG source as part of this fix.
- Do not assume a container restart clears an upstream IP restriction.
- Do not hide provider failures behind a successful HTTP status or an empty result.
- Do not deploy mutable tags when an immutable digest is available.
