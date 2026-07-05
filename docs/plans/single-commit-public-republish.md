# Single-Commit Public Republish

## Goal
Preserve the current full-history Thorondor repository as a private archive, then publish a clean public `FeanorsCodeSL/thorondor` repository containing only the final approved source tree as a single initial commit. Recreate the public repository metadata, CI, release surfaces, and branch protections after publication.

## Current Evidence
- Local checkout: `C:\git\FEANORS-CODE\thorondor`.
- Active local branch: `addTui` at `83d0f77`, tracking `origin/addTui`.
- `addTui` is 4 commits ahead of `origin/main`: `83d0f77`, `7163bf8`, `0dda019`, `2cc2fa1`.
- Remote: `origin git@github-feanors:FeanorsCodeSL/thorondor.git`.
- GitHub repo is already public: `FeanorsCodeSL/thorondor`, default branch `main`.
- Existing repo description: `Public-alpha self-hosted semantic web search for agents, with REST and MCP surfaces.`
- Existing topics: `mcp-server`, `semantic-search`, `webcrawler`.
- Existing homepage: `https://feanorscode.com`.
- Existing workflows: `release-guard`, `publish-images`, `SonarQube`.
- Existing tag: `v0.1.0`.
- Existing `main` branch protection restricts pushes to `feanorscode`, blocks force pushes/deletions, and enforces admins, but does not currently require status checks.
- Existing repo ruleset `Restrict base-repo branch changes` blocks branch creation/update/deletion outside `main` unless bypassed by `feanorscode`.
- Existing Actions workflow token permissions are `write`; the clean public repo should default to read-only and grant write only in workflows that need it, such as image publishing.

## Important Boundary
Thorondor is already public. Renaming the existing repository and making it private protects the full history going forward, but it cannot guarantee removal of history already fetched, cached, forked, mirrored, indexed, or released while the repo was public. Treat this as a clean future publication boundary, not a retroactive erasure guarantee.

## References
- `AGENTS.md` - repository commands, structure, and publishing constraints.
- `README.md` - public entrypoint, public-alpha messaging, quickstarts, REST/MCP contract, and release image instructions.
- `docs/plans/public-readiness-fixes.md` - completed public readiness work and remaining verification surface.
- `docs/plans/thorondor-ghcr-release-images.md` - GHCR image publishing workflow and production Compose contract.
- `.github/workflows/release-guard.yml` - required CI jobs for the clean public repo: `release-guard` and `test`.
- `.github/workflows/publish-images.yml` - tag/manual image publishing workflow requiring `contents: write` and `packages: write`.
- `.github/workflows/sonar.yml` - decide whether this LAN/self-hosted workflow belongs in the public clean repo.
- `scripts/check-release-guard.ps1` and `scripts/check-release-guard.sh` - release guard parity checks.
- `docker-compose.yml`, `docker-compose.llamacpp.yml`, `docker-compose.production.yml` - Compose surfaces to validate before publication.
- `.env.example`, `.env.llamacpp.example`, `.env.production.example` - config templates required by Compose validation and public docs.
- `THIRD-PARTY-NOTICES.md`, `SECURITY.md`, `CONTRIBUTING.md`, `LICENSE` - public release and governance files.

## Build & Run
- **Containerized:** yes
- **Build command:** `docker compose --env-file .env.example -f docker-compose.yml --profile bundled-models config`
- **Test command:** `python -m pytest semantic-chunking-service\tests orchestrator\tests thorondor_cli\tests -v`

Additional verification commands:
- `python -m compileall orchestrator semantic-chunking-service ssrf-proxy thorondor_cli -q`
- `docker compose --env-file .env.example --env-file .env.llamacpp.example -f docker-compose.yml -f docker-compose.llamacpp.yml --profile llamacpp-models config`
- `docker compose --env-file .env.example --env-file .env.production.example -f docker-compose.production.yml config`
- `pwsh scripts/check-release-guard.ps1`
- `bash scripts/check-release-guard.sh`

## Phase 1 - Confirm Source Tree
**Status:** pending
**Kind:** logic

### Tasks
- [ ] Fetch and prune `origin`.
- [ ] Verify local worktree is clean.
- [ ] Confirm whether the final public snapshot should come from `addTui` (`83d0f77`) or from `origin/main`.
- [ ] If `addTui` is the source, decide whether to merge it into private `main` before archiving or publish it directly as the public single-commit tree.
- [ ] List tags, releases, package images, and workflow runs that should be preserved, recreated, or intentionally left behind.
- [ ] Run a tracked-file secret scan before exporting any public snapshot.

### Verification
- [ ] Record `git status --short --branch`.
- [ ] Record `git rev-list --left-right --count origin/main...<source>`.
- [ ] Record selected source commit and tree hash with `git rev-parse <source>` and `git rev-parse <source>^{tree}`.
- [ ] Record current GitHub visibility, default branch, topics, homepage, description, workflows, branch protections, rulesets, and Actions permissions.

## Phase 2 - Re-Verify Public Readiness
**Status:** pending
**Kind:** mixed

