"""Pure URL selection policy."""
from .normalize import host_for
from .types import DiscoveryResult


class SelectionPolicyImpl:
    def select(
        self,
        results: list[DiscoveryResult],
        max_urls: int,
        blocklist: set[str],
        allowlist: set[str] | None = None,
    ) -> list[DiscoveryResult]:
        blocked = {host.lower() for host in blocklist}
        allowed = {host.lower() for host in allowlist} if allowlist else None
        candidates = [
            result
            for result in results
            if host_for(result.url) not in blocked
            and (allowed is None or host_for(result.url) in allowed)
        ]
        return sorted(candidates, key=lambda r: (-r.score, r.url))[:max_urls]
