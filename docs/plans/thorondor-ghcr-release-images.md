# Thorondor GHCR Release Images

## Goal
Publish Thorondor first-party runtime images to GHCR and provide a production Compose slice that Tengwar can consume without building from source or mounting the Thorondor source tree.

## References
- `docker-compose.yml` - current local development stack and service environment contract.
- `orchestrator/Dockerfile` - first-party orchestrator image build definition.
- `semantic-chunking-service/Dockerfile` - first-party chunker image build definition.
- `ssrf-proxy/Dockerfile` - first-party egress proxy image build definition.
- `.github/workflows/release-guard.yml` - existing CI validation surface to keep aligned with release behavior.
- `THIRD-PARTY-NOTICES.md` - existing third-party runtime image inventory and license notes.

## Build & run
- **Containerized:** yes
- **Build command:** `docker compose --env-file .env.example --env-file .env.production.example -f docker-compose.production.yml config`
- **Test command:** `python -m pytest semantic-chunking-service\tests orchestrator\tests -v`

## Phase 1 - Release images and production compose
**Status:** completed
**Kind:** logic

### Tasks
- [x] Add a GitHub Actions workflow that builds and pushes `thorondor-orchestrator`, `thorondor-chunker`, and `thorondor-egress-proxy` to `ghcr.io/feanorscodesl`.
- [x] Emit pushed image digests as workflow artifacts and GitHub release assets for tag releases.
- [x] Add image-only production Compose/env files for Tengwar-style deployments with external model endpoints.
- [x] Extend release guards and docs so the new production path is validated and discoverable.

### Verification
- [x] `.\scripts\check-release-guard.ps1` - passed.
- [x] `docker compose --env-file .env.example --env-file .env.production.example -f docker-compose.production.yml config` - passed.
- [x] `.\.venv\Scripts\python.exe -m pytest semantic-chunking-service\tests orchestrator\tests -v` - passed, 252 tests.
