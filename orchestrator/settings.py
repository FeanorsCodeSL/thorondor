"""Environment-driven configuration for the orchestrator."""
import ipaddress
import os
import re
from dataclasses import dataclass
from urllib.parse import urlparse

from .models import (
    MAX_SELECTED_URLS,
    MAX_SITE_DISCOVERED_URLS,
    MAX_SITE_PAGES,
    MAX_SUBQUERY_COUNT,
)
from .resource_policy import ResourcePolicy
from .url_safety import UrlSafetyPolicy

MAX_CRAWL_CONCURRENCY = 20
CRAWLER_ROBOTS_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9._-]*")
CRAWLER_CONTACT_URL = re.compile(r"https?://[^\s()<>]+")


def _required(name: str) -> str:
    value = os.environ.get(name)
    if value is None or not value.strip():
        raise RuntimeError(f"Required environment variable {name} is not set")
    return value.strip()


def _configured_optional(name: str) -> str | None:
    if name not in os.environ:
        raise RuntimeError(f"Required environment variable {name} is not set")
    value = os.environ[name]
    return value.strip() if value.strip() else None


def _int_env(name: str) -> int:
    return int(_required(name))


def _float_env(name: str) -> float:
    return float(_required(name))


def _bool_env(name: str) -> bool:
    raw = _required(name).lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(f"{name} must be a boolean value")


def _set_env(name: str) -> set[str]:
    if name not in os.environ:
        raise RuntimeError(f"Required environment variable {name} is not set")
    raw = os.environ[name]
    return {part.strip().lower() for part in raw.split(",") if part.strip()}


