import argparse
import json
import sys
from urllib.parse import urlsplit


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


def _proxy_host(service: dict, variable: str) -> str | None:
    environment = service.get("environment", {})
    if not isinstance(environment, dict):
        return None
    value = environment.get(variable)
    return urlsplit(value).hostname if isinstance(value, str) else None


def _environment_value(service: dict, variable: str) -> str | None:
    environment = service.get("environment", {})
    if not isinstance(environment, dict):
        return None
    value = environment.get(variable)
    return value if isinstance(value, str) else None


def validate(config: dict, crawl_name: str, proxy_name: str, provider_names: list[str]) -> list[str]:
    errors = []
    services = config.get("services", {})
    networks = config.get("networks", {})
    crawl = services.get(crawl_name, {})
    proxy = services.get(proxy_name, {})
    crawl_networks = _networks(crawl)
    proxy_networks = _networks(proxy)

    if len(crawl_networks) != 1:
        errors.append(f"{crawl_name} must have exactly one network")
    elif not _is_internal(networks, next(iter(crawl_networks))):
        errors.append(f"{crawl_name} network must be internal")
    if not crawl_networks or not crawl_networks <= proxy_networks:
        errors.append(f"{proxy_name} must share the internal network with {crawl_name}")
    if not any(not _is_internal(networks, name) for name in proxy_networks):
        errors.append(f"{proxy_name} must have provider egress")
    for variable in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
    ):
        if _proxy_host(crawl, variable) != proxy_name:
            errors.append(f"{crawl_name} {variable} must target {proxy_name}")
    no_proxy = _environment_value(crawl, "NO_PROXY")
    lower_no_proxy = _environment_value(crawl, "no_proxy")
    if no_proxy is None or lower_no_proxy is None:
        errors.append(f"{crawl_name} must define NO_PROXY and no_proxy")
    elif no_proxy != lower_no_proxy:
        errors.append(f"{crawl_name} NO_PROXY and no_proxy must match")
    else:
        allowed_no_proxy = {"localhost", "127.0.0.1", "::1"}
        entries = {entry.strip().lower() for entry in no_proxy.split(",") if entry.strip()}
        if not entries <= allowed_no_proxy:
            errors.append(f"{crawl_name} NO_PROXY may contain only loopback targets")
    for provider_name in provider_names:
        provider_networks = _networks(services.get(provider_name, {}))
        if not any(not _is_internal(networks, name) for name in provider_networks):
            errors.append(f"{provider_name} must retain provider-plane egress")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--crawl-service", required=True)
    parser.add_argument("--proxy-service", required=True)
    parser.add_argument("--provider-service", action="append", default=[])
    args = parser.parse_args()
    errors = validate(
        json.load(sys.stdin),
        args.crawl_service,
        args.proxy_service,
        args.provider_service,
    )
    for error in errors:
        print(error, file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
