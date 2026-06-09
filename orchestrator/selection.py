"""Pure URL selection policy."""
from .normalize import canonical_host, host_for
from .types import DiscoveryResult


def _matches_host(host: str, pattern: str) -> bool:
    return host == pattern or host.endswith(f".{pattern}")


class SelectionPolicyImpl:
    def select(
        self,
        results: list[DiscoveryResult],
        max_urls: int,
        blocklist: set[str],
        allowlist: set[str] | None = None,
    ) -> list[DiscoveryResult]:
        blocked = {canonical_host(host) for host in blocklist}
        blocked.discard("")
        allowed = {canonical_host(host) for host in allowlist} if allowlist is not None else None
        if allowed is not None:
            allowed.discard("")
        candidates = [
            result
            for result in results
            if (host := host_for(result.url))
            and not any(_matches_host(host, item) for item in blocked)
            and (allowed is None or any(_matches_host(host, item) for item in allowed))
        ]
        return sorted(candidates, key=lambda r: (-r.score, r.url))[:max_urls]
