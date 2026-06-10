#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.yml}"
PROFILE="${PROFILE:-bundled-models}"
HEALTH_URL="${HEALTH_URL:-http://localhost:8080/healthz}"
SKIP_SMOKE="${SKIP_SMOKE:-0}"

if [ ! -f .env ]; then
  cp .env.example .env
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

compose=(docker compose -f "$COMPOSE_FILE")
if [ -n "$PROFILE" ]; then
  compose+=(--profile "$PROFILE")
fi

"${compose[@]}" config >/dev/null
"${compose[@]}" build
"${compose[@]}" up -d

for attempt in $(seq 1 60); do
  if curl -fsS "$HEALTH_URL" >/dev/null 2>&1; then
    break
  fi
  if [ "$attempt" -eq 60 ]; then
    echo "Timed out waiting for $HEALTH_URL" >&2
    "${compose[@]}" ps
    exit 1
  fi
  sleep 2
done

if [ "$SKIP_SMOKE" != "1" ]; then
  scripts/smoke.sh
fi
