"""Closed internal outcome codes for future web-intelligence stages."""
from enum import Enum


class FetchOutcomeCode(str, Enum):
    CONTENT = "content"
    EMPTY_SHELL = "empty_shell"
    CHALLENGE = "challenge"
    ROBOTS_REFUSED = "robots_refused"
    UPSTREAM_TIMEOUT = "upstream_timeout"
    DEADLINE_CANCELLED = "deadline_cancelled"
    UNSAFE_REDIRECT = "unsafe_redirect"
    UNSUPPORTED_CONTENT = "unsupported_content"
    UNSUPPORTED_CAPABILITY = "unsupported_capability"
    CONTENT_TOO_LARGE = "content_too_large"
    EXTRACTION_EMPTY = "extraction_empty"
    MALFORMED_UPSTREAM_RESPONSE = "malformed_upstream_response"
    UPSTREAM_FAILURE = "upstream_failure"
    RATE_LIMITED = "rate_limited"
    CAPACITY_UNAVAILABLE = "capacity_unavailable"
    UNSAFE_TARGET = "unsafe_target"
    LOCAL_PROCESSING_FAILURE = "local_processing_failure"


class MapOutcomeCode(str, Enum):
    COMPLETED = "completed"
    PARTIAL = "partial"
    CANCELLED = "cancelled"
    UNSAFE_SEED = "unsafe_seed"
    UNSAFE_REDIRECT = "unsafe_redirect"
    ROBOTS_REFUSED = "robots_refused"
    LIMIT_REACHED = "limit_reached"
    UPSTREAM_TIMEOUT = "upstream_timeout"
    DEADLINE_CANCELLED = "deadline_cancelled"
    MALFORMED_UPSTREAM_RESPONSE = "malformed_upstream_response"
    UPSTREAM_FAILURE = "upstream_failure"
    RATE_LIMITED = "rate_limited"
    UNSAFE_TARGET = "unsafe_target"
    LOCAL_PROCESSING_FAILURE = "local_processing_failure"


class CrawlOutcomeCode(str, Enum):
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"
    NO_ADMITTED_URLS = "no_admitted_urls"
    DEADLINE_CANCELLED = "deadline_cancelled"
    UPSTREAM_TIMEOUT = "upstream_timeout"
    UPSTREAM_FAILURE = "upstream_failure"
    RATE_LIMITED = "rate_limited"
    UNSAFE_SEED = "unsafe_seed"
    UNSAFE_TARGET = "unsafe_target"
    UNSAFE_REDIRECT = "unsafe_redirect"
    LOCAL_PROCESSING_FAILURE = "local_processing_failure"


class CacheOutcomeCode(str, Enum):
    MISS = "miss"
    FRESH = "fresh"
    STALE = "stale"
    REVALIDATED = "revalidated"
    BYPASS = "bypass"
    CORRUPT = "corrupt"
    UNSUPPORTED_CAPABILITY = "unsupported_capability"


class JobOutcomeCode(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
