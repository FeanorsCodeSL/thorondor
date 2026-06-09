"""SearXNG discovery client."""
import httpx

from ..types import DiscoveryResult


class DiscoveryUnavailable(Exception):
    pass


class SearxngDiscovery:
    def __init__(self, base_url: str, client: httpx.AsyncClient | None = None, api_key: str | None = None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._client = client or httpx.AsyncClient(
            timeout=15.0,
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
        self._owns_client = client is None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def search(self, subquery: str, freshness: str | None = None) -> list[DiscoveryResult]:
        params = {"q": subquery, "format": "json"}
        if freshness:
            params["time_range"] = freshness
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else None
        try:
            response = await self._client.get(f"{self.base_url}/search", params=params, headers=headers)
        except Exception as exc:
            raise DiscoveryUnavailable(str(exc)) from exc
        if response.status_code != 200:
            raise DiscoveryUnavailable(f"status {response.status_code}")
        payload = response.json()
        results = []
        for rank, item in enumerate(payload.get("results", [])):
            score = float(item.get("score") or 0.0)
            if score == 0.0:
                score = 1.0 / (rank + 1)
            results.append(
                DiscoveryResult(
                    title=item.get("title") or item.get("url") or "",
                    url=item.get("url", ""),
                    snippet=item.get("content", ""),
                    engine=item.get("engine", ""),
                    score=score,
                )
            )
        return [r for r in results if r.url]
