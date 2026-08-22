from pathlib import Path

import pytest

from orchestrator.settings import load_settings

REQUIRED = [
    "LOG_LEVEL",
    "SEARXNG_URL",
    "CRAWL4AI_URL",
    "CHUNKER_URL",
    "RERANKER_ENDPOINT",
    "RERANKER_MODEL",
    "RERANKER_PATH",
    "RERANKER_BATCH_SIZE",
    "RERANKER_TIMEOUT_S",
    "RELEVANCE_SCORE_FLOOR",
    "EVIDENCE_QUALITY_ENABLED",
    "LLM_ENDPOINT",
    "LLM_MODEL",
    "MAX_URLS",
    "CRAWL_CONCURRENCY",
    "CRAWL_TIMEOUT_S",
    "DEFAULT_TOKEN_BUDGET",
    "DOMAIN_BLOCKLIST",
    "DOMAIN_ALLOWLIST",
    "ALLOWLIST_ONLY",
    "CRAWL_RESPECT_ROBOTS_TXT",
    "CRAWL_PER_HOST_CONCURRENCY",
    "CRAWLER_USER_AGENT",
    "CRAWLER_ROBOTS_USER_AGENT",
    "SEARXNG_API_KEY",
    "CRAWL4AI_API_KEY",
    "CHUNKER_API_KEY",
    "RERANKER_API_KEY",
    "LLM_API_KEY",
    "MARKDOWN_EXTRACTOR",
    "MARKDOWN_EXTRACTOR_FAVOR_RECALL",
    "MARKDOWN_EXTRACTOR_INCLUDE_COMMENTS",
    "MARKDOWN_EXTRACTOR_INCLUDE_TABLES",
    "MARKDOWN_EXTRACTOR_DEDUPLICATE",
    "MAX_SUBQUERIES",
    "MAX_REQUEST_BODY_BYTES",
    "MAX_RESPONSE_BODY_BYTES",
    "SEARCH_ROUTE_DEADLINE_S",
    "FETCH_ROUTE_DEADLINE_S",
    "MAP_ROUTE_DEADLINE_S",
    "SITE_CRAWL_ROUTE_DEADLINE_S",
    "DISCOVERY_TIMEOUT_S",
    "CHUNK_TIMEOUT_S",
    "MAX_INFLIGHT_SEARCHES",
    "MAX_INFLIGHT_FETCHES",
    "MAX_INFLIGHT_MAPS",
    "MAX_INFLIGHT_CRAWLS",
    "ADMISSION_WAIT_S",
    "ADMISSION_RETRY_AFTER_S",
    "MAX_INTERNAL_FANOUT",
    "MAX_CONTENT_BYTES",
    "CHUNK_CONCURRENCY",
    "SITE_DEFAULT_DELAY_S",
    "SITE_MAX_JITTER_S",
    "SITE_MAX_COOLDOWN_S",
    "ROBOTS_CACHE_TTL_S",
    "MAX_ROBOTS_BYTES",
    "MAX_SITEMAP_BYTES",
    "MAX_SITEMAP_ENTRIES",
    "MAX_SITEMAP_DOCUMENTS",
    "PAGE_CACHE_ENABLED",
    "PAGE_CACHE_PATH",
    "PAGE_CACHE_TTL_S",
    "PAGE_CACHE_STALE_S",
    "PAGE_CACHE_RETENTION_S",
    "PAGE_CACHE_RAW_HTML_ENABLED",
    "PAGE_DIFF_MAX_INPUT_LINES",
    "PAGE_DIFF_MAX_OPERATIONS",
    "PAGE_DIFF_MAX_OUTPUT_LINES",
    "CRAWL_JOBS_ENABLED",
    "CRAWL_JOB_PATH",
    "CRAWL_SYNC_MAX_PAGES",
    "CRAWL_JOB_RETENTION_S",
    "CRAWL_JOB_EXPIRED_TOMBSTONE_S",
    "CRAWL_JOB_MAX_ATTEMPTS",
    "CRAWL_JOB_RETRY_BASE_S",
    "CRAWL_JOB_ATTEMPT_DEADLINE_S",
    "CRAWL_JOB_MAX_RECORDS",
    "CRAWL_JOB_RAW_HTML_ENABLED",
    "CRAWL_JOB_MAX_INFLIGHT_REQUESTS",
    "CRAWL_JOB_RESULT_PAGE_MAX_ITEMS",
    "CRAWL_JOB_RESULT_PAGE_MAX_BYTES",
    "SEARCH_PROFILE_QUICK_TOKEN_BUDGET",
    "SEARCH_PROFILE_QUICK_MAX_URLS",
    "SEARCH_PROFILE_QUICK_MAX_PASSAGES",
    "SEARCH_PROFILE_RESEARCH_TOKEN_BUDGET",
    "SEARCH_PROFILE_RESEARCH_MAX_URLS",
    "SEARCH_PROFILE_RESEARCH_MAX_PASSAGES",
    "SEARCH_PROFILE_DEEP_TOKEN_BUDGET",
    "SEARCH_PROFILE_DEEP_MAX_URLS",
    "SEARCH_PROFILE_DEEP_MAX_PASSAGES",
    "URL_SAFETY_BLOCKED_IP_CATEGORIES",
    "URL_SAFETY_BLOCKED_SPECIAL_IPS",
    "URL_SAFETY_NAT64_NETWORKS",
    "URL_SAFETY_SIX_TO_FOUR_NETWORKS",
    "URL_SAFETY_IPV4_COMPAT_NETWORKS",
]


