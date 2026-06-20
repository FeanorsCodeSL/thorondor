"""Dashboard tests for the Thorondor TUI."""

from __future__ import annotations

import asyncio

import pytest
from textual.widgets import DataTable, Footer, OptionList, Static
from thorondor_cli.state import ConfigAnswers, persist_env_changes
from thorondor_cli.tui import ThorondorApp
from thorondor_cli.tui.screens.dashboard import DashboardScreen
from thorondor_cli.tui.screens.deploy import DeployScreen
from thorondor_cli.tui.screens.endpoint_form import EndpointFormScreen
from thorondor_cli.tui.screens.harness import HarnessScreen
from thorondor_cli.tui.screens.model_mode import ModelModeScreen
from thorondor_cli.tui.screens.search_settings import SearchSettingsScreen
from thorondor_cli.tui.screens.validate import ValidateScreen

pytest.importorskip("textual")


def _static(widget) -> str:
    return str(widget.renderable) if hasattr(widget, "renderable") else str(widget.content)


def _make_project(tmp_path):
    root = tmp_path / "thorondor"
    root.mkdir()
    (root / "searxng").mkdir()
    (root / "searxng" / "settings.yml").write_text(
        "use_default_settings: true\n", encoding="utf-8"
    )
    (root / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    (root / "docker-compose.llamacpp.yml").write_text("services: {}\n", encoding="utf-8")
    return root


def _brand_text(app: ThorondorApp) -> str:
    return _static(app.screen.query_one("#brand", Static))


def _paths_text(app: ThorondorApp) -> str:
    return _static(app.screen.query_one("#paths", Static))


def _issues_text(app: ThorondorApp) -> str:
    return _static(app.screen.query_one("#issues", Static))


def _harness_text(app: ThorondorApp) -> str:
    return _static(app.screen.query_one("#harness", Static))


def _components_text(app: ThorondorApp) -> str:
    table = app.screen.query_one("#components", DataTable)
    return "\n".join(
        " ".join(str(cell) for cell in table.get_row_at(index))
        for index in range(table.row_count)
    )


def _action_labels(app: ThorondorApp) -> list[str]:
    menu = app.screen.query_one("#action-menu", OptionList)
    return [str(menu.get_option_at_index(index).prompt) for index in range(menu.option_count)]


async def _wait_for(pilot, predicate, message: str) -> None:
    for _ in range(80):
        await pilot.pause(0.05)
        if predicate():
            return
    raise AssertionError(message)


def test_dashboard_renders_brand_and_components_for_configured_project(tmp_path):
    root = _make_project(tmp_path)
    persist_env_changes(root, ConfigAnswers(mode="bundled-models"))

    async def run() -> None:
        app = ThorondorApp(root)
        async with app.run_test(size=(120, 36)):
            brand = _brand_text(app)
            paths = _paths_text(app)
            components = _components_text(app)
            assert "Search Stack Configurator" in brand
            assert "Fëanor's Code" in brand
            assert "O(log n)" in brand
            assert "◆" in brand
            assert "✦" in brand
            assert "╔" in brand and "║" in brand
            assert "mode: bundled-models" in paths
            assert "searxng" in components
            assert "crawl4ai" in components
            assert "chunker" in components
            assert "embedding" in components
            assert "reranker" in components
            assert "llm planner" in components
            assert "configured" in components

    asyncio.run(run())


def test_dashboard_compact_brand_keeps_marks_on_narrow_terminal(tmp_path):
    root = _make_project(tmp_path)

    async def run() -> None:
        app = ThorondorApp(root)
        async with app.run_test(size=(80, 24)):
            brand = _brand_text(app)
            assert "Search Stack Configurator" in brand
            assert "Fëanor's Code" in brand
            assert "O(log n)" in brand
            assert "◆" in brand

    asyncio.run(run())


def test_dashboard_shows_issues_for_unconfigured_project(tmp_path):
    root = _make_project(tmp_path)

    async def run() -> None:
        app = ThorondorApp(root)
        async with app.run_test(size=(120, 36)):
            issues = _issues_text(app)
            assert "Missing .env" in issues
            assert "Mode" in issues or "Validate" in issues
            harness = _harness_text(app)
            assert "claude-code" in harness
            assert "codex" in harness
            assert "opencode" in harness

    asyncio.run(run())


def test_dashboard_action_menu_lists_thorondor_screens(tmp_path):
    root = _make_project(tmp_path)

    async def run() -> None:
        app = ThorondorApp(root)
        async with app.run_test(size=(120, 36)):
            assert _action_labels(app) == [
                "Mode",
                "Endpoints",
                "Search/crawl",
                "Validate",
                "Deploy",
                "Wire MCP",
                "Refresh state",
                "Quit",
            ]

    asyncio.run(run())


def test_dashboard_missing_project_degrades_actions_to_wire_and_quit(tmp_path):
    root = _make_project(tmp_path)
    (root / "docker-compose.yml").unlink()

    async def run() -> None:
        app = ThorondorApp(root)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            assert _action_labels(app) == ["Wire MCP", "Quit"]

    asyncio.run(run())


def test_dashboard_no_footer_or_letter_shortcut_bindings(tmp_path):
    root = _make_project(tmp_path)

    async def run() -> None:
        app = ThorondorApp(root)
        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.pause()
            assert len(list(app.screen.query(Footer))) == 0
            for key in ("m", "e", "s", "v", "d", "w", "q"):
                await pilot.press(key)
                await pilot.pause()
                assert isinstance(app.screen, DashboardScreen)

    asyncio.run(run())


def test_dashboard_action_menu_has_initial_focus_and_arrow_keys_move(tmp_path):
    root = _make_project(tmp_path)

    async def run() -> None:
        app = ThorondorApp(root)
        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.pause()
            menu = app.screen.query_one("#action-menu", OptionList)
            assert menu.has_focus
            assert menu.highlighted == 0
            await pilot.press("down")
            assert menu.highlighted == 1
            await pilot.press("up")
            assert menu.highlighted == 0

    asyncio.run(run())


def test_dashboard_right_then_left_toggles_focus_between_components_and_actions(tmp_path):
    root = _make_project(tmp_path)
    persist_env_changes(root, ConfigAnswers(mode="bundled-models"))

    async def run() -> None:
        app = ThorondorApp(root)
        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.pause()
            menu = app.screen.query_one("#action-menu", OptionList)
            table = app.screen.query_one("#components", DataTable)
            assert menu.has_focus
            await pilot.press("right")
            assert table.has_focus
            await pilot.press("left")
            assert menu.has_focus

    asyncio.run(run())


def test_dashboard_enter_on_highlighted_mode_opens_model_mode(tmp_path):
    root = _make_project(tmp_path)

    async def run() -> None:
        app = ThorondorApp(root)
        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, ModelModeScreen)

    asyncio.run(run())


