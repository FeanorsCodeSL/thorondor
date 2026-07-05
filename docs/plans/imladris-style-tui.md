# Restyle Thorondor TUI to match Imladris visual & navigation

## Goal
Make the Thorondor `thorondor` Textual TUI feel like a sibling of the
`imladris` TUI: same color palette, same chrome, same arrow-key
navigation, same widget choices, same per-screen structure — but keep
Thorondor's specific use cases (Mode / Endpoints / Search / Validate /
Deploy / MCP) instead of Imladris's council model.

The two products must read as shipped by the same developer / same
company.

## References
- `thorondor_cli/tui/app.py` — current Thorondor app shell
- `thorondor_cli/tui/thorondor.tcss` — current Thorondor CSS
- `thorondor_cli/tui/screens/dashboard.py` — current Thorondor dashboard
- `thorondor_cli/tui/screens/{model_mode,endpoint_form,search_settings,validate,deploy,harness}.py` — current Thorondor screens
- `imladris/orchestrator/cli/tui/app.py` — reference app shell
- `imladris/orchestrator/cli/tui/app.tcss` — reference CSS
- `imladris/orchestrator/cli/tui/screens/navigation.py` — ArrowNavigationMixin
- `imladris/orchestrator/cli/tui/screens/dashboard.py` — reference dashboard
- `imladris/orchestrator/cli/tui/screens/{member_form,defaults,delete_member,roles,harness}.py` — reference form screens
- `thorondor_cli/state.py` — Draft, Issue, ConfigAnswers, persist_env_changes
- `thorondor_cli/probe.py` — probe_embedding / probe_reranker / probe_llm
- `thorondor_cli/harness/` — wire_claude_code / wire_codex / wire_opencode
- `thorondor_cli/tests/test_tui_dashboard.py` — existing dashboard tests (will be rewritten)

## Build & run
- **Containerized:** no
- **Build command:** n/a
- **Test command:** `python -m pytest thorondor_cli\tests -v`

## Phase 1 — Shared shell (CSS, app, navigation mixin)
**Status:** pending
**Kind:** ui

### Tasks
- [ ] Replace `thorondor_cli/tui/thorondor.tcss` with the Imladris palette
      (#0B0B0B background, #F2C04B foreground, #5C4520 borders, #D6A93D
      field labels), double border on the outer `Screen`, `round` border
      on the side panels, and selectors matching the new widget tree
      (`#dashboard`, `#dashboard-body`, `#dashboard-nav`,
      `#dashboard-main`, `#brand`, `#paths`, `#action-menu`, `#components`,
      `#roles`, `#issues`, `#harness`).
- [ ] Rewrite `thorondor_cli/tui/app.py` to mirror `ImladrisApp`:
      `TITLE = "thorondor"`, `SUB_TITLE = "Search Stack Configurator"`,
      drop the `Footer`/letter-key bindings, hold the project root, and
      add `refresh_dashboard_state()` plus `_detect_harnesses()` helpers
      that reuse the existing `harness.wire_*` writers for detection.
      Export the app from `thorondor_cli/tui/__init__.py`.
- [ ] Add `thorondor_cli/tui/screens/navigation.py` with the same
      `ARROW_NAV_BINDINGS` + `ArrowNavigationMixin` from Imladris (only
      changes: type-check / import path).

### Verification
- [ ] `python -c "from thorondor_cli.tui import ThorondorApp"` imports
      cleanly without raising.
- [ ] `pytest thorondor_cli/tests/test_tui_dashboard.py -x` runs (older
      assertions may fail — that is expected; Phase 4 updates them).
- [ ] Visually inspect `app.tcss`: no leftover `Footer`/`#action-bar`
      selectors and no yellow on black for header text.

## Phase 2 — Dashboard rewrite
**Status:** pending
**Kind:** ui

### Tasks
- [ ] Replace `thorondor_cli/tui/screens/dashboard.py` with the Imladris
      layout: vertical `dashboard` with brand, paths, then horizontal
      `dashboard-body` containing a left `dashboard-nav` (option list
      `#action-menu` + harness summary `#harness`) and right
      `dashboard-main` (`#components` DataTable + `#roles` and `#issues`
      status rows).
- [ ] Use a `thorondor` ASCII wordmark in `_brand()` that follows the
      same shape (5 lines, framed by `╔═...═╗` / `╚═...═╝`) and keeps
      the `Fëanor's Code` / `O(log n)` marks plus the
      `Search Stack Configurator` subtitle.
- [ ] Build `_populate_components()` to render the existing
      searxng / crawl4ai / chunker / embedding / reranker / llm-planner
      rows from the live `Draft` (mode, endpoints, overlay flag, token
      presence, etc.) — keep the data, drop the `║` framed text.