def set_required_env(monkeypatch):
    values = {
        "LOG_LEVEL": "INFO",
        "SEARXNG_URL": "http://searxng:8080",
        "CRAWL4AI_URL": "http://crawl4ai:11235",
        "CHUNKER_URL": "http://chunker:8000",
        "RERANKER_ENDPOINT": "http://reranker:80",
        "RERANKER_MODEL": "rerank",
        "RERANKER_PATH": "/rerank",
        "RERANKER_BATCH_SIZE": "32",
        "RERANKER_TIMEOUT_S": "30",
        "RELEVANCE_SCORE_FLOOR": "0.0",
        "EVIDENCE_QUALITY_ENABLED": "false",
        "LLM_ENDPOINT": "",
        "LLM_MODEL": "",
        "MAX_URLS": "6",
        "CRAWL_CONCURRENCY": "4",
        "CRAWL_TIMEOUT_S": "15",
        "DEFAULT_TOKEN_BUDGET": "4000",
        "DOMAIN_BLOCKLIST": "",
        "DOMAIN_ALLOWLIST": "",
        "ALLOWLIST_ONLY": "false",
        "CRAWL_RESPECT_ROBOTS_TXT": "true",
        "CRAWL_PER_HOST_CONCURRENCY": "1",
        "CRAWLER_USER_AGENT": "ThorondorBot/1.0 (+https://example.test/contact)",
        "CRAWLER_ROBOTS_USER_AGENT": "ThorondorBot",
        "SEARXNG_API_KEY": "",
        "CRAWL4AI_API_KEY": "",
        "CHUNKER_API_KEY": "",
        "RERANKER_API_KEY": "",
        "LLM_API_KEY": "",
        "MARKDOWN_EXTRACTOR": "trafilatura",
        "MARKDOWN_EXTRACTOR_FAVOR_RECALL": "true",
        "MARKDOWN_EXTRACTOR_INCLUDE_COMMENTS": "false",
        "MARKDOWN_EXTRACTOR_INCLUDE_TABLES": "true",
        "MARKDOWN_EXTRACTOR_DEDUPLICATE": "true",
        "MAX_SUBQUERIES": "3",
        "MAX_REQUEST_BODY_BYTES": "32768",
        "MAX_RESPONSE_BODY_BYTES": "2097152",
        "SEARCH_ROUTE_DEADLINE_S": "120",
        "FETCH_ROUTE_DEADLINE_S": "60",
        "MAP_ROUTE_DEADLINE_S": "90",
        "SITE_CRAWL_ROUTE_DEADLINE_S": "120",
        "DISCOVERY_TIMEOUT_S": "20",
        "CHUNK_TIMEOUT_S": "45",
        "MAX_INFLIGHT_SEARCHES": "4",
        "MAX_INFLIGHT_FETCHES": "8",
        "MAX_INFLIGHT_MAPS": "4",
        "MAX_INFLIGHT_CRAWLS": "2",
        "ADMISSION_WAIT_S": "0.05",
        "ADMISSION_RETRY_AFTER_S": "1",
        "MAX_INTERNAL_FANOUT": "20",
        "MAX_CONTENT_BYTES": "262144",
        "CHUNK_CONCURRENCY": "4",
        "SITE_DEFAULT_DELAY_S": "0.5",
        "SITE_MAX_JITTER_S": "0.25",
        "SITE_MAX_COOLDOWN_S": "300",
        "ROBOTS_CACHE_TTL_S": "86400",
        "MAX_ROBOTS_BYTES": "262144",
        "MAX_SITEMAP_BYTES": "262144",
        "MAX_SITEMAP_ENTRIES": "500",
        "MAX_SITEMAP_DOCUMENTS": "16",
        "PAGE_CACHE_ENABLED": "false",
        "PAGE_CACHE_PATH": "/var/lib/thorondor/page-cache.sqlite3",
        "PAGE_CACHE_TTL_S": "300",
        "PAGE_CACHE_STALE_S": "900",
        "PAGE_CACHE_RETENTION_S": "604800",
        "PAGE_CACHE_RAW_HTML_ENABLED": "false",
        "PAGE_DIFF_MAX_INPUT_LINES": "2000",
        "PAGE_DIFF_MAX_OPERATIONS": "1000000",
        "PAGE_DIFF_MAX_OUTPUT_LINES": "24",
        "CRAWL_JOBS_ENABLED": "false",
        "CRAWL_JOB_PATH": "/var/lib/thorondor/crawl-jobs.sqlite3",
        "CRAWL_SYNC_MAX_PAGES": "10",
        "CRAWL_JOB_RETENTION_S": "86400",
        "CRAWL_JOB_EXPIRED_TOMBSTONE_S": "3600",
        "CRAWL_JOB_MAX_ATTEMPTS": "3",
        "CRAWL_JOB_RETRY_BASE_S": "1",
        "CRAWL_JOB_ATTEMPT_DEADLINE_S": "600",
        "CRAWL_JOB_MAX_RECORDS": "100",
        "CRAWL_JOB_RAW_HTML_ENABLED": "false",
        "CRAWL_JOB_MAX_INFLIGHT_REQUESTS": "16",
        "CRAWL_JOB_RESULT_PAGE_MAX_ITEMS": "10",
        "CRAWL_JOB_RESULT_PAGE_MAX_BYTES": "524288",
        "SEARCH_PROFILE_QUICK_TOKEN_BUDGET": "2000",
        "SEARCH_PROFILE_QUICK_MAX_URLS": "5",
        "SEARCH_PROFILE_QUICK_MAX_PASSAGES": "5",
        "SEARCH_PROFILE_RESEARCH_TOKEN_BUDGET": "8000",
        "SEARCH_PROFILE_RESEARCH_MAX_URLS": "12",
        "SEARCH_PROFILE_RESEARCH_MAX_PASSAGES": "20",
        "SEARCH_PROFILE_DEEP_TOKEN_BUDGET": "16000",
        "SEARCH_PROFILE_DEEP_MAX_URLS": "20",
        "SEARCH_PROFILE_DEEP_MAX_PASSAGES": "40",
        "URL_SAFETY_BLOCKED_IP_CATEGORIES": "loopback,link_local,private,reserved,multicast,unspecified",
        "URL_SAFETY_BLOCKED_SPECIAL_IPS": "169.254.169.254,fd00:ec2::254",
        "URL_SAFETY_NAT64_NETWORKS": "64:ff9b::/96",
        "URL_SAFETY_SIX_TO_FOUR_NETWORKS": "2002::/16",
        "URL_SAFETY_IPV4_COMPAT_NETWORKS": "::/96",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)


