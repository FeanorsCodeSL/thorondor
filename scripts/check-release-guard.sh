#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
PYTHON_BIN="${PYTHON:-python3}"

runtime_files=(
  docker-compose.yml
  docker-compose.llamacpp.yml
  docker-compose.production.yml
  .env.example
  .env.llamacpp.example
  .env.production.example
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

if grep -nE '^[[:space:]]+build:[[:space:]]*$' docker-compose.production.yml; then
  echo "docker-compose.production.yml must consume published images only." >&2
  exit 1
fi

first_party_images=(
  'THORONDOR_ORCHESTRATOR_IMAGE=ghcr.io/feanorscodesl/thorondor-orchestrator'
  'THORONDOR_CHUNKER_IMAGE=ghcr.io/feanorscodesl/thorondor-chunker'
  'THORONDOR_EGRESS_PROXY_IMAGE=ghcr.io/feanorscodesl/thorondor-egress-proxy'
)

for image_entry in "${first_party_images[@]}"; do
  image_var="${image_entry%%=*}"
  image_ref="${image_entry#*=}"
  if ! grep -Eq "^${image_var}=${image_ref}(:|@sha256:)" .env.production.example; then
    echo ".env.production.example: ${image_var} must point at ${image_ref} by tag or digest." >&2
    exit 1
  fi
done

for image_var in THORONDOR_SEARXNG_IMAGE THORONDOR_CRAWL4AI_IMAGE; do
  if ! grep -Eq "^${image_var}=.+@sha256:" .env.production.example; then
    echo ".env.production.example: ${image_var} must be pinned by digest." >&2
    exit 1
  fi
done

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

if ! cmp -s .env.example thorondor_cli/templates/env.example; then
  echo "thorondor_cli/templates/env.example must match .env.example." >&2
  exit 1
fi

if ! cmp -s .env.llamacpp.example thorondor_cli/templates/env.llamacpp.example; then
  echo "thorondor_cli/templates/env.llamacpp.example must match .env.llamacpp.example." >&2
  exit 1
fi

if ! cmp -s .env.production.example thorondor_cli/templates/env.production.example; then
  echo "thorondor_cli/templates/env.production.example must match .env.production.example." >&2
  exit 1
fi

asset_pairs=(
  'docker-compose.llamacpp.yml thorondor_cli/assets/docker-compose.llamacpp.yml'
  'searxng/settings.yml thorondor_cli/assets/searxng/settings.yml'
  'searxng/limiter.toml thorondor_cli/assets/searxng/limiter.toml'
)

for pair in "${asset_pairs[@]}"; do
  read -r source target <<< "$pair"
  if ! cmp -s "$source" "$target"; then
    echo "$target must match $source." >&2
    exit 1
  fi
done

if grep -nE '^[[:space:]]+build:[[:space:]]*$' thorondor_cli/assets/docker-compose.yml; then
  echo "thorondor_cli/assets/docker-compose.yml must consume published images only." >&2
  exit 1
fi

docker compose \
  --env-file thorondor_cli/templates/env.example \
  -f thorondor_cli/assets/docker-compose.yml \
  config >/dev/null

if ! grep -q 'uv tool install --force git+https://github.com/FeanorsCodeSL/thorondor' scripts/install.sh; then
  echo "scripts/install.sh must install the canonical Thorondor repository with uv tool install." >&2
  exit 1
fi

if ! grep -q 'uv tool install --force git+https://github.com/FeanorsCodeSL/thorondor' scripts/install.ps1; then
  echo "scripts/install.ps1 must install the canonical Thorondor repository with uv tool install." >&2
  exit 1
fi

if grep -iq 'checkout' scripts/install.sh scripts/install.ps1; then
  echo "Install scripts must not require a Thorondor checkout after installation." >&2
  exit 1
fi

sh -n scripts/install.sh

overlay_dir="$(mktemp -d)"
overlay="$overlay_dir/docker-compose.host-endpoints.yml"
cleanup_overlay() {
  rm -rf "$overlay_dir"
}
trap cleanup_overlay EXIT
"$PYTHON_BIN" - "$overlay_dir" <<'PY'
import sys
from thorondor_cli.state import write_host_endpoints_overlay

write_host_endpoints_overlay(sys.argv[1], host_rewritten=True)
PY
docker compose --env-file .env.example -f docker-compose.yml -f "$overlay" config >/dev/null

echo "Release guard passed."
