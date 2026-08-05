"""SearXNG discovery client."""
import httpx

from ..observability import request_id_headers
from ..types import DiscoveryEngineFailure, DiscoveryOutcome, DiscoveryResult


class DiscoveryUnavailable(Exception):
    pass


SEARXNG_INTERNAL_HEADERS = {"X-Real-IP": "127.0.0.1"}


def _search_params(subquery: str, freshness: str | None) -> dict[str, str]:
    params = {"q": subquery, "format": "json"}
    if freshness:
        params["time_range"] = freshness
    return params


def _search_headers(api_key: str | None) -> dict[str, str]:
    headers = dict(SEARXNG_INTERNAL_HEADERS)
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return request_id_headers(headers)


def _parse_results(payload: dict) -> list[DiscoveryResult]:
    results = []
    for rank, item in enumerate(payload.get("results", [])):
        score = float(item.get("score") or 0.0)
        if score <= 0.0:
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
    return [result for result in results if result.url]


def _parse_failures(payload: dict) -> list[DiscoveryEngineFailure]:
    failures = []
    for item in payload.get("unresponsive_engines", []):
        if not isinstance(item, (list, tuple)) or not item:
            continue
        engine = str(item[0]).strip()
        reason = str(item[1]).strip() if len(item) > 1 else "unresponsive"
        if engine:
            failures.append(DiscoveryEngineFailure(engine, reason))
    return failures


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

    async def search(self, subquery: str, freshness: str | None = None) -> DiscoveryOutcome:
        params = _search_params(subquery, freshness)
        headers = _search_headers(self.api_key)
        try:
            response = await self._client.get(f"{self.base_url}/search", params=params, headers=headers)
        except Exception as exc:
            raise DiscoveryUnavailable(str(exc)) from exc
        if response.status_code != 200:
            raise DiscoveryUnavailable(f"status {response.status_code}")
        payload = response.json()
        return DiscoveryOutcome(_parse_results(payload), _parse_failures(payload))
