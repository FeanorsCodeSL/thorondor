#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

runtime_files=(
  docker-compose.yml
  docker-compose.llamacpp.yml
  .env.example
  .env.llamacpp.example
)

if grep -n ':latest' "${runtime_files[@]}"; then
  echo "Runtime image configuration must not use :latest." >&2
  exit 1
fi

if awk '
  /^  searxng:[[:space:]]*$/ { in_searxng=1; next }
  in_searxng && /^  [A-Za-z0-9_-]+:[[:space:]]*$/ { in_searxng=0 }
  in_searxng && /^[[:space:]]+build:[[:space:]]*$/ { found=1 }
  END { exit found ? 0 : 1 }
' docker-compose.yml; then
  echo "docker-compose.yml: searxng service must not use build:" >&2
  exit 1
fi

echo "Release guard passed."
