"""Form-screen tests for the Thorondor TUI."""

from __future__ import annotations

import asyncio
import json
import tomllib

import httpx
import pytest
import respx
from textual.widgets import Static
from thorondor_cli.models import LLAMACPP_MODEL_SOURCES
from thorondor_cli.state import ConfigAnswers, persist_env_changes
from thorondor_cli.tui import ThorondorApp
from thorondor_cli.tui.screens.dashboard import DashboardScreen
from thorondor_cli.tui.screens.endpoint_form import EndpointFormScreen
from thorondor_cli.tui.screens.harness import HarnessScreen
from thorondor_cli.tui.screens.model_mode import ModelModeScreen

pytest.importorskip("textual")


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


def test_model_mode_apply_persists_bundled_models_to_env(tmp_path):
    root = _make_project(tmp_path)

    async def run() -> None:
        app = ThorondorApp(root)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, ModelModeScreen)
            app.screen._save("bundled-models")  # noqa: SLF001
            await pilot.pause()

    asyncio.run(run())

    env_text = (root / ".env").read_text(encoding="utf-8")
    assert "EMBEDDING_ENDPOINT=http://embedding:80" in env_text
    assert "RERANKER_ENDPOINT=http://reranker:80" in env_text
    assert "SEARXNG_SECRET=" in env_text


def test_model_mode_arrow_keys_move_focus_through_buttons(tmp_path):
    root = _make_project(tmp_path)

    async def run() -> None:
        app = ThorondorApp(root)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, ModelModeScreen)
            byo = app.screen.query_one("#byo")
            bundled = app.screen.query_one("#bundled")
            llamacpp = app.screen.query_one("#llamacpp")
            assert byo.has_focus
            await pilot.press("down")
            assert bundled.has_focus
            await pilot.press("down")
            assert llamacpp.has_focus
            await pilot.press("up")
            assert bundled.has_focus

    asyncio.run(run())


def test_harness_screen_apply_wires_claude_code_with_stdio(tmp_path):
    root = _make_project(tmp_path)
    home = tmp_path / "home"
    home.mkdir()

    async def run() -> None:
        app = ThorondorApp(root, home=home)
        async with app.run_test(size=(120, 40)) as pilot:
            for _ in range(5):
                await pilot.press("down")
                await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, HarnessScreen)
            app.screen.query_one("#harness-claude-code").value = True
            await pilot.click("#apply-harnesses")
            await pilot.pause()

    asyncio.run(run())

    claude = json.loads((home / ".claude.json").read_text(encoding="utf-8"))
    assert claude["mcpServers"]["thorondor"]["command"] == "thorondor-mcp"
    assert "THORONDOR_BASE_URL" in claude["mcpServers"]["thorondor"]["env"]


def test_harness_screen_apply_wires_codex_with_http(tmp_path):
    root = _make_project(tmp_path)
    home = tmp_path / "home"
    home.mkdir()

    async def run() -> None:
        from textual.widgets import Select

        app = ThorondorApp(root, home=home)
        async with app.run_test(size=(120, 40)) as pilot:
            for _ in range(5):
                await pilot.press("down")
                await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, HarnessScreen)
            app.screen.query_one("#harness-codex").value = True
            delivery = app.screen.query_one("#harness-delivery", Select)
            delivery.value = "http"
            await pilot.click("#apply-harnesses")
            await pilot.pause()

    asyncio.run(run())

    codex = tomllib.loads(
        (home / ".codex" / "config.toml").read_text(encoding="utf-8")
    )
    assert codex["mcp_servers"]["thorondor"]["type"] == "http"
    assert codex["mcp_servers"]["thorondor"]["url"] == "http://localhost:8080/mcp"


def test_harness_screen_cancel_pops_back_to_dashboard(tmp_path):
    root = _make_project(tmp_path)
    home = tmp_path / "home"
    home.mkdir()

    async def run() -> None:
        app = ThorondorApp(root, home=home)
        async with app.run_test(size=(120, 40)) as pilot:
            for _ in range(5):
                await pilot.press("down")
                await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, HarnessScreen)
            await pilot.press("escape")
            await pilot.pause()
            assert isinstance(app.screen, DashboardScreen)

    asyncio.run(run())


