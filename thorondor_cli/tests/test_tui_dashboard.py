import asyncio

import pytest

from thorondor_cli.state import ConfigAnswers, persist_env_changes

pytest.importorskip("textual")

from thorondor_cli.tui.app import ThorondorApp


def _static_text(widget) -> str:
    return str(widget.content)


def make_project(tmp_path):
    root = tmp_path / "thorondor"
    root.mkdir()
    (root / "searxng").mkdir()
    (root / "searxng" / "settings.yml").write_text("use_default_settings: true\n", encoding="utf-8")
    (root / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    (root / "docker-compose.llamacpp.yml").write_text("services: {}\n", encoding="utf-8")
    return root


def test_dashboard_renders_brand_and_status_for_configured_project(tmp_path):
    root = make_project(tmp_path)
    persist_env_changes(root, ConfigAnswers(mode="bundled-models"))

    async def run() -> None:
        async with ThorondorApp(root).run_test(size=(120, 36)) as pilot:
            text = _static_text(pilot.app.screen.query_one("#dashboard-body"))
            header = _static_text(pilot.app.screen.query_one("#brand-header"))
            assert "thorondor" in str(header)
            assert "Fëanor's Code" in str(header)
            assert "O(log n)" in str(header)
            assert "❧" in str(header)
            assert "◈" in str(header)
            assert "╔═◈" in str(text)
            assert "mode: bundled-models" in str(text)
            assert "embedding" in str(text)
            assert "MCP" in str(text)

    asyncio.run(run())


def test_dashboard_renders_issue_list_for_empty_project(tmp_path):
    root = make_project(tmp_path)

    async def run() -> None:
        async with ThorondorApp(root).run_test(size=(80, 24)) as pilot:
            text = _static_text(pilot.app.screen.query_one("#dashboard-body"))
            header = _static_text(pilot.app.screen.query_one("#brand-header"))
            assert "Missing .env" in text
            assert "thorondor" in header
            assert "O(log n)" in header
            assert "Fëanor's Code" not in header
            assert "✦" in text
            action_bar = _static_text(pilot.app.screen.query_one("#action-bar"))
            assert "[m] Mode" in action_bar
            assert "[1]" in action_bar

    asyncio.run(run())
