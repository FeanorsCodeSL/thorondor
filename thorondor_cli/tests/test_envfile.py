import os

from thorondor_cli.envfile import read_env, seed_from_example, write_env


def test_seed_from_bundled_template_loads_required_keys_outside_repo(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    values = seed_from_example()
    assert "SEARXNG_URL" in values
    assert "SEARXNG_SECRET" in values
    assert "EMBEDDING_ENDPOINT" in values
    assert "RERANKER_ENDPOINT" in values


def test_write_env_preserves_existing_secret_and_quotes_values(tmp_path):
    target = tmp_path / ".env"
    target.write_text("SEARXNG_SECRET=keepme\nEXTRA=value\n", encoding="utf-8")
    template = {"SEARXNG_SECRET": "", "DOMAIN_BLOCKLIST": "", "EXTRA": ""}

    write_env(
        target,
        {"SEARXNG_SECRET": "replace-me", "DOMAIN_BLOCKLIST": "example.com # comment"},
        template_values=template,
        preserve_existing_nonblank=("SEARXNG_SECRET",),
    )

    values = read_env(target)
    assert values["SEARXNG_SECRET"] == "keepme"
    assert values["DOMAIN_BLOCKLIST"] == "example.com # comment"
    assert values["EXTRA"] == "value"


def test_missing_file_created_at_0600(tmp_path):
    target = tmp_path / ".env"
    write_env(target, {"A": "b"}, template_values={"A": ""})
    assert read_env(target) == {"A": "b"}
    if os.name == "posix":
        assert oct(target.stat().st_mode & 0o777) == "0o600"