@pytest.mark.parametrize("name", REQUIRED)
def test_missing_required_var_raises(monkeypatch, name):
    set_required_env(monkeypatch)
    monkeypatch.delenv(name, raising=False)

    with pytest.raises(RuntimeError, match=name):
        load_settings()


def test_domain_blocklist_parses_explicit_configuration(monkeypatch):
    set_required_env(monkeypatch)
    monkeypatch.setenv("DOMAIN_BLOCKLIST", "a.com, b.com")
    monkeypatch.setenv("DOMAIN_ALLOWLIST", "safe.com")
    monkeypatch.setenv("ALLOWLIST_ONLY", "true")
    monkeypatch.setenv("CRAWL_RESPECT_ROBOTS_TXT", "false")
    monkeypatch.setenv("CRAWL_PER_HOST_CONCURRENCY", "2")
    monkeypatch.setenv("RERANKER_API_KEY", "secret")
    monkeypatch.setenv("RERANKER_BATCH_SIZE", "7")
    monkeypatch.setenv("RERANKER_TIMEOUT_S", "45")
    monkeypatch.setenv("RELEVANCE_SCORE_FLOOR", "0.25")

    settings = load_settings()

    assert settings.domain_blocklist == {"a.com", "b.com"}
    assert settings.domain_allowlist == {"safe.com"}
    assert settings.allowlist_only is True
    assert settings.crawl_respect_robots_txt is False
    assert settings.crawl_per_host_concurrency == 2
    assert settings.crawler_user_agent == "ThorondorBot/1.0 (+https://example.test/contact)"
    assert settings.crawler_robots_user_agent == "ThorondorBot"
    assert settings.reranker_api_key == "secret"
    assert settings.max_urls == 6
    assert settings.crawl_concurrency == 4
    assert settings.crawl_timeout_s == 15
    assert settings.default_token_budget == 4000
    assert settings.reranker_path == "/rerank"
    assert settings.reranker_batch_size == 7
    assert settings.reranker_timeout_s == 45
    assert settings.relevance_score_floor == 0.25
    assert settings.evidence_quality_enabled is False
    assert settings.log_level == "INFO"
    assert settings.markdown_extractor == "trafilatura"
    assert settings.markdown_extractor_favor_recall is True
    assert settings.markdown_extractor_include_comments is False
    assert settings.markdown_extractor_include_tables is True
    assert settings.markdown_extractor_deduplicate is True
    assert settings.max_subqueries == 3
    assert settings.max_request_body_bytes == 32768
    assert settings.max_response_body_bytes == 2097152
    assert settings.search_route_deadline_s == 120
    assert settings.fetch_route_deadline_s == 60
    assert settings.map_route_deadline_s == 90
    assert settings.site_crawl_route_deadline_s == 120
    assert settings.discovery_timeout_s == 20
    assert settings.chunk_timeout_s == 45
    assert settings.max_inflight_searches == 4
    assert settings.max_inflight_fetches == 8
    assert settings.max_inflight_maps == 4
    assert settings.max_inflight_crawls == 2
    assert settings.admission_wait_s == 0.05
    assert settings.admission_retry_after_s == 1
    assert settings.max_internal_fanout == 20
    assert settings.max_content_bytes == 262144
    assert settings.chunk_concurrency == 4
    assert settings.site_default_delay_s == 0.5
    assert settings.site_max_jitter_s == 0.25
    assert settings.site_max_cooldown_s == 300
    assert settings.robots_cache_ttl_s == 86400
    assert settings.max_robots_bytes == 262144
    assert settings.max_sitemap_bytes == 262144
    assert settings.max_sitemap_entries == 500
    assert settings.max_sitemap_documents == 16
    assert settings.page_cache_enabled is False
    assert settings.page_cache_path == "/var/lib/thorondor/page-cache.sqlite3"
    assert settings.page_cache_ttl_s == 300
    assert settings.page_cache_stale_s == 900
    assert settings.page_cache_retention_s == 604800
    assert settings.page_cache_raw_html_enabled is False
    assert settings.page_diff_max_input_lines == 2000
    assert settings.page_diff_max_operations == 1000000
    assert settings.page_diff_max_output_lines == 24
    assert settings.search_profiles["quick"].token_budget == 2000
    assert settings.search_profiles["research"].max_urls == 12
    assert settings.search_profiles["deep"].max_passages == 40
    assert settings.url_safety_policy.blocked_ip_categories == {
        "loopback",
        "link_local",
        "private",
        "reserved",
        "multicast",
        "unspecified",
    }


