import subprocess

from thorondor_cli import app
from thorondor_cli.project import ensure_managed_project


def test_help_prints_both_commands(capsys):
    assert app.main(["--help"]) == 0
    out = capsys.readouterr().out
    assert "thorondor" in out
    assert "thorondor-mcp" in out
    assert "uninstall" in out
    assert "~/.thorondor" in out
    assert "Usage:" in out


def test_unknown_subcommand_returns_nonzero(capsys):
    assert app.main(["bogus"]) == 2
    err = capsys.readouterr().err
    assert "Unknown subcommand: bogus" in err


def test_install_scripts_pin_canonical_repo():
    shell = open("scripts/install.sh", encoding="utf-8").read()
    powershell = open("scripts/install.ps1", encoding="utf-8").read()
    expected = "uv tool install --force git+https://github.com/FeanorsCodeSL/thorondor"
    assert expected in shell
    assert expected in powershell
    assert "thorondor-mcp" in shell
    assert "thorondor-mcp" in powershell
    assert "Run `thorondor` next." in shell
    assert "Run `thorondor` next." in powershell
    assert "checkout" not in shell.lower()
    assert "checkout" not in powershell.lower()


def test_uninstall_removes_managed_project_without_removing_tool(monkeypatch, tmp_path, capsys):
    project = ensure_managed_project(tmp_path / "managed")
    calls = []

    def runner(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(app.shutil, "which", lambda command: "/usr/bin/docker")

    assert app.uninstall(project.root, keep_tool=True, runner=runner) == 0

    assert not project.root.exists()
    assert calls
    assert calls[0][-3:] == ["down", "--volumes", "--remove-orphans"]
    out = capsys.readouterr().out
    assert "Removed Thorondor managed files" in out
    assert "Kept the thorondor command" in out
