"""Environment-driven configuration for the orchestrator."""
from dataclasses import dataclass
import ipaddress
import os
import re
from urllib.parse import urlparse

from .models import MAX_SUBQUERY_COUNT
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
    search_profiles: dict[str, SearchProfileDefaults]
    url_safety_policy: UrlSafetyPolicy

    def __post_init__(self) -> None:
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
