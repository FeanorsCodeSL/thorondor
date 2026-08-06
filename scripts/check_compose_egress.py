import argparse
import json
import sys


PROXY_ENVIRONMENT_VARIABLES = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "NO_PROXY",
    "no_proxy",
)
REQUIRED_TMPFS_TARGETS = {
    "/tmp",
    "/var/lib/redis",
    "/var/lib/crawl4ai/outputs",
    "/home/appuser/.crawl4ai",
    "/home/appuser/.cache/url_seeder",
    "/home/appuser/.gunicorn",
}
EXPECTED_HEALTHCHECK = ["CMD", "curl", "-f", "http://localhost:11235/health"]


def _networks(service: dict) -> set[str]:
    value = service.get("networks", {})
    if isinstance(value, dict):
        return set(value)
    if isinstance(value, list):
        return set(value)
    return set()


def _is_internal(networks: dict, name: str) -> bool:
    value = networks.get(name, {})
    return isinstance(value, dict) and value.get("internal") is True


def _network_members(services: dict, network: str) -> set[str]:
    return {name for name, service in services.items() if network in _networks(service)}


def _gateway_priority(service: dict, network: str) -> int:
    networks = service.get("networks", {})
    if not isinstance(networks, dict):
        return 0
    options = networks.get(network)
    if not isinstance(options, dict):
        return 0
    value = options.get("gw_priority", 0)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _environment(service: dict) -> dict:
    value = service.get("environment", {})
    return value if isinstance(value, dict) else {}


def _tmpfs_targets(service: dict) -> set[str]:
    value = service.get("tmpfs", [])
    if not isinstance(value, list):
        return set()
    return {
        entry.split(":", 1)[0]
        for entry in value
        if isinstance(entry, str) and entry.startswith("/")
    }


def validate(config: dict, crawl_name: str, control_peer_name: str, provider_names: list[str]) -> list[str]:
    errors = []
    services = config.get("services", {})
    networks = config.get("networks", {})
    crawl = services.get(crawl_name, {})
    crawl_networks = _networks(crawl)
    internal_networks = {name for name in crawl_networks if _is_internal(networks, name)}
    egress_networks = {name for name in crawl_networks if not _is_internal(networks, name)}

    if len(internal_networks) != 1:
        errors.append(f"{crawl_name} must have exactly one internal control network")
    else:
        control_network = next(iter(internal_networks))
        if _network_members(services, control_network) != {crawl_name, control_peer_name}:
            errors.append(
                f"{crawl_name} internal control network must be shared only with {control_peer_name}"
            )

    if len(egress_networks) != 1:
        errors.append(f"{crawl_name} must have exactly one dedicated egress network")
    else:
        egress_network = next(iter(egress_networks))
        definition = networks.get(egress_network, {})
        if not isinstance(definition, dict) or definition.get("external") is True:
            errors.append(f"{crawl_name} egress network must be managed by this Compose project")
        elif _network_members(services, egress_network) != {crawl_name}:
            errors.append(f"{crawl_name} egress network must not be shared with other services")
        other_gateway_priority = max(
            (_gateway_priority(crawl, name) for name in crawl_networks - {egress_network}),
            default=0,
        )
        if _gateway_priority(crawl, egress_network) <= other_gateway_priority:
            errors.append(f"{crawl_name} egress network must be the default gateway")

    environment = _environment(crawl)
    if environment.get("CRAWL4AI_ALLOW_INTERNAL_URLS") != "false":
        errors.append(f"{crawl_name} must reject internal target URLs")
    for variable in PROXY_ENVIRONMENT_VARIABLES:
        if variable in environment:
            errors.append(f"{crawl_name} must not define {variable}")
    if "CRAWL4AI_API_TOKEN" not in environment:
        errors.append(f"{crawl_name} must define CRAWL4AI_API_TOKEN")

    if crawl.get("ports"):
        errors.append(f"{crawl_name} API port must not be published")
    image = crawl.get("image")
    if not isinstance(image, str) or "@sha256:" not in image:
        errors.append(f"{crawl_name} image must be pinned by digest")
    if crawl.get("user") != "appuser":
        errors.append(f"{crawl_name} must run as appuser")
    if crawl.get("read_only") is not True:
        errors.append(f"{crawl_name} root filesystem must be read-only")
    if "ALL" not in crawl.get("cap_drop", []):
        errors.append(f"{crawl_name} must drop all Linux capabilities")
    if "no-new-privileges:true" not in crawl.get("security_opt", []):
        errors.append(f"{crawl_name} must disable privilege escalation")
    if crawl.get("pids_limit") != 512:
        errors.append(f"{crawl_name} pids_limit must be 512")
    if not REQUIRED_TMPFS_TARGETS <= _tmpfs_targets(crawl):
        errors.append(f"{crawl_name} must define every required writable tmpfs")

    healthcheck = crawl.get("healthcheck", {})
    health_test = healthcheck.get("test") if isinstance(healthcheck, dict) else None
    if health_test != EXPECTED_HEALTHCHECK:
        errors.append(f"{crawl_name} healthcheck must probe only /health")

    for provider_name in provider_names:
        provider_networks = _networks(services.get(provider_name, {}))
        if not any(not _is_internal(networks, name) for name in provider_networks):
            errors.append(f"{provider_name} must retain provider-plane egress")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--crawl-service", required=True)
    parser.add_argument("--control-peer-service", required=True)
    parser.add_argument("--provider-service", action="append", default=[])
    args = parser.parse_args()
    errors = validate(
        json.load(sys.stdin),
        args.crawl_service,
        args.control_peer_service,
        args.provider_service,
    )
    for error in errors:
        print(error, file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