@pytest.mark.parametrize("name", ["MAX_URLS", "RERANKER_PATH", "ALLOWLIST_ONLY"])
def test_blank_required_var_raises(monkeypatch, name):
    set_required_env(monkeypatch)
    monkeypatch.setenv(name, "  ")

    with pytest.raises(RuntimeError, match=name):
        load_settings()


def test_optional_vars_must_exist_but_can_be_blank(monkeypatch):
    set_required_env(monkeypatch)

    settings = load_settings()

    assert settings.llm_endpoint is None
    assert settings.llm_model is None
    assert settings.domain_blocklist == set()
    assert settings.searxng_api_key is None


def test_invalid_crawl_concurrency_rejected(monkeypatch):
    set_required_env(monkeypatch)
    monkeypatch.setenv("CRAWL_CONCURRENCY", "999")

    with pytest.raises(RuntimeError):
        load_settings()


def test_max_subqueries_above_wire_limit_is_rejected(monkeypatch):
    set_required_env(monkeypatch)
    monkeypatch.setenv("MAX_SUBQUERIES", "9")

    with pytest.raises(RuntimeError, match="MAX_SUBQUERIES must be between 1 and 8"):
        load_settings()


@pytest.mark.parametrize(
    "path",
    [
        "docker-compose.yml",
        "docker-compose.production.yml",
        "thorondor_cli/assets/docker-compose.yml",
    ],
)
def test_compose_manifests_forward_evidence_quality_setting(path):
    manifest = Path(path).read_text(encoding="utf-8")

    assert 'EVIDENCE_QUALITY_ENABLED: "${EVIDENCE_QUALITY_ENABLED}"' in manifest


