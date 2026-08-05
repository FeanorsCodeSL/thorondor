"""Pure configuration state for the Thorondor Textual app and deploy helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

from .envfile import ENV_TEMPLATE, LLAMACPP_TEMPLATE, read_env, seed_from_example, write_env
from .project import missing_project_paths
from .secret import generate_crawl4ai_api_key, generate_searxng_secret

Mode = Literal["byo", "bundled-models", "llamacpp"]
Action = Literal["Mode", "Endpoints", "Search/crawl", "Validate", "Deploy", "MCP", "Manual repair"]

HOST_ENDPOINTS_OVERLAY = "docker-compose.host-endpoints.yml"

INTEGER_KEYS = {
    "ORCHESTRATOR_PORT",
    "HEALTHCHECK_MAX_CONNECTIONS",
    "HEALTHCHECK_MAX_KEEPALIVE_CONNECTIONS",
    "MAX_SUBQUERIES",
    "SEARCH_PROFILE_QUICK_TOKEN_BUDGET",
    "SEARCH_PROFILE_QUICK_MAX_URLS",
    "SEARCH_PROFILE_QUICK_MAX_PASSAGES",
    "SEARCH_PROFILE_RESEARCH_TOKEN_BUDGET",
    "SEARCH_PROFILE_RESEARCH_MAX_URLS",
    "SEARCH_PROFILE_RESEARCH_MAX_PASSAGES",
    "SEARCH_PROFILE_DEEP_TOKEN_BUDGET",
    "SEARCH_PROFILE_DEEP_MAX_URLS",
    "SEARCH_PROFILE_DEEP_MAX_PASSAGES",
    "EMBEDDING_BATCH_SIZE",
    "RERANKER_BATCH_SIZE",
    "RERANKER_TIMEOUT_S",
    "MAX_URLS",
    "CRAWL_CONCURRENCY",
    "CRAWL_PER_HOST_CONCURRENCY",
    "CRAWL_TIMEOUT_S",
    "CRAWL_MAX_PREFLIGHT_REDIRECTS",
    "DEFAULT_TOKEN_BUDGET",
    "PROXY_PORT",
    "PROXY_MAX_HEADER_BYTES",
    "PROXY_READ_CHUNK_BYTES",
    "PROXY_RELAY_CHUNK_BYTES",
}
FLOAT_KEYS = {"HEALTHCHECK_TIMEOUT_S", "EMBEDDING_TIMEOUT_S", "RELEVANCE_SCORE_FLOOR"}
BOOL_KEYS = {
    "MARKDOWN_EXTRACTOR_FAVOR_RECALL",
    "MARKDOWN_EXTRACTOR_INCLUDE_COMMENTS",
    "MARKDOWN_EXTRACTOR_INCLUDE_TABLES",
    "MARKDOWN_EXTRACTOR_DEDUPLICATE",
    "CRAWL_RESPECT_ROBOTS_TXT",
    "CRAWL_VALIDATE_REDIRECTS",
    "ALLOWLIST_ONLY",
}


@dataclass(frozen=True)
class Issue:
    label: str
    action: Action
    severity: Literal["info", "warning", "error"] = "warning"


@dataclass(frozen=True)
class Draft:
    project_dir: Path
    env_path: Path
    llamacpp_env_path: Path
    env: dict[str, str]
    llamacpp_env: dict[str, str]
    template_env: dict[str, str]
    template_llamacpp_env: dict[str, str]
    missing_project_paths: list[str]
    missing_env_keys: list[str]
    invalid_values: list[str]
    mode: Mode
    profile: str
    overlay_needed: bool
    host_rewritten: bool
    env_exists: bool
    llamacpp_env_exists: bool
    token_present: dict[str, bool] = field(default_factory=dict)
    last_validation: str | None = None
    last_deploy: str | None = None


@dataclass(frozen=True)
class ConfigAnswers:
    mode: Mode = "byo"
    embedding_endpoint: str = "http://embedding:80"
    embedding_model: str = "BAAI/bge-m3"
    embedding_api_key: str = ""
    reranker_endpoint: str = "http://reranker:80"
    reranker_model: str = "BAAI/bge-reranker-v2-m3"
    reranker_path: str = "/rerank"
    reranker_health_path: str = "/health"
    reranker_api_key: str = ""
    llm_endpoint: str = ""
    llm_model: str = ""
    llm_api_key: str = ""
    orchestrator_host: str = "127.0.0.1"
    orchestrator_port: str = "8080"
    search_overrides: dict[str, str] = field(default_factory=dict)
    llamacpp_overrides: dict[str, str] = field(default_factory=dict)
    docker_run: bool = True


def _bool_valid(value: str) -> bool:
    return value.lower() in {"1", "0", "true", "false", "yes", "no", "on", "off"}


def _invalid_values(values: dict[str, str]) -> list[str]:
    invalid: list[str] = []
    for key in INTEGER_KEYS & values.keys():
        try:
            int(values[key])
        except ValueError:
            invalid.append(key)
    for key in FLOAT_KEYS & values.keys():
        try:
            float(values[key])
        except ValueError:
            invalid.append(key)
    for key in BOOL_KEYS & values.keys():
        if not _bool_valid(values[key]):
            invalid.append(key)
    return sorted(invalid)


def _mode_from_env(values: dict[str, str], llamacpp_env_exists: bool) -> Mode:
    embedding = values.get("EMBEDDING_ENDPOINT", "")
    reranker = values.get("RERANKER_ENDPOINT", "")
    if llamacpp_env_exists and embedding == "http://embedding:8080":
        return "llamacpp"
    if embedding == "http://embedding:80" and reranker == "http://reranker:80":
        return "bundled-models"
    return "byo"


def _profile_for_mode(mode: Mode) -> str:
    if mode == "bundled-models":
        return "bundled-models"
    if mode == "llamacpp":
        return "llamacpp-models"
    return ""


def _endpoint_host(endpoint: str) -> str:
    return (urlsplit(endpoint).hostname or "").lower()


def rewrite_host_for_docker(url: str) -> tuple[str, bool]:
    parsed = urlsplit(url)
    if parsed.hostname not in {"localhost", "127.0.0.1"}:
        return url, False
    netloc = "host.docker.internal"
    if parsed.port is not None:
        netloc = f"{netloc}:{parsed.port}"
    if parsed.username or parsed.password:
        userinfo = parsed.username or ""
        if parsed.password:
            userinfo = f"{userinfo}:{parsed.password}"
        netloc = f"{userinfo}@{netloc}"
    return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment)), True


def embedding_needs_overlay(endpoint: str) -> bool:
    host = _endpoint_host(endpoint)
    return bool(host and host not in {"embedding"})


def endpoint_is_host_rewritten(endpoint: str) -> bool:
    return _endpoint_host(endpoint) == "host.docker.internal"


def load_draft(project_dir: str | Path) -> Draft:
    root = Path(project_dir).expanduser().resolve()
    env_path = root / ".env"
    llamacpp_env_path = root / ".env.llamacpp"
    template_env = seed_from_example(template_name=ENV_TEMPLATE, project_dir=root)
    template_llamacpp = seed_from_example(template_name=LLAMACPP_TEMPLATE, project_dir=root)
    actual_env = read_env(env_path)
    actual_llamacpp = read_env(llamacpp_env_path)
    env = {**template_env, **actual_env}
    llamacpp_env = {**template_llamacpp, **actual_llamacpp}
    missing_keys = sorted(key for key in template_env if key not in actual_env)
    mode = _mode_from_env(env, llamacpp_env_path.exists())
    overlay_needed = mode == "byo" and embedding_needs_overlay(env.get("EMBEDDING_ENDPOINT", ""))
    return Draft(
        project_dir=root,
        env_path=env_path,
        llamacpp_env_path=llamacpp_env_path,
        env=env,
        llamacpp_env=llamacpp_env,
        template_env=template_env,
        template_llamacpp_env=template_llamacpp,
        missing_project_paths=missing_project_paths(root),
        missing_env_keys=missing_keys,
        invalid_values=_invalid_values(env),
        mode=mode,
        profile=_profile_for_mode(mode),
        overlay_needed=overlay_needed,
        host_rewritten=endpoint_is_host_rewritten(env.get("EMBEDDING_ENDPOINT", ""))
        or endpoint_is_host_rewritten(env.get("RERANKER_ENDPOINT", "")),
        env_exists=env_path.exists(),
        llamacpp_env_exists=llamacpp_env_path.exists(),
        token_present={
            "embedding": bool(env.get("EMBEDDING_API_KEY", "").strip()),
            "reranker": bool(env.get("RERANKER_API_KEY", "").strip()),
            "llm": bool(env.get("LLM_API_KEY", "").strip()),
        },
    )


def compute_issues(draft: Draft, harness_status: dict[str, bool] | None = None) -> list[Issue]:
    issues: list[Issue] = []
    if draft.missing_project_paths:
        issues.append(
            Issue(
                f"Project directory is missing {', '.join(draft.missing_project_paths)}",
                "Manual repair",
                "error",
            )
        )
    if not draft.env_exists:
        issues.append(
            Issue("Missing .env - choose Mode or Endpoints to seed from template", "Mode")
        )
    if draft.missing_env_keys:
        issues.append(
            Issue(
                f".env is missing {len(draft.missing_env_keys)} required keys",
                "Validate",
                "error",
            )
        )
    if not draft.env.get("SEARXNG_SECRET", "").strip():
        issues.append(Issue("SEARXNG_SECRET blank - generated on save", "Mode"))
    if not draft.env.get("CRAWL4AI_API_KEY", "").strip():
        issues.append(Issue("CRAWL4AI_API_KEY blank - generated on save", "Mode"))
    for key in draft.invalid_values:
        issues.append(Issue(f"{key} has an invalid value", "Search/crawl", "error"))
    if draft.mode == "byo":
        if not draft.env.get("EMBEDDING_ENDPOINT", "").strip():
            issues.append(Issue("Embedding endpoint is blank", "Endpoints", "error"))
        if not draft.env.get("RERANKER_ENDPOINT", "").strip():
            issues.append(Issue("Reranker endpoint is blank", "Endpoints", "error"))
        if draft.overlay_needed:
            issues.append(
                Issue("External embedding requires generated host-endpoints overlay", "Validate")
            )
    if draft.mode == "llamacpp":
        missing = missing_llamacpp_models(draft.project_dir, draft.llamacpp_env)
        for model_path in missing:
            issues.append(Issue(f"Missing GGUF model file {model_path}", "Mode", "error"))
    harness_status = harness_status or {}
    if harness_status and not any(harness_status.values()):
        issues.append(Issue("No MCP harness wired", "MCP", "info"))
    return issues


def _with_docker_rewrite(endpoint: str, docker_run: bool) -> tuple[str, bool]:
    if not docker_run:
        return endpoint, False
    return rewrite_host_for_docker(endpoint)


def build_env_values(
    answers: ConfigAnswers,
    existing: dict[str, str] | None = None,
) -> dict[str, str]:
    template = seed_from_example(template_name=ENV_TEMPLATE)
    existing = existing or {}
    values = {**template, **existing}
    embedding_endpoint, _ = _with_docker_rewrite(answers.embedding_endpoint, answers.docker_run)
    reranker_endpoint, _ = _with_docker_rewrite(answers.reranker_endpoint, answers.docker_run)

    if answers.mode == "bundled-models":
        embedding_endpoint = "http://embedding:80"
        reranker_endpoint = "http://reranker:80"
        values.update(
            EMBEDDING_MODEL="BAAI/bge-m3",
            EMBEDDING_MODEL_REVISION="5617a9f61b028005a4858fdac845db406aefb181",
            RERANKER_MODEL="BAAI/bge-reranker-v2-m3",
            RERANKER_MODEL_REVISION="953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e",
            RERANKER_PATH="/rerank",
            RERANKER_HEALTH_PATH="/health",
        )
    elif answers.mode == "llamacpp":
        embedding_endpoint = "http://embedding:8080"
        reranker_endpoint = "http://reranker:8080"
        llamacpp = {
            **seed_from_example(template_name=LLAMACPP_TEMPLATE),
            **answers.llamacpp_overrides,
        }
        values.update(
            EMBEDDING_MODEL=llamacpp.get("LLAMACPP_EMBEDDING_ALIAS", "bge-m3"),
            RERANKER_MODEL=llamacpp.get("LLAMACPP_RERANKER_ALIAS", "bge-reranker-v2-m3"),
            RERANKER_PATH="/reranking",
            RERANKER_HEALTH_PATH="/health",
        )
    else:
        values.update(
            EMBEDDING_MODEL=answers.embedding_model,
            RERANKER_MODEL=answers.reranker_model,
            RERANKER_PATH=answers.reranker_path,
            RERANKER_HEALTH_PATH=answers.reranker_health_path,
        )

    values.update(
        ORCHESTRATOR_HOST=answers.orchestrator_host,
        ORCHESTRATOR_PORT=answers.orchestrator_port,
        EMBEDDING_ENDPOINT=embedding_endpoint,
        EMBEDDING_API_KEY=answers.embedding_api_key,
        RERANKER_ENDPOINT=reranker_endpoint,
        RERANKER_API_KEY=answers.reranker_api_key,
        LLM_ENDPOINT=answers.llm_endpoint,
        LLM_MODEL=answers.llm_model,
        LLM_API_KEY=answers.llm_api_key,
    )
    values.update(answers.search_overrides)
    if not values.get("SEARXNG_SECRET", "").strip():
        values["SEARXNG_SECRET"] = generate_searxng_secret()
    if not values.get("CRAWL4AI_API_KEY", "").strip():
        values["CRAWL4AI_API_KEY"] = generate_crawl4ai_api_key()
    return values


def build_llamacpp_env_values(answers: ConfigAnswers) -> dict[str, str]:
    values = seed_from_example(template_name=LLAMACPP_TEMPLATE)
    values.update(answers.llamacpp_overrides)
    return values


def compose_overlays(config: ConfigAnswers | Draft | dict[str, str]) -> list[str]:
    if isinstance(config, Draft):
        return [HOST_ENDPOINTS_OVERLAY] if config.overlay_needed else []
    if isinstance(config, ConfigAnswers):
        if config.mode != "byo":
            return []
        endpoint, _ = _with_docker_rewrite(config.embedding_endpoint, config.docker_run)
    else:
        endpoint = config.get("EMBEDDING_ENDPOINT", "")
    return [HOST_ENDPOINTS_OVERLAY] if embedding_needs_overlay(endpoint) else []


def host_endpoints_overlay_text(*, host_rewritten: bool) -> str:
    extra_hosts = (
        '\n    extra_hosts:\n      - "host.docker.internal:host-gateway"'
        if host_rewritten
        else ""
    )
    orchestrator_extra_hosts = (
        '\n    extra_hosts:\n      - "host.docker.internal:host-gateway"'
        if host_rewritten
        else ""
    )
    return (
        "services:\n"
        "  chunker:\n"
        "    networks:\n"
        "      - internal\n"
        "      - egress"
        f"{extra_hosts}\n"
        "  orchestrator:"
        f"{orchestrator_extra_hosts}\n"
    )


def write_host_endpoints_overlay(project_dir: str | Path, *, host_rewritten: bool) -> Path:
    target = Path(project_dir).expanduser().resolve() / HOST_ENDPOINTS_OVERLAY
    target.write_text(host_endpoints_overlay_text(host_rewritten=host_rewritten), encoding="utf-8")
    return target


def missing_llamacpp_models(project_dir: str | Path, values: dict[str, str]) -> list[str]:
    root = Path(project_dir).expanduser().resolve()
    missing: list[str] = []
    for key in ("LLAMACPP_EMBEDDING_MODEL", "LLAMACPP_RERANKER_MODEL"):
        model = values.get(key, "")
        if not model.startswith("/models/"):
            continue
        local = root / "models" / model.removeprefix("/models/")
        if not local.exists():
            missing.append(model)
    return missing


class MissingLlamaCppModelsError(ValueError):
    """Raised when the llamacpp profile is selected but GGUF files are missing.

    Carries the list of container paths that need to be on disk; the TUI
    and the ``download-models`` subcommand catch this and download them
    via :mod:`thorondor_cli.models` before retrying the save.
    """

    def __init__(self, missing: list[str]) -> None:
        self.missing = list(missing)
        super().__init__(f"Missing GGUF model files: {', '.join(self.missing)}")


def persist_env_changes(project_dir: str | Path, answers: ConfigAnswers) -> Draft:
    root = Path(project_dir).expanduser().resolve()
    template = seed_from_example(template_name=ENV_TEMPLATE, project_dir=root)
    existing = read_env(root / ".env")
    values = build_env_values(answers, existing=existing)
    llamacpp_values: dict[str, str] | None = None
    if answers.mode == "llamacpp":
        llamacpp_values = build_llamacpp_env_values(answers)
        missing = missing_llamacpp_models(root, llamacpp_values)
        if missing:
            raise MissingLlamaCppModelsError(missing)
    write_env(
        root / ".env",
        values,
        template_values=template,
        preserve_existing_nonblank=("SEARXNG_SECRET", "CRAWL4AI_API_KEY"),
    )
    if answers.mode == "llamacpp":
        write_env(
            root / ".env.llamacpp",
            llamacpp_values or {},
            template_values=seed_from_example(template_name=LLAMACPP_TEMPLATE, project_dir=root),
        )
    overlays = compose_overlays(values)
    if HOST_ENDPOINTS_OVERLAY in overlays:
        write_host_endpoints_overlay(
            root,
            host_rewritten=endpoint_is_host_rewritten(values.get("EMBEDDING_ENDPOINT", ""))
            or endpoint_is_host_rewritten(values.get("RERANKER_ENDPOINT", "")),
        )
    return load_draft(root)
