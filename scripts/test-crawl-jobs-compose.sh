#!/usr/bin/env bash
set -euo pipefail

project="thorondor-crawl-jobs-test"
compose=(docker compose -p "${project}" -f docker-compose.crawl-jobs-test.yml)

cleanup() {
    "${compose[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup EXIT

"${compose[@]}" build crawl-job-probe
set +e
"${compose[@]}" run --rm crawl-job-probe \
    python -m orchestrator.tests.crawl_job_compose_probe \
    interrupt /var/lib/thorondor/crawl-jobs-test.sqlite3 \
    /var/lib/thorondor/crawl-jobs-test.json
interrupted_status=$?
set -e
if [[ "${interrupted_status}" -ne 75 ]]; then
    echo "[FAIL] interruption probe exited ${interrupted_status}, expected 75" >&2
    exit 1
fi
"${compose[@]}" run --rm crawl-job-probe
