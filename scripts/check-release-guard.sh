#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

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

declare -A first_party_images=(
  [THORONDOR_ORCHESTRATOR_IMAGE]='ghcr.io/feanorscodesl/thorondor-orchestrator'
  [THORONDOR_CHUNKER_IMAGE]='ghcr.io/feanorscodesl/thorondor-chunker'
  [THORONDOR_EGRESS_PROXY_IMAGE]='ghcr.io/feanorscodesl/thorondor-egress-proxy'
)

for image_var in "${!first_party_images[@]}"; do
  image_ref="${first_party_images[$image_var]}"
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

echo "Release guard passed."