def test_endpoint_form_cancel_does_not_write_env(tmp_path):
    root = _make_project(tmp_path)
    persist_env_changes(root, ConfigAnswers(mode="byo"))
    original = (root / ".env").read_text(encoding="utf-8")

    async def run() -> None:
        app = ThorondorApp(root)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press("down", "enter")
            await pilot.pause()
            assert isinstance(app.screen, EndpointFormScreen)
            await pilot.press("escape")
            await pilot.pause()
            assert isinstance(app.screen, DashboardScreen)

    asyncio.run(run())

    assert (root / ".env").read_text(encoding="utf-8") == original


def test_model_mode_llamacpp_auto_downloads_then_saves(tmp_path):
    root = _make_project(tmp_path)
    embedding_payload = b"GGUF\x00" * 600
    reranker_payload = b"GGUF\x11" * 600

    async def run() -> None:
        with respx.mock(assert_all_called=True) as router:
            router.get(LLAMACPP_MODEL_SOURCES["bge-m3.gguf"]).mock(
                return_value=httpx.Response(200, content=embedding_payload)
            )
            router.get(LLAMACPP_MODEL_SOURCES["bge-reranker-v2-m3.gguf"]).mock(
                return_value=httpx.Response(200, content=reranker_payload)
            )
            app = ThorondorApp(root)
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.press("enter")
                await pilot.pause()
                assert isinstance(app.screen, ModelModeScreen)
                await pilot.click("#llamacpp")
                for _ in range(60):
                    await pilot.pause(0.05)
                    if (root / "models" / "bge-m3.gguf").exists() and (
                        root / "models" / "bge-reranker-v2-m3.gguf"
                    ).exists() and not isinstance(app.screen, ModelModeScreen):
                        break
                assert isinstance(app.screen, DashboardScreen)

    asyncio.run(run())

    assert (root / "models" / "bge-m3.gguf").read_bytes() == embedding_payload
    assert (root / "models" / "bge-reranker-v2-m3.gguf").read_bytes() == reranker_payload
    env_text = (root / ".env").read_text(encoding="utf-8")
    llamacpp_text = (root / ".env.llamacpp").read_text(encoding="utf-8")
    assert "EMBEDDING_ENDPOINT=http://embedding:8080" in env_text
    assert "RERANKER_ENDPOINT=http://reranker:8080" in env_text
    assert "LLAMACPP_IMAGE=" in llamacpp_text


def test_model_mode_download_button_fetches_models_without_changing_mode(tmp_path):
    root = _make_project(tmp_path)
    payload = b"GGUF\x22" * 200

    async def run() -> None:
        with respx.mock(assert_all_called=True) as router:
            router.get(LLAMACPP_MODEL_SOURCES["bge-m3.gguf"]).mock(
                return_value=httpx.Response(200, content=payload)
            )
            router.get(LLAMACPP_MODEL_SOURCES["bge-reranker-v2-m3.gguf"]).mock(
                return_value=httpx.Response(200, content=payload)
            )
            app = ThorondorApp(root)
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.press("enter")
                await pilot.pause()
                assert isinstance(app.screen, ModelModeScreen)
                await pilot.click("#download-models")
                for _ in range(60):
                    await pilot.pause(0.05)
                    if (root / "models" / "bge-reranker-v2-m3.gguf").exists():
                        break
                assert isinstance(app.screen, ModelModeScreen)
                await pilot.press("escape")
                await pilot.pause()
                assert isinstance(app.screen, DashboardScreen)

    asyncio.run(run())

    assert (root / "models" / "bge-m3.gguf").exists()
    assert (root / "models" / "bge-reranker-v2-m3.gguf").exists()
    assert not (root / ".env").exists()


def test_model_mode_download_error_is_surfaced_in_status(tmp_path):
    root = _make_project(tmp_path)

    async def run() -> None:
        with respx.mock(assert_all_called=True) as router:
            router.get(LLAMACPP_MODEL_SOURCES["bge-m3.gguf"]).mock(
                return_value=httpx.Response(500, text="upstream down")
            )
            app = ThorondorApp(root)
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.press("enter")
                await pilot.pause()
                assert isinstance(app.screen, ModelModeScreen)
                await pilot.click("#download-models")
                for _ in range(60):
                    await pilot.pause(0.05)
                    status = str(app.screen.query_one("#mode-status", Static).content)
                    if "failed" in status.lower():
                        break
                assert isinstance(app.screen, ModelModeScreen)
                assert "failed" in status.lower()
                assert not (root / "models" / "bge-m3.gguf").exists()

    asyncio.run(run())
