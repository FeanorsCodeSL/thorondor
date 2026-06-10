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
INVESTIGATION="${INVESTIGATION:-false}"

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

run_search_smoke() {
  local body="$1"
  local require_prefilter="${2:-false}"
  echo "REQUEST_JSON:"
  echo "$body"
  response="$(curl -fsS "$SEARCH_URL" -H 'content-type: application/json' -d "$body")"
  echo "RESPONSE_JSON:"
  echo "$response"

  RESPONSE="$response" BODY="$body" REQUIRE_PREFILTER="$require_prefilter" INVESTIGATION="$INVESTIGATION" python3 -c '
import json, sys
import os
body = json.loads(os.environ["RESPONSE"])
request = json.loads(os.environ["BODY"])
assert body.get("passages"), "expected non-empty passages"
assert isinstance(body.get("citations"), list), "expected citations array"
stats = body.get("stats", {})
assert stats.get("reranked") is True, "expected reranked=true"
assert stats.get("tokens_returned", 0) <= request["token_budget"], "token budget exceeded"
if os.environ["REQUIRE_PREFILTER"] == "true" and stats.get("chunks_produced", 0) >= 50:
    assert stats.get("chunks_sent_to_reranker", 0) < stats.get("chunks_produced", 0), "expected prefilter to reduce broad chunk set"
if os.environ["INVESTIGATION"] == "true":
    assert stats.get("url_diagnostics"), "expected URL diagnostics in investigation mode"
'
}

if [ "$INVESTIGATION" = "true" ]; then
  run_search_smoke '{"query":"Where was J. Robert Oppenheimer born?","search_profile":"research","token_budget":5000,"max_urls":8,"max_passages":8,"include_raw_markdown":false}'
  run_search_smoke '{"query":"J. Robert Oppenheimer Manhattan Project early life education security hearing","search_profile":"research","token_budget":8000,"max_urls":12,"max_passages":12,"include_raw_markdown":false}' true
else
  run_search_smoke '{"query":"what changed in the EU AI Act timeline in 2025","token_budget":4000}'
fi
