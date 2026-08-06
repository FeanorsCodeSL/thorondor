import pytest

from scripts.check_compose_egress import validate


def _config() -> dict:
    return {
        "networks": {
            "internal": {"internal": True},
            "crawl-control": {"internal": True},
            "crawl-egress": {},
            "provider-egress": {},
        },
        "services": {
            "orchestrator": {"networks": ["internal", "crawl-control"]},
            "crawl4ai": {
                "image": "unclecode/crawl4ai@sha256:abc123",
                "networks": {
                    "crawl-control": None,
                    "crawl-egress": {"gw_priority": 1},
                },
                "environment": {
                    "CRAWL4AI_API_TOKEN": "token",
                    "CRAWL4AI_ALLOW_INTERNAL_URLS": "false",
                },
                "user": "appuser",
                "read_only": True,
                "cap_drop": ["ALL"],
                "security_opt": ["no-new-privileges:true"],
                "pids_limit": 512,
                "tmpfs": [
                    "/tmp",
                    "/var/lib/redis:uid=999,gid=999,mode=0700",
                    "/var/lib/crawl4ai/outputs:uid=999,gid=999,mode=0700",
                    "/home/appuser/.crawl4ai:uid=999,gid=999,mode=0700",
                    "/home/appuser/.cache/url_seeder:uid=999,gid=999,mode=0700",
                    "/home/appuser/.gunicorn:uid=999,gid=999,mode=0700",
                ],
                "healthcheck": {
                    "test": ["CMD", "curl", "-f", "http://localhost:11235/health"],
                },
            },
            "searxng": {"networks": ["internal", "provider-egress"]},
        },
    }


def _validate(config: dict) -> list[str]:
    return validate(config, "crawl4ai", "orchestrator", ["searxng"])


def test_accepts_isolated_crawl4ai_owned_egress():
    assert _validate(_config()) == []


def test_requires_direct_crawl4ai_egress():
    config = _config()
    config["services"]["crawl4ai"]["networks"] = {"crawl-control": None}

    assert _validate(config) == ["crawl4ai must have exactly one dedicated egress network"]


def test_requires_egress_as_default_gateway():
    config = _config()
    config["services"]["crawl4ai"]["networks"]["crawl-egress"] = None

    assert _validate(config) == ["crawl4ai egress network must be the default gateway"]


def test_requires_isolated_internal_control_network():
    config = _config()
    config["services"]["searxng"]["networks"].append("crawl-control")

    assert _validate(config) == [
        "crawl4ai internal control network must be shared only with orchestrator"
    ]


def test_requires_dedicated_egress_network():
    config = _config()
    config["services"]["searxng"]["networks"].append("crawl-egress")

    assert _validate(config) == ["crawl4ai egress network must not be shared with other services"]


def test_rejects_external_project_egress_network():
    config = _config()
    config["networks"]["crawl-egress"] = {"external": True}

    assert _validate(config) == ["crawl4ai egress network must be managed by this Compose project"]


def test_rejects_internal_url_escape_hatch():
    config = _config()
    config["services"]["crawl4ai"]["environment"]["CRAWL4AI_ALLOW_INTERNAL_URLS"] = "true"

    assert _validate(config) == ["crawl4ai must reject internal target URLs"]


def test_rejects_external_proxy_environment_variables():
    config = _config()
    config["services"]["crawl4ai"]["environment"]["HTTPS_PROXY"] = "http://egress-proxy:8888"

    assert _validate(config) == ["crawl4ai must not define HTTPS_PROXY"]


def test_requires_api_token_configuration():
    config = _config()
    del config["services"]["crawl4ai"]["environment"]["CRAWL4AI_API_TOKEN"]

    assert _validate(config) == ["crawl4ai must define CRAWL4AI_API_TOKEN"]


def test_rejects_published_crawl4ai_api_port():
    config = _config()
    config["services"]["crawl4ai"]["ports"] = ["11235:11235"]

    assert _validate(config) == ["crawl4ai API port must not be published"]


def test_requires_digest_pinned_crawl4ai_image():
    config = _config()
    config["services"]["crawl4ai"]["image"] = "unclecode/crawl4ai:0.9.2"

    assert _validate(config) == ["crawl4ai image must be pinned by digest"]


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("user", "root", "crawl4ai must run as appuser"),
        ("read_only", False, "crawl4ai root filesystem must be read-only"),
        ("cap_drop", [], "crawl4ai must drop all Linux capabilities"),
        ("security_opt", [], "crawl4ai must disable privilege escalation"),
        ("pids_limit", 0, "crawl4ai pids_limit must be 512"),
        ("tmpfs", ["/tmp"], "crawl4ai must define every required writable tmpfs"),
    ],
)
def test_requires_upstream_container_hardening(field, value, expected):
    config = _config()
    config["services"]["crawl4ai"][field] = value

    assert _validate(config) == [expected]


def test_rejects_redis_implementation_detail_healthcheck():
    config = _config()
    config["services"]["crawl4ai"]["healthcheck"]["test"] = [
        "CMD-SHELL",
        "redis-cli ping && curl -f http://localhost:11235/health",
    ]

    assert _validate(config) == ["crawl4ai healthcheck must probe only /health"]
