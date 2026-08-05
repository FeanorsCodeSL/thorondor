#!/usr/bin/env bash
# Thorondor llama.cpp deploy wrapper. Auto-downloads missing GGUF model files
# via `thorondor download-models` before invoking the base deploy logic.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PROFILE="${PROFILE:-llamacpp-models}"
HEALTH_URL="${HEALTH_URL:-http://localhost:8080/healthz}"
SEARCH_URL="${SEARCH_URL:-http://localhost:8080/v1/search}"
SKIP_SMOKE="${SKIP_SMOKE:-0}"

if [ ! -f .env ]; then
  cp .env.example .env
fi
if [ ! -f .env.llamacpp ]; then
  cp .env.llamacpp.example .env.llamacpp
fi

ensure_env_value() {
  local key="$1"
  local value

  if grep -Eq "^${key}=.+" .env; then
    return
  fi

  value="$(head -c 48 /dev/urandom | base64 | tr -dc 'a-zA-Z0-9' | head -c 48)"
  if grep -Eq "^${key}=" .env; then
    sed -i.bak "s|^${key}=.*|${key}=${value}|" .env
    rm -f .env.bak
  else
    printf '\n%s=%s\n' "$key" "$value" >> .env
  fi
}

ensure_env_value SEARXNG_SECRET
ensure_env_value CRAWL4AI_API_KEY

read_env_value() {
  local key="$1"
  local path="$2"
  awk -F= -v k="$key" '
    $1 == k {
      sub(/^[^=]*=/, "", $0)
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", $0)
      gsub(/^["'\'']|["'\'']$/, "", $0)
      print
      exit
    }
  ' "$path"
}

llamacpp_embedding="$(read_env_value LLAMACPP_EMBEDDING_MODEL .env.llamacpp)"
llamacpp_reranker="$(read_env_value LLAMACPP_RERANKER_MODEL .env.llamacpp)"

if [ -z "$llamacpp_embedding" ] || [ -z "$llamacpp_reranker" ]; then
  echo "LLAMACPP_EMBEDDING_MODEL and LLAMACPP_RERANKER_MODEL must be set in .env.llamacpp." >&2
  exit 1
fi

needs_download=0
for container_path in "$llamacpp_embedding" "$llamacpp_reranker"; do
  if [[ "$container_path" == /models/* ]]; then
    local_path="models/${container_path#/models/}"
    if [ ! -f "$local_path" ]; then
      needs_download=1
    fi
  fi
done

if [ "$needs_download" -eq 1 ]; then
  if ! command -v thorondor >/dev/null 2>&1; then
    echo "thorondor CLI not found on PATH. Install with 'uv tool install .' or set up the project, then re-run this script." >&2
    exit 1
  fi
  thorondor download-models
fi

still_missing=0
for container_path in "$llamacpp_embedding" "$llamacpp_reranker"; do
  if [[ "$container_path" == /models/* ]]; then
    local_path="models/${container_path#/models/}"
    if [ ! -f "$local_path" ]; then
      echo "Still missing llama.cpp GGUF model file after download: $local_path" >&2
      still_missing=1
    fi
  fi
done
if [ "$still_missing" -ne 0 ]; then
  exit 1
fi

compose=(docker compose -f docker-compose.yml -f docker-compose.llamacpp.yml --env-file .env --env-file .env.llamacpp)
if [ -n "$PROFILE" ]; then
  compose+=(--profile "$PROFILE")
fi

"${compose[@]}" config >/dev/null
"${compose[@]}" build
"${compose[@]}" up -d

for attempt in $(seq 1 60); do
  health="$(curl -fsS "$HEALTH_URL" || true)"
  if echo "$health" | python3 -c 'import json,sys; d=json.load(sys.stdin); values=list(d.get("dependencies", {}).values()); sys.exit(0 if values and all(values) else 1)' 2>/dev/null; then
    break
  fi
  if [ "$attempt" -eq 60 ]; then
    echo "Timed out waiting for all dependencies at $HEALTH_URL" >&2
    echo "$health" >&2
    "${compose[@]}" ps
    exit 1
  fi
  sleep 2
done

if [ "$SKIP_SMOKE" != "1" ]; then
  SEARCH_URL="$SEARCH_URL" "$(dirname "${BASH_SOURCE[0]}")/smoke.sh"
fi
