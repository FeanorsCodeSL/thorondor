"""SearXNG discovery client."""
import httpx

from ..types import DiscoveryResult


class DiscoveryUnavailable(Exception):
    pass


class SearxngDiscovery:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    async def search(self, subquery: str, freshness: str | None = None) -> list[DiscoveryResult]:
        params = {"q": subquery, "format": "json"}
        if freshness:
            params["time_range"] = freshness
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(f"{self.base_url}/search", params=params)
        except Exception as exc:
            raise DiscoveryUnavailable(str(exc)) from exc
        if response.status_code != 200:
            raise DiscoveryUnavailable(f"status {response.status_code}")
        payload = response.json()
        results = []
        for item in payload.get("results", []):
            results.append(
                DiscoveryResult(
                    title=item.get("title") or item.get("url") or "",
                    url=item.get("url", ""),
                    snippet=item.get("content", ""),
                    engine=item.get("engine", ""),
                    score=float(item.get("score") or 0.0),
                )
            )
        return [r for r in results if r.url]