def _ip_set_env(name: str) -> set[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    return {ipaddress.ip_address(value) for value in _set_env(name)}


def _ipv6_networks_env(name: str) -> list[ipaddress.IPv6Network]:
    networks = [ipaddress.ip_network(value, strict=False) for value in _set_env(name)]
    invalid = [str(network) for network in networks if network.version != 6]
    if invalid:
        raise RuntimeError(f"{name} must contain only IPv6 networks: {', '.join(invalid)}")
    return [network for network in networks if isinstance(network, ipaddress.IPv6Network)]


def _has_contact_url(user_agent: str) -> bool:
    for candidate in CRAWLER_CONTACT_URL.findall(user_agent):
        parsed = urlparse(candidate)
        if parsed.scheme in {"http", "https"} and parsed.hostname:
            return True
    return False


@dataclass(frozen=True)
class SearchProfileDefaults:
    token_budget: int
    max_urls: int
    max_passages: int

    def __post_init__(self) -> None:
        if self.token_budget < 1:
            raise RuntimeError("search profile token_budget must be >= 1")
        if self.max_urls < 1:
            raise RuntimeError("search profile max_urls must be >= 1")
        if self.max_passages < 1:
            raise RuntimeError("search profile max_passages must be >= 1")
        if self.max_urls > MAX_SELECTED_URLS:
            raise RuntimeError(
                f"search profile max_urls must be <= {MAX_SELECTED_URLS}"
            )


@dataclass(frozen=True)
class Settings:
    log_level: str
    searxng_url: str
    crawl4ai_url: str
    chunker_url: str
    reranker_endpoint: str
    reranker_model: str
    reranker_path: str
    reranker_health_path: str
    reranker_batch_size: int
    reranker_timeout_s: int
    relevance_score_floor: float
    evidence_quality_enabled: bool
    llm_endpoint: str | None
    llm_model: str | None
    max_urls: int
    crawl_concurrency: int
    crawl_timeout_s: int
    default_token_budget: int
    domain_blocklist: set[str]
    domain_allowlist: set[str]
    allowlist_only: bool
    crawl_respect_robots_txt: bool
    crawl_per_host_concurrency: int
    crawler_user_agent: str
    crawler_robots_user_agent: str
    searxng_api_key: str | None
    crawl4ai_api_key: str | None
    chunker_api_key: str | None
    reranker_api_key: str | None
    llm_api_key: str | None
    healthcheck_timeout_s: float
    healthcheck_max_connections: int
    healthcheck_max_keepalive_connections: int
    markdown_extractor: str
    markdown_extractor_favor_recall: bool
    markdown_extractor_include_comments: bool
    markdown_extractor_include_tables: bool
    markdown_extractor_deduplicate: bool
    max_subqueries: int
    max_request_body_bytes: int
    max_response_body_bytes: int
    search_route_deadline_s: float
    fetch_route_deadline_s: float
    map_route_deadline_s: float
    site_crawl_route_deadline_s: float
    discovery_timeout_s: float
    chunk_timeout_s: float
    max_inflight_searches: int
    max_inflight_fetches: int
    max_inflight_maps: int
    max_inflight_crawls: int
    admission_wait_s: float
    admission_retry_after_s: int
    max_internal_fanout: int
    max_content_bytes: int
    chunk_concurrency: int
    site_default_delay_s: float
    site_max_jitter_s: float
    site_max_cooldown_s: int
    robots_cache_ttl_s: int
    max_robots_bytes: int
    max_sitemap_bytes: int
    max_sitemap_entries: int
    max_sitemap_documents: int
    page_cache_enabled: bool
    page_cache_path: str
    page_cache_ttl_s: int
    page_cache_stale_s: int
    page_cache_retention_s: int
    page_cache_raw_html_enabled: bool
    page_diff_max_input_lines: int
    page_diff_max_operations: int
    page_diff_max_output_lines: int
    crawl_jobs_enabled: bool
    crawl_job_path: str
    crawl_sync_max_pages: int
    crawl_job_retention_s: int
    crawl_job_expired_tombstone_s: int
    crawl_job_max_attempts: int
    crawl_job_retry_base_s: float
    crawl_job_attempt_deadline_s: float
    crawl_job_max_records: int
    crawl_job_raw_html_enabled: bool
    crawl_job_max_inflight_requests: int
    crawl_job_result_page_max_items: int
    crawl_job_result_page_max_bytes: int
    search_profiles: dict[str, SearchProfileDefaults]
    url_safety_policy: UrlSafetyPolicy

    def _validate_crawler_identity(self) -> None:
        if not 1 <= self.crawl_concurrency <= MAX_CRAWL_CONCURRENCY:
            raise RuntimeError(f"CRAWL_CONCURRENCY must be between 1 and {MAX_CRAWL_CONCURRENCY}")
        if self.crawl_per_host_concurrency < 1:
            raise RuntimeError("CRAWL_PER_HOST_CONCURRENCY must be >= 1")
        if "\r" in self.crawler_user_agent or "\n" in self.crawler_user_agent:
            raise RuntimeError("CRAWLER_USER_AGENT must not contain newline characters")
        if not _has_contact_url(self.crawler_user_agent):
            raise RuntimeError("CRAWLER_USER_AGENT must contain an http or https contact URL")
        if not CRAWLER_ROBOTS_TOKEN.fullmatch(self.crawler_robots_user_agent):
            raise RuntimeError("CRAWLER_ROBOTS_USER_AGENT must be a robots user-agent token")
        if self.crawler_robots_user_agent.casefold() not in self.crawler_user_agent.casefold():
            raise RuntimeError("CRAWLER_ROBOTS_USER_AGENT must appear in CRAWLER_USER_AGENT")

    def _validate_service_limits(self) -> None:
        if self.reranker_batch_size < 1:
            raise RuntimeError("RERANKER_BATCH_SIZE must be >= 1")
        if self.reranker_timeout_s < 1:
            raise RuntimeError("RERANKER_TIMEOUT_S must be >= 1")
        if self.healthcheck_timeout_s <= 0:
            raise RuntimeError("HEALTHCHECK_TIMEOUT_S must be > 0")
        if self.healthcheck_max_connections < 1:
            raise RuntimeError("HEALTHCHECK_MAX_CONNECTIONS must be >= 1")
        if self.healthcheck_max_keepalive_connections < 1:
            raise RuntimeError("HEALTHCHECK_MAX_KEEPALIVE_CONNECTIONS must be >= 1")
        if self.markdown_extractor.lower() != "trafilatura":
            raise RuntimeError("MARKDOWN_EXTRACTOR must be trafilatura")
        if not 1 <= self.max_subqueries <= MAX_SUBQUERY_COUNT:
            raise RuntimeError(
                f"MAX_SUBQUERIES must be between 1 and {MAX_SUBQUERY_COUNT}"
            )
        if not 1 <= self.max_urls <= MAX_SELECTED_URLS:
            raise RuntimeError(
                f"MAX_URLS must be between 1 and {MAX_SELECTED_URLS}"
            )

    def _validate_resource_policy(self) -> None:
        try:
            ResourcePolicy(
                max_request_body_bytes=self.max_request_body_bytes,
                max_response_body_bytes=self.max_response_body_bytes,
                search_route_deadline_s=self.search_route_deadline_s,
                fetch_route_deadline_s=self.fetch_route_deadline_s,
                map_route_deadline_s=self.map_route_deadline_s,
                site_crawl_route_deadline_s=self.site_crawl_route_deadline_s,
                discovery_stage_deadline_s=self.discovery_timeout_s,
                crawl_stage_deadline_s=self.crawl_timeout_s,
                chunk_stage_deadline_s=self.chunk_timeout_s,
                rerank_stage_deadline_s=self.reranker_timeout_s,
                max_inflight_searches=self.max_inflight_searches,
                max_inflight_fetches=self.max_inflight_fetches,
                max_inflight_maps=self.max_inflight_maps,
                max_inflight_crawls=self.max_inflight_crawls,
                admission_wait_s=self.admission_wait_s,
                admission_retry_after_s=self.admission_retry_after_s,
                max_internal_fanout=self.max_internal_fanout,
                max_content_bytes=self.max_content_bytes,
                chunk_concurrency=self.chunk_concurrency,
            )
        except ValueError as exc:
            raise RuntimeError(str(exc)) from exc
        required_fanout = max(
            self.max_urls,
            self.max_subqueries,
            self.crawl_concurrency,
            self.chunk_concurrency,
            *(profile.max_urls for profile in self.search_profiles.values()),
        )
        if self.max_internal_fanout < required_fanout:
            raise RuntimeError(
                "MAX_INTERNAL_FANOUT must cover URL, subquery, crawl, chunk, and profile limits"
            )

    def _validate_site_limits(self) -> None:
        if self.site_default_delay_s < 0:
            raise RuntimeError("SITE_DEFAULT_DELAY_S must be >= 0")
        if self.site_max_jitter_s < 0:
            raise RuntimeError("SITE_MAX_JITTER_S must be >= 0")
        if self.site_max_cooldown_s < 1:
            raise RuntimeError("SITE_MAX_COOLDOWN_S must be >= 1")
        if not 1 <= self.robots_cache_ttl_s <= 86400:
            raise RuntimeError("ROBOTS_CACHE_TTL_S must be between 1 and 86400")
        for name, value in (
            ("MAX_ROBOTS_BYTES", self.max_robots_bytes),
            ("MAX_SITEMAP_BYTES", self.max_sitemap_bytes),
            ("MAX_SITEMAP_ENTRIES", self.max_sitemap_entries),
            ("MAX_SITEMAP_DOCUMENTS", self.max_sitemap_documents),
        ):
            if value < 1:
                raise RuntimeError(f"{name} must be >= 1")
        if self.max_robots_bytes > self.max_content_bytes:
            raise RuntimeError("MAX_ROBOTS_BYTES must not exceed MAX_CONTENT_BYTES")
        if self.max_sitemap_bytes > self.max_content_bytes:
            raise RuntimeError("MAX_SITEMAP_BYTES must not exceed MAX_CONTENT_BYTES")
        if self.max_sitemap_entries > MAX_SITE_DISCOVERED_URLS:
            raise RuntimeError(
                f"MAX_SITEMAP_ENTRIES must not exceed {MAX_SITE_DISCOVERED_URLS}"
            )
        if self.max_sitemap_documents > self.max_internal_fanout:
            raise RuntimeError(
                "MAX_SITEMAP_DOCUMENTS must not exceed MAX_INTERNAL_FANOUT"
            )

    def _validate_page_cache(self) -> None:
        if not self.page_cache_path:
            raise RuntimeError("PAGE_CACHE_PATH must not be blank")
        for name, value in (
            ("PAGE_CACHE_TTL_S", self.page_cache_ttl_s),
            ("PAGE_CACHE_RETENTION_S", self.page_cache_retention_s),
            ("PAGE_DIFF_MAX_INPUT_LINES", self.page_diff_max_input_lines),
            ("PAGE_DIFF_MAX_OPERATIONS", self.page_diff_max_operations),
            ("PAGE_DIFF_MAX_OUTPUT_LINES", self.page_diff_max_output_lines),
        ):
            if value < 1:
                raise RuntimeError(f"{name} must be >= 1")
        if self.page_cache_stale_s < 0:
            raise RuntimeError("PAGE_CACHE_STALE_S must be >= 0")
        if self.page_cache_retention_s < self.page_cache_ttl_s + self.page_cache_stale_s:
            raise RuntimeError(
                "PAGE_CACHE_RETENTION_S must cover PAGE_CACHE_TTL_S plus PAGE_CACHE_STALE_S"
            )
        if self.page_diff_max_output_lines > 24:
            raise RuntimeError("PAGE_DIFF_MAX_OUTPUT_LINES must not exceed 24")

    def _validate_crawl_jobs(self) -> None:
        if not self.crawl_job_path:
            raise RuntimeError("CRAWL_JOB_PATH must not be blank")
        if not 1 <= self.crawl_sync_max_pages <= MAX_SITE_PAGES:
            raise RuntimeError(
                f"CRAWL_SYNC_MAX_PAGES must be between 1 and {MAX_SITE_PAGES}"
            )
        for name, value in (
            ("CRAWL_JOB_RETENTION_S", self.crawl_job_retention_s),
            ("CRAWL_JOB_EXPIRED_TOMBSTONE_S", self.crawl_job_expired_tombstone_s),
            ("CRAWL_JOB_MAX_RECORDS", self.crawl_job_max_records),
            (
                "CRAWL_JOB_MAX_INFLIGHT_REQUESTS",
                self.crawl_job_max_inflight_requests,
            ),
            ("CRAWL_JOB_RESULT_PAGE_MAX_ITEMS", self.crawl_job_result_page_max_items),
            ("CRAWL_JOB_RESULT_PAGE_MAX_BYTES", self.crawl_job_result_page_max_bytes),
        ):
            if value < 1:
                raise RuntimeError(f"{name} must be >= 1")
        if not 1 <= self.crawl_job_max_attempts <= 5:
            raise RuntimeError("CRAWL_JOB_MAX_ATTEMPTS must be between 1 and 5")
        if not 0 <= self.crawl_job_retry_base_s <= 60:
            raise RuntimeError("CRAWL_JOB_RETRY_BASE_S must be between 0 and 60")
        if not 1 <= self.crawl_job_attempt_deadline_s <= 3600:
            raise RuntimeError(
                "CRAWL_JOB_ATTEMPT_DEADLINE_S must be between 1 and 3600"
            )
        if self.crawl_job_max_records > 10000:
            raise RuntimeError("CRAWL_JOB_MAX_RECORDS must not exceed 10000")
        if self.crawl_job_max_inflight_requests > 128:
            raise RuntimeError("CRAWL_JOB_MAX_INFLIGHT_REQUESTS must not exceed 128")
        if self.crawl_job_result_page_max_items > MAX_SITE_PAGES:
            raise RuntimeError(
                f"CRAWL_JOB_RESULT_PAGE_MAX_ITEMS must not exceed {MAX_SITE_PAGES}"
            )
        if self.crawl_job_result_page_max_bytes > self.max_response_body_bytes // 2:
            raise RuntimeError(
                "CRAWL_JOB_RESULT_PAGE_MAX_BYTES must not exceed half MAX_RESPONSE_BODY_BYTES"
            )

    def __post_init__(self) -> None:
        self._validate_crawler_identity()
        self._validate_service_limits()
        self._validate_resource_policy()
        self._validate_site_limits()
        self._validate_page_cache()
        self._validate_crawl_jobs()


def load_settings() -> Settings:
    return Settings(
        log_level=_required("LOG_LEVEL").upper(),
        searxng_url=_required("SEARXNG_URL"),
        crawl4ai_url=_required("CRAWL4AI_URL"),
        chunker_url=_required("CHUNKER_URL"),
        reranker_endpoint=_required("RERANKER_ENDPOINT"),
        reranker_model=_required("RERANKER_MODEL"),
        reranker_path=_required("RERANKER_PATH"),
        reranker_health_path=_required("RERANKER_HEALTH_PATH"),
        reranker_batch_size=_int_env("RERANKER_BATCH_SIZE"),
        reranker_timeout_s=_int_env("RERANKER_TIMEOUT_S"),
        relevance_score_floor=_float_env("RELEVANCE_SCORE_FLOOR"),
        evidence_quality_enabled=_bool_env("EVIDENCE_QUALITY_ENABLED"),
        llm_endpoint=_configured_optional("LLM_ENDPOINT"),
        llm_model=_configured_optional("LLM_MODEL"),
        max_urls=_int_env("MAX_URLS"),
        crawl_concurrency=_int_env("CRAWL_CONCURRENCY"),
        crawl_timeout_s=_int_env("CRAWL_TIMEOUT_S"),
        default_token_budget=_int_env("DEFAULT_TOKEN_BUDGET"),
        domain_blocklist=_set_env("DOMAIN_BLOCKLIST"),
        domain_allowlist=_set_env("DOMAIN_ALLOWLIST"),
        allowlist_only=_bool_env("ALLOWLIST_ONLY"),
        crawl_respect_robots_txt=_bool_env("CRAWL_RESPECT_ROBOTS_TXT"),
        crawl_per_host_concurrency=_int_env("CRAWL_PER_HOST_CONCURRENCY"),
        crawler_user_agent=_required("CRAWLER_USER_AGENT"),
        crawler_robots_user_agent=_required("CRAWLER_ROBOTS_USER_AGENT"),
        searxng_api_key=_configured_optional("SEARXNG_API_KEY"),
        crawl4ai_api_key=_configured_optional("CRAWL4AI_API_KEY"),
        chunker_api_key=_configured_optional("CHUNKER_API_KEY"),
        reranker_api_key=_configured_optional("RERANKER_API_KEY"),
        llm_api_key=_configured_optional("LLM_API_KEY"),
        healthcheck_timeout_s=_float_env("HEALTHCHECK_TIMEOUT_S"),
        healthcheck_max_connections=_int_env("HEALTHCHECK_MAX_CONNECTIONS"),
        healthcheck_max_keepalive_connections=_int_env("HEALTHCHECK_MAX_KEEPALIVE_CONNECTIONS"),
        markdown_extractor=_required("MARKDOWN_EXTRACTOR"),
        markdown_extractor_favor_recall=_bool_env("MARKDOWN_EXTRACTOR_FAVOR_RECALL"),
        markdown_extractor_include_comments=_bool_env("MARKDOWN_EXTRACTOR_INCLUDE_COMMENTS"),
        markdown_extractor_include_tables=_bool_env("MARKDOWN_EXTRACTOR_INCLUDE_TABLES"),
        markdown_extractor_deduplicate=_bool_env("MARKDOWN_EXTRACTOR_DEDUPLICATE"),
        max_subqueries=_int_env("MAX_SUBQUERIES"),
        max_request_body_bytes=_int_env("MAX_REQUEST_BODY_BYTES"),
        max_response_body_bytes=_int_env("MAX_RESPONSE_BODY_BYTES"),
        search_route_deadline_s=_float_env("SEARCH_ROUTE_DEADLINE_S"),
        fetch_route_deadline_s=_float_env("FETCH_ROUTE_DEADLINE_S"),
        map_route_deadline_s=_float_env("MAP_ROUTE_DEADLINE_S"),
        site_crawl_route_deadline_s=_float_env("SITE_CRAWL_ROUTE_DEADLINE_S"),
        discovery_timeout_s=_float_env("DISCOVERY_TIMEOUT_S"),
        chunk_timeout_s=_float_env("CHUNK_TIMEOUT_S"),
        max_inflight_searches=_int_env("MAX_INFLIGHT_SEARCHES"),
        max_inflight_fetches=_int_env("MAX_INFLIGHT_FETCHES"),
        max_inflight_maps=_int_env("MAX_INFLIGHT_MAPS"),
        max_inflight_crawls=_int_env("MAX_INFLIGHT_CRAWLS"),
        admission_wait_s=_float_env("ADMISSION_WAIT_S"),
        admission_retry_after_s=_int_env("ADMISSION_RETRY_AFTER_S"),
        max_internal_fanout=_int_env("MAX_INTERNAL_FANOUT"),
        max_content_bytes=_int_env("MAX_CONTENT_BYTES"),
        chunk_concurrency=_int_env("CHUNK_CONCURRENCY"),
        site_default_delay_s=_float_env("SITE_DEFAULT_DELAY_S"),
        site_max_jitter_s=_float_env("SITE_MAX_JITTER_S"),
        site_max_cooldown_s=_int_env("SITE_MAX_COOLDOWN_S"),
        robots_cache_ttl_s=_int_env("ROBOTS_CACHE_TTL_S"),
        max_robots_bytes=_int_env("MAX_ROBOTS_BYTES"),
        max_sitemap_bytes=_int_env("MAX_SITEMAP_BYTES"),
        max_sitemap_entries=_int_env("MAX_SITEMAP_ENTRIES"),
        max_sitemap_documents=_int_env("MAX_SITEMAP_DOCUMENTS"),
        page_cache_enabled=_bool_env("PAGE_CACHE_ENABLED"),
        page_cache_path=_required("PAGE_CACHE_PATH"),
        page_cache_ttl_s=_int_env("PAGE_CACHE_TTL_S"),
        page_cache_stale_s=_int_env("PAGE_CACHE_STALE_S"),
        page_cache_retention_s=_int_env("PAGE_CACHE_RETENTION_S"),
        page_cache_raw_html_enabled=_bool_env("PAGE_CACHE_RAW_HTML_ENABLED"),
        page_diff_max_input_lines=_int_env("PAGE_DIFF_MAX_INPUT_LINES"),
        page_diff_max_operations=_int_env("PAGE_DIFF_MAX_OPERATIONS"),
        page_diff_max_output_lines=_int_env("PAGE_DIFF_MAX_OUTPUT_LINES"),
        crawl_jobs_enabled=_bool_env("CRAWL_JOBS_ENABLED"),
        crawl_job_path=_required("CRAWL_JOB_PATH"),
        crawl_sync_max_pages=_int_env("CRAWL_SYNC_MAX_PAGES"),
        crawl_job_retention_s=_int_env("CRAWL_JOB_RETENTION_S"),
        crawl_job_expired_tombstone_s=_int_env("CRAWL_JOB_EXPIRED_TOMBSTONE_S"),
        crawl_job_max_attempts=_int_env("CRAWL_JOB_MAX_ATTEMPTS"),
        crawl_job_retry_base_s=_float_env("CRAWL_JOB_RETRY_BASE_S"),
        crawl_job_attempt_deadline_s=_float_env("CRAWL_JOB_ATTEMPT_DEADLINE_S"),
        crawl_job_max_records=_int_env("CRAWL_JOB_MAX_RECORDS"),
        crawl_job_raw_html_enabled=_bool_env("CRAWL_JOB_RAW_HTML_ENABLED"),
        crawl_job_max_inflight_requests=_int_env(
            "CRAWL_JOB_MAX_INFLIGHT_REQUESTS"
        ),
        crawl_job_result_page_max_items=_int_env("CRAWL_JOB_RESULT_PAGE_MAX_ITEMS"),
        crawl_job_result_page_max_bytes=_int_env("CRAWL_JOB_RESULT_PAGE_MAX_BYTES"),
        search_profiles={
            profile: SearchProfileDefaults(
                token_budget=_int_env(f"SEARCH_PROFILE_{profile.upper()}_TOKEN_BUDGET"),
                max_urls=_int_env(f"SEARCH_PROFILE_{profile.upper()}_MAX_URLS"),
                max_passages=_int_env(f"SEARCH_PROFILE_{profile.upper()}_MAX_PASSAGES"),
            )
            for profile in ("quick", "research", "deep")
        },
        url_safety_policy=UrlSafetyPolicy(
            blocked_ip_categories=_set_env("URL_SAFETY_BLOCKED_IP_CATEGORIES"),
            blocked_special_ips=_ip_set_env("URL_SAFETY_BLOCKED_SPECIAL_IPS"),
            nat64_networks=_ipv6_networks_env("URL_SAFETY_NAT64_NETWORKS"),
            six_to_four_networks=_ipv6_networks_env("URL_SAFETY_SIX_TO_FOUR_NETWORKS"),
            ipv4_compat_networks=_ipv6_networks_env("URL_SAFETY_IPV4_COMPAT_NETWORKS"),
        ),
    )
