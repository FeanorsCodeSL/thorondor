# Public Readiness Fixes

## Goal
Resolve the public-readiness findings from the adversarial repo review so the repository can be published as a clearly documented public alpha without obvious release, licensing, security, or CI gaps.

## References
- `README.md` - primary public entrypoint and quickstart.
- `THIRD-PARTY-NOTICES.md` - license inventory that must not contain stale or unverified claims.
- `docs/architecture/security.md` - public threat model and operator responsibilities.
- `docs/architecture/deployment.md` - deploy behavior must match scripts.
- `docs/architecture/dependencies.md` - dependency versions and model licenses must match manifests and current upstream metadata.
- `.github/workflows/release-guard.yml` - CI must prove tests, not only static release guard checks.
- `scripts/deploy.ps1` and `scripts/deploy.sh` - deploy scripts must fail on degraded health.
- `docker-compose.yml` and service Dockerfiles - public exposure and container defaults.

## Build & run
- **Containerized:** yes
- **Build command:** `docker compose --env-file .env.example -f docker-compose.yml --profile bundled-models config`
- **Test command:** `python -m pytest semantic-chunking-service\tests orchestrator\tests -v`

## Phase 1 - Release behavior and CI
**Status:** completed
**Kind:** logic

### Tasks
- [x] Make deploy scripts wait for all `/healthz` dependencies to be true.
- [x] Add CI steps that install both services' runtime/dev dependencies and run the offline pytest suite.
- [x] Keep release guard checks in CI.
- [x] Fail release guard when runtime Compose files use interpolation fallback values.

### Verification
- [x] Add repeatable release-guard checks for the fixed health-polling behavior.
- [x] Run release guard locally.
- [x] Run the offline pytest suite locally, or document the exact dependency/setup blocker.

## Phase 2 - Public docs and licensing
**Status:** completed
**Kind:** logic

### Tasks
- [x] Remove stale README references to missing directories.
- [x] Correct dependency versions and model licenses.
- [x] Remove redistribution placeholders from publication-facing notices and state the actual source-distribution boundary.
- [x] Add `SECURITY.md` and `CONTRIBUTING.md`.

### Verification
- [x] Search for stale placeholders, missing-directory references, and mismatched dependency/license text.

## Phase 3 - Public exposure and container hardening
**Status:** completed
**Kind:** logic

### Tasks
- [x] Bind the orchestrator to localhost by default for local Compose deployments.
- [x] Require that binding to come from explicit env values, not Compose fallback syntax.
- [x] Document the explicit opt-in for LAN/public binding.
- [x] Run first-party containers as non-root users.
- [x] Pin the Crawl4AI image by digest using the existing documented digest.

### Verification
- [x] Render bundled and llama.cpp Compose configs and verify localhost binding, internal network isolation, and pinned image references.
- [x] Compile Python sources after Dockerfile/script changes.
