#!/usr/bin/env bash
set -euo pipefail

# The bundled embedding/reranker servers are GPU-oriented. CPU alternatives can
# be substituted through .env for low-volume development.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.yml}"
PROFILE="${PROFILE:-bundled-models}"
HEALTH_URL="${HEALTH_URL:-http://localhost:8080/healthz}"
SEARCH_URL="${SEARCH_URL:-http://localhost:8080/search}"

compose=(docker compose -f "$COMPOSE_FILE")
if [ -n "$PROFILE" ]; then
  compose+=(--profile "$PROFILE")
fi

"${compose[@]}" up -d

for attempt in $(seq 1 90); do
  health="$(curl -fsS "$HEALTH_URL" || true)"
  if echo "$health" | python3 -c 'import json,sys; d=json.load(sys.stdin); sys.exit(0 if all(d.get("dependencies", {}).values()) else 1)' 2>/dev/null; then
    break
  fi
  if [ "$attempt" -eq 90 ]; then
    echo "Timed out waiting for all dependencies at $HEALTH_URL" >&2
    echo "$health" >&2
    "${compose[@]}" ps
    exit 1
  fi
  sleep 2
done

response="$(curl -fsS "$SEARCH_URL" \
  -H 'content-type: application/json' \
  -d '{"query":"what changed in the EU AI Act timeline in 2025","token_budget":4000}')"

echo "$response" | python3 -c '
import json, sys
body = json.load(sys.stdin)
assert body.get("passages"), "expected non-empty passages"
assert isinstance(body.get("citations"), list), "expected citations array"
stats = body.get("stats", {})
assert stats.get("reranked") is True, "expected reranked=true"
assert stats.get("tokens_returned", 0) <= 4000, "token budget exceeded"
'

echo "$response"
