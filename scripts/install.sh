#!/bin/sh
set -eu

if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi

if [ -d "$HOME/.local/bin" ]; then
  export PATH="$HOME/.local/bin:$PATH"
fi

uv tool install --force git+https://github.com/FeanorsCodeSL/thorondor

cat <<'MSG'
Installed Thorondor commands:
  thorondor       Textual search stack configurator
  thorondor-mcp   Native stdio MCP proxy

Run `thorondor` next.
MSG