@pytest.mark.parametrize(
    "path",
    [
        "docker-compose.yml",
        "docker-compose.production.yml",
        "thorondor_cli/assets/docker-compose.yml",
    ],
)
def test_compose_manifests_forward_resource_policy(path):
    manifest = Path(path).read_text(encoding="utf-8")

    for name in (
        "MAX_REQUEST_BODY_BYTES",
        "MAX_RESPONSE_BODY_BYTES",
        "SEARCH_ROUTE_DEADLINE_S",
        "FETCH_ROUTE_DEADLINE_S",
        "MAP_ROUTE_DEADLINE_S",
        "SITE_CRAWL_ROUTE_DEADLINE_S",
        "DISCOVERY_TIMEOUT_S",
        "CHUNK_TIMEOUT_S",
        "MAX_INFLIGHT_SEARCHES",
        "MAX_INFLIGHT_FETCHES",
        "MAX_INFLIGHT_MAPS",
        "MAX_INFLIGHT_CRAWLS",
        "ADMISSION_WAIT_S",
        "ADMISSION_RETRY_AFTER_S",
        "MAX_INTERNAL_FANOUT",
        "MAX_CONTENT_BYTES",
        "CHUNK_CONCURRENCY",
        "SITE_DEFAULT_DELAY_S",
        "SITE_MAX_JITTER_S",
        "SITE_MAX_COOLDOWN_S",
        "ROBOTS_CACHE_TTL_S",
        "MAX_ROBOTS_BYTES",
        "MAX_SITEMAP_BYTES",
        "MAX_SITEMAP_ENTRIES",
        "MAX_SITEMAP_DOCUMENTS",
        "PAGE_CACHE_ENABLED",
        "PAGE_CACHE_PATH",
        "PAGE_CACHE_TTL_S",
        "PAGE_CACHE_STALE_S",
        "PAGE_CACHE_RETENTION_S",
        "PAGE_CACHE_RAW_HTML_ENABLED",
        "PAGE_DIFF_MAX_INPUT_LINES",
        "PAGE_DIFF_MAX_OPERATIONS",
        "PAGE_DIFF_MAX_OUTPUT_LINES",
        "CRAWL_JOBS_ENABLED",
        "CRAWL_JOB_PATH",
        "CRAWL_SYNC_MAX_PAGES",
        "CRAWL_JOB_RETENTION_S",
        "CRAWL_JOB_EXPIRED_TOMBSTONE_S",
        "CRAWL_JOB_MAX_ATTEMPTS",
        "CRAWL_JOB_RETRY_BASE_S",
        "CRAWL_JOB_ATTEMPT_DEADLINE_S",
        "CRAWL_JOB_MAX_RECORDS",
        "CRAWL_JOB_RAW_HTML_ENABLED",
        "CRAWL_JOB_MAX_INFLIGHT_REQUESTS",
        "CRAWL_JOB_RESULT_PAGE_MAX_ITEMS",
        "CRAWL_JOB_RESULT_PAGE_MAX_BYTES",
    ):
        assert f'{name}: "${{{name}}}"' in manifest


