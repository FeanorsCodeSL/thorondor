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

if grep -nE '\$\{[^}]+:-|\$\{[^}:]+-[^}]' "${runtime_files[@]}"; then
  echo "Runtime Compose configuration must not use interpolation fallback values; declare values explicitly in env files." >&2
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

if ! grep -Eq 'image:[[:space:]]+unclecode/crawl4ai@sha256:' docker-compose.yml; then
  echo "docker-compose.yml: crawl4ai image must be pinned by digest." >&2
  exit 1
fi

for dockerfile in orchestrator/Dockerfile semantic-chunking-service/Dockerfile ssrf-proxy/Dockerfile; do
  if ! grep -Eq '^USER[[:space:]]+' "$dockerfile"; then
    echo "$dockerfile: first-party images must run as a non-root user." >&2
    exit 1
  fi
done

if ! grep -q 'health.dependencies.PSObject.Properties' scripts/deploy.ps1; then
  echo "scripts/deploy.ps1: deploy health polling must inspect dependency values." >&2
  exit 1
fi

if ! grep -q 'dependencies' scripts/deploy.sh; then
  echo "scripts/deploy.sh: deploy health polling must inspect dependency values." >&2
  exit 1
fi

echo "Release guard passed."
