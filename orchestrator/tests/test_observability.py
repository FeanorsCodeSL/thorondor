import logging

import anyio

from orchestrator import fakes
from orchestrator.models import SearchRequest
from orchestrator.observability import JsonFormatter, redact_url, reset_request_id, set_request_id
from orchestrator.pipeline import run_search


def test_redact_url_strips_userinfo_query_and_fragment():
    assert (
        redact_url("https://user:secret@example.test:8443/path?q=token#frag")
        == "https://example.test:8443/path"
    )


def test_json_formatter_emits_structured_payload():
    record = logging.LogRecord(
        "test",
        logging.INFO,
        __file__,
        1,
        "search_completed",
        (),
        None,
    )
    record.event = "search_completed"
    record.query_hash = "abc"

    token = set_request_id("req-log")
    try:
        out = JsonFormatter().format(record)
    finally:
        reset_request_id(token)

    assert '"event":"search_completed"' in out
    assert '"query_hash":"abc"' in out
    assert '"request_id":"req-log"' in out


def test_search_summary_log_uses_query_hash_not_raw_query(caplog):
    raw_query = "secret raw query should not be logged"
    caplog.set_level(logging.INFO, logger="orchestrator.pipeline")

    response = anyio.run(run_search, SearchRequest(query=raw_query), fakes.deps())

    records = [
        record
        for record in caplog.records
        if getattr(record, "event", None) == "search_completed"
    ]
    assert response.passages
    assert records
    assert getattr(records[-1], "query_hash")
    assert raw_query not in records[-1].getMessage()