@pytest.mark.parametrize(
    ("name", "value", "match"),
    [
        ("MAX_REQUEST_BODY_BYTES", "0", "max_request_body_bytes"),
        ("SEARCH_ROUTE_DEADLINE_S", "0", "search_route_deadline_s"),
        ("ADMISSION_WAIT_S", "-1", "admission_wait_s"),
        ("MAX_CONTENT_BYTES", "3000000", "max_content_bytes"),
        ("MAX_INTERNAL_FANOUT", "5", "MAX_INTERNAL_FANOUT"),
        ("SITE_DEFAULT_DELAY_S", "-1", "SITE_DEFAULT_DELAY_S"),
        ("SITE_MAX_JITTER_S", "-1", "SITE_MAX_JITTER_S"),
        ("SITE_MAX_COOLDOWN_S", "0", "SITE_MAX_COOLDOWN_S"),
        ("ROBOTS_CACHE_TTL_S", "86401", "ROBOTS_CACHE_TTL_S"),
        ("MAX_ROBOTS_BYTES", "262145", "MAX_ROBOTS_BYTES"),
        ("MAX_SITEMAP_BYTES", "262145", "MAX_SITEMAP_BYTES"),
        ("MAX_SITEMAP_ENTRIES", "501", "MAX_SITEMAP_ENTRIES"),
        ("MAX_SITEMAP_DOCUMENTS", "21", "MAX_SITEMAP_DOCUMENTS"),
        ("PAGE_CACHE_TTL_S", "0", "PAGE_CACHE_TTL_S"),
        ("PAGE_CACHE_STALE_S", "-1", "PAGE_CACHE_STALE_S"),
        ("PAGE_CACHE_RETENTION_S", "1199", "PAGE_CACHE_RETENTION_S"),
        ("PAGE_DIFF_MAX_INPUT_LINES", "0", "PAGE_DIFF_MAX_INPUT_LINES"),
        ("PAGE_DIFF_MAX_OPERATIONS", "0", "PAGE_DIFF_MAX_OPERATIONS"),
        ("PAGE_DIFF_MAX_OUTPUT_LINES", "25", "PAGE_DIFF_MAX_OUTPUT_LINES"),
        ("CRAWL_SYNC_MAX_PAGES", "21", "CRAWL_SYNC_MAX_PAGES"),
        ("CRAWL_JOB_RETENTION_S", "0", "CRAWL_JOB_RETENTION_S"),
        (
            "CRAWL_JOB_EXPIRED_TOMBSTONE_S",
            "0",
            "CRAWL_JOB_EXPIRED_TOMBSTONE_S",
        ),
        ("CRAWL_JOB_MAX_ATTEMPTS", "6", "CRAWL_JOB_MAX_ATTEMPTS"),
        ("CRAWL_JOB_RETRY_BASE_S", "61", "CRAWL_JOB_RETRY_BASE_S"),
        (
            "CRAWL_JOB_ATTEMPT_DEADLINE_S",
            "3601",
            "CRAWL_JOB_ATTEMPT_DEADLINE_S",
        ),
        ("CRAWL_JOB_MAX_RECORDS", "10001", "CRAWL_JOB_MAX_RECORDS"),
        (
            "CRAWL_JOB_MAX_INFLIGHT_REQUESTS",
            "129",
            "CRAWL_JOB_MAX_INFLIGHT_REQUESTS",
        ),
        ("CRAWL_JOB_RESULT_PAGE_MAX_ITEMS", "21", "CRAWL_JOB_RESULT_PAGE_MAX_ITEMS"),
        (
            "CRAWL_JOB_RESULT_PAGE_MAX_BYTES",
            "1048577",
            "CRAWL_JOB_RESULT_PAGE_MAX_BYTES",
        ),
        ("MAX_URLS", "21", "MAX_URLS must be between 1 and 20"),
        (
            "SEARCH_PROFILE_DEEP_MAX_URLS",
            "21",
            "search profile max_urls must be <= 20",
        ),
    ],
)
def test_invalid_resource_policy_rejected(monkeypatch, name, value, match):
    set_required_env(monkeypatch)
    monkeypatch.setenv(name, value)

    with pytest.raises(RuntimeError, match=match):
        load_settings()


@pytest.mark.parametrize(
    ("name", "value", "match"),
    [
        ("CRAWLER_USER_AGENT", "ThorondorBot/1.0", "contact URL"),
        ("CRAWLER_USER_AGENT", "ThorondorBot/1.0\r\nX: y (+https://example.test/contact)", "newline"),
        ("CRAWLER_ROBOTS_USER_AGENT", "*", "robots user-agent token"),
        ("CRAWLER_ROBOTS_USER_AGENT", "AnotherBot", "must appear"),
    ],
)
def test_invalid_crawler_identity_rejected(monkeypatch, name, value, match):
    set_required_env(monkeypatch)
    monkeypatch.setenv(name, value)

    with pytest.raises(RuntimeError, match=match):
        load_settings()
