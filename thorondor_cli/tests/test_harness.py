import json

from thorondor_cli.harness.claude_code import wire_claude_code
from thorondor_cli.harness.codex import wire_codex
from thorondor_cli.harness.opencode import wire_opencode


def test_claude_code_wire_preserves_unrelated_entries(tmp_path):
    path = tmp_path / ".claude.json"
    path.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}}), encoding="utf-8")
    wire_claude_code(tmp_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["mcpServers"]["other"]["command"] == "x"
    assert data["mcpServers"]["thorondor"]["command"] == "thorondor-mcp"


def test_codex_wire_is_idempotent_and_can_switch_to_http(tmp_path):
    wire_codex(tmp_path)
    wire_codex(tmp_path, delivery="http")
    text = (tmp_path / ".codex" / "config.toml").read_text(encoding="utf-8")
    assert text.count("[mcp_servers.thorondor]") == 1
    assert 'type = "http"' in text
    assert "http://localhost:8080/mcp" in text


def test_opencode_wire_stdio_shape(tmp_path):
    wire_opencode(tmp_path)
    data = json.loads(
        (tmp_path / ".config" / "opencode" / "opencode.json").read_text(encoding="utf-8")
    )
    assert data["mcp"]["thorondor"]["type"] == "local"
    assert data["mcp"]["thorondor"]["command"] == ["thorondor-mcp"]