### Tasks
- [ ] Run offline tests from the selected source tree.
- [ ] Compile Python sources.
- [ ] Validate bundled Compose config.
- [ ] Validate llama.cpp Compose config.
- [ ] Validate production Compose config.
- [ ] Run release guard in PowerShell and Bash where available.
- [ ] Review publication-facing docs for stale public-alpha, release, or install statements after the clean-repo migration plan.
- [ ] Decide whether `.github/workflows/sonar.yml` should remain public or be kept only in the private archive.

### Verification
- [ ] `python -m pytest semantic-chunking-service\tests orchestrator\tests thorondor_cli\tests -v` passes.
- [ ] `python -m compileall orchestrator semantic-chunking-service ssrf-proxy thorondor_cli -q` passes.
- [ ] All three documented `docker compose ... config` commands pass.
- [ ] `scripts/check-release-guard.ps1` and `scripts/check-release-guard.sh` pass or any shell/platform blocker is documented.
- [ ] Public docs match the final repository shape and do not point users to private-only history.

## Phase 3 - Archive Existing Repository Privately
**Status:** pending
**Kind:** logic

### Tasks
- [ ] Pick archive name, recommended `FeanorsCodeSL/thorondor-private` unless occupied.
- [ ] Rename existing `FeanorsCodeSL/thorondor` to the archive name.
- [ ] Change archive visibility to private if the rename leaves it public.
- [ ] Update the local full-history checkout remote to the archive URL.
- [ ] Verify all existing branches, tags, PRs, releases, rulesets, and GHCR links remain associated with the archive or are intentionally deprecated.

### Verification
- [ ] `gh repo view FeanorsCodeSL/thorondor-private --json visibility,isPrivate,defaultBranchRef,url` shows private archive state.
- [ ] `git remote -v` in `C:\git\FEANORS-CODE\thorondor` points to the private archive.
- [ ] The public `FeanorsCodeSL/thorondor` name becomes available for clean publication.

## Phase 4 - Publish Clean Single-Commit Public Repo
**Status:** pending
**Kind:** logic

### Tasks
- [ ] Create a new empty public `FeanorsCodeSL/thorondor` repository.
- [ ] Reapply metadata: description, homepage, topics, issues/projects/wiki choices.
- [ ] Export the selected final tree with `git archive` into a new local directory such as `C:\git\FEANORS-CODE\thorondor-public-<timestamp>`.
- [ ] Initialize a new Git repo on `main` in that directory.
- [ ] Commit once with subject `Initial public release`.
- [ ] Verify the clean repo has exactly one commit.
- [ ] Verify the clean repo tree hash equals the selected source tree hash, including executable file modes.
- [ ] Push `main` to the new public repo. If automation is blocked from publishing private-origin content to a public remote, stop and hand the exact verified `git push -u origin main` command to the user.

### Verification
- [ ] `git rev-list --count HEAD` in the clean public directory returns `1`.
- [ ] `git rev-parse HEAD^{tree}` in the clean public directory matches the selected source tree hash.
- [ ] `gh repo view FeanorsCodeSL/thorondor --json visibility,isPrivate,defaultBranchRef,url` shows `PUBLIC` and default branch `main` after push.
- [ ] The first public commit contains no private Git history.

## Phase 5 - Recreate CI, Releases, and Protections
**Status:** pending
**Kind:** logic

### Tasks
- [ ] Let `release-guard` run on the clean public `main` push.
- [ ] Verify required public checks and exact job names from GitHub Actions.
- [ ] Set default workflow permissions to read-only.
- [ ] Keep write permissions only inside workflows that require them, especially `publish-images.yml` for `contents: write` and `packages: write`.
- [ ] Protect `main`: block force pushes and deletions, enforce admins, restrict pushes to `feanorscode`, require conversation resolution, require linear history if compatible with the chosen workflow, and require the green `release-guard` checks.
- [ ] Recreate a ruleset for the default branch and any additional branch restrictions needed for the public repo.
- [ ] Decide whether to recreate tag `v0.1.0` in the clean public repo or publish a new tag such as `v0.1.1` to avoid ambiguity with already-public historical artifacts.
- [ ] If image publication is still desired, run or tag-trigger `publish-images` and verify GHCR visibility and digest assets.

### Verification
- [ ] `gh run list --repo FeanorsCodeSL/thorondor --branch main --workflow release-guard --limit 1` shows success.
- [ ] `gh api repos/FeanorsCodeSL/thorondor/actions/permissions/workflow` shows read-only default token permissions.
- [ ] `gh api repos/FeanorsCodeSL/thorondor/branches/main/protection` shows required checks, force-push/deletion blocks, admin enforcement, and `feanorscode` push restrictions.
- [ ] `gh api repos/FeanorsCodeSL/thorondor/rulesets` shows the expected active rulesets.
- [ ] GHCR/release state is either recreated and verified or explicitly documented as intentionally not republished.

## Phase 6 - Final Handoff
**Status:** pending
**Kind:** logic

### Tasks
- [ ] Record final private archive URL.
- [ ] Record final public repo URL.
- [ ] Record public clean commit SHA and source tree hash.
- [ ] Record CI run URL and status.
- [ ] Record remaining caveats, especially the already-public historical exposure boundary.
- [ ] Confirm which local checkout should be used for future public work: the full-history private archive or the clean public clone.

### Verification
- [ ] Final report includes exact repository URLs, commit SHA, checks run, GitHub protections, and any manual push or visibility steps that automation could not perform.