def test_dashboard_enter_on_endpoints_opens_endpoint_form(tmp_path):
    root = _make_project(tmp_path)

    async def run() -> None:
        app = ThorondorApp(root)
        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.pause()
            await pilot.press("down", "enter")
            await pilot.pause()
            assert isinstance(app.screen, EndpointFormScreen)

    asyncio.run(run())


def test_dashboard_enter_on_search_opens_search_settings(tmp_path):
    root = _make_project(tmp_path)

    async def run() -> None:
        app = ThorondorApp(root)
        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.pause()
            await pilot.press("down", "down", "enter")
            await pilot.pause()
            assert isinstance(app.screen, SearchSettingsScreen)

    asyncio.run(run())


def test_dashboard_enter_on_validate_opens_validate_screen(tmp_path):
    root = _make_project(tmp_path)

    async def run() -> None:
        app = ThorondorApp(root)
        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.pause()
            for _ in range(3):
                await pilot.press("down")
                await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, ValidateScreen)

    asyncio.run(run())


def test_dashboard_enter_on_deploy_opens_deploy_screen(tmp_path):
    root = _make_project(tmp_path)

    async def run() -> None:
        app = ThorondorApp(root)
        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.pause()
            await pilot.press("down", "down", "down", "down", "enter")
            await pilot.pause()
            assert isinstance(app.screen, DeployScreen)

    asyncio.run(run())


def test_dashboard_enter_on_wire_opens_harness_screen(tmp_path):
    root = _make_project(tmp_path)

    async def run() -> None:
        app = ThorondorApp(root)
        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.pause()
            for _ in range(5):
                await pilot.press("down")
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, HarnessScreen)

    asyncio.run(run())


def test_dashboard_enter_on_quit_exits(tmp_path):
    root = _make_project(tmp_path)

    async def run() -> None:
        app = ThorondorApp(root)
        async with app.run_test(size=(120, 36)) as pilot:
            for _ in range(7):
                await pilot.press("down")
            await pilot.press("enter")

        assert app.return_value == 0

    asyncio.run(run())


def test_dashboard_refresh_action_reloads_state(tmp_path):
    root = _make_project(tmp_path)

    async def run() -> None:
        app = ThorondorApp(root)
        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.pause()
            assert "Missing .env" in _issues_text(app)
            persist_env_changes(root, ConfigAnswers(mode="bundled-models"))
            for _ in range(6):
                await pilot.press("down")
                await pilot.pause()
            await pilot.press("enter")
            await _wait_for(
                pilot,
                lambda: "Missing .env" not in _issues_text(app),
                "refresh state did not pick up persisted env",
            )

    asyncio.run(run())


def test_dashboard_brand_uses_thorondor_outer_title_and_subtitle(tmp_path):
    root = _make_project(tmp_path)

    async def run() -> None:
        app = ThorondorApp(root)
        async with app.run_test(size=(120, 36)):
            outer = app.screen.query_one("#dashboard")
            assert "thorondor" in str(outer.border_title)
            assert "✦" in str(outer.border_title)
            assert "navigate" in str(outer.border_subtitle).lower()
            assert "Actions" in str(
                app.screen.query_one("#dashboard-nav").border_title
            )
            assert "Stack" in str(
                app.screen.query_one("#dashboard-main").border_title
            )

    asyncio.run(run())