- [ ] Wire the action menu entries to the existing screens
      (`Mode`, `Endpoints`, `Search/crawl`, `Validate`, `Deploy`,
      `Wire MCP`, `Refresh deploy state`, `Quit`) and provide
      `action_*` methods. Bind `left` → focus actions, `right` →
      focus components table; do not register any letter shortcut
      bindings on the screen or app.
- [ ] `Border` titles: outer `◆  thorondor  ◆` with subtitle
      `↑↓ navigate · enter select`; left nav `◆ Actions ◆`; right main
      `◆ Components ◆` / `◆ Status ◆` as appropriate.

### Verification
- [ ] Pilot test: pressing `enter` on the highlighted `Mode` action
      pushes `ModelModeScreen`; pressing `right` then `down` moves
      through the components table.
- [ ] The `Missing .env` issue still surfaces for an un-configured
      project and the action menu degrades to `[Wire MCP, Quit]` (or
      similar) when no compose assets are present.

## Phase 3 — Form-screen restyle
**Status:** pending
**Kind:** ui

### Tasks
- [ ] `ModelModeScreen` (mode selection): title `Static(..., classes="brand")`
      with `id="mode-title"`, three Buttons for `BYO endpoints` /
      `bundled-models (TEI)` / `llamacpp (GGUF)` styled like Imladris
      (no `screen-title` header), `Apply` + `Cancel` row, `#mode-status`
      Static. Drop the `b`/`l`/`y` letter bindings; rely on
      `ArrowNavigationMixin` + tab/enter.
- [ ] `EndpointFormScreen` (endpoints): keep all existing fields
      (embedding, reranker, llm, paths, tokens) but render each one as
      `Static(..., classes="field-label")` + `Input` row inside a
      `Vertical(id="endpoint-form")`, add `#form-status` Static, replace
      `Test connection` / `Save` with `Test connection` / `Save` / `Cancel`
      row. Reuse `probe_embedding` / `probe_reranker` and the existing
      `rewrite_host_for_docker` preview logic.
- [ ] `SearchSettingsScreen` (search/crawl): same `field-label` +
      `Input` pattern over the existing `FIELDS`, `Save` / `Cancel`,
      `#search-status`.
- [ ] `ValidateScreen`: render as `Static(id="validate-title", classes="brand")`
      + `Vertical(id="validate-body", classes="status")` populated from
      `compute_issues` and `compose_args(compose_plan(...))`. Add a
      `Refresh` button that re-runs the check.
- [ ] `DeployScreen`: brand title, status Static fed by the existing
      `deploy()` progress iterator, `Start deploy` / `Back` row. Keep
      the `DeployError` handling and the `docker compose ps` hint.
- [ ] `HarnessScreen` (MCP): mirror Imladris's harness screen — three
      `Checkbox` rows for `claude-code`, `codex`, `opencode`, a
      `delivery` `Select` for `stdio` / `http`, an `Apply` / `Cancel`
      row, and `#harness-status`. Reuse the existing
      `wire_*` writers; the `delivery` choice selects between
      stdio writers and the documented Docker HTTP `/mcp` path.

### Verification
- [ ] Each screen mounts under `ImladrisApp`-style chrome with no
      `Footer` visible and no leftover `screen-title` class.
- [ ] End-to-end pilot: `Mode` → pick `bundled-models` → returns to
      dashboard; `Endpoints` → edit embedding endpoint → `Test
      connection` updates `#form-status`; `Validate` shows the
      compose command line.

## Phase 4 — Tests & docs
**Status:** pending
**Kind:** logic

### Tasks
- [ ] Rewrite `thorondor_cli/tests/test_tui_dashboard.py` to use the
      Imladris-style assertions: brand Static contains `thorondor`,
      `Search Stack Configurator`, `Fëanor's Code`, `O(log n)`, `◆`,
      `✦`; action menu exposes `Mode` / `Endpoints` / `Search/crawl` /
      `Validate` / `Deploy` / `Wire MCP` / `Quit`; pressing `enter` on
      the highlighted action pushes the right screen; no `Footer`
      widget exists on the screen; the components table is focused
      after pressing `right` on a configured project.
- [ ] Add `test_tui_mode_form.py` covering arrow navigation and the
      `bundled-models` save path.
- [ ] Add `test_tui_endpoints.py` covering `Test connection` status
      rendering and the docker host rewrite preview.
- [ ] Update `test_tui_state.py` only if a new helper was added.
- [ ] Update `README.md` "Quickstart — Textual Configurator" table to
      reflect the new action names (no `[m]` / `[e]` letter
      shortcuts) and the left/right arrow navigation.

### Verification
- [ ] `python -m pytest thorondor_cli/tests -v` passes.
- [ ] `python -m pytest thorondor_cli/tests/test_tui_dashboard.py -v`
      asserts the new branding, action list, and navigation.
