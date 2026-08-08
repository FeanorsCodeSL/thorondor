"""SearXNG discovery client."""
import math

import httpx

from ..observability import request_id_headers
from ..types import DiscoveryContribution, DiscoveryEngineFailure, DiscoveryOutcome, DiscoveryResult


class DiscoveryUnavailable(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


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


def _engines(item: dict) -> list[str]:
    plural = item.get("engines")
    if plural is not None:
        if not isinstance(plural, list):
            raise ValueError("engines must be a list")
        if any(not isinstance(engine, str) for engine in plural):
            raise ValueError("engines must contain strings")
        engines = [engine.strip() for engine in plural if engine.strip()]
        if engines:
            return engines[:16]
    singular_value = item.get("engine")
    if singular_value is not None and not isinstance(singular_value, str):
        raise ValueError("engine must be a string")
    singular = (singular_value or "").strip()
    return [singular] if singular else []


def _positions(item: dict) -> list[int | None]:
    plural = item.get("positions")
    if plural is None:
        return []
    if not isinstance(plural, list):
        raise ValueError("positions must be a list")
    return [
        value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None
        for value in plural[:16]
    ]


def _text(item: dict, key: str, default: str = "") -> str:
    value = item.get(key)
    if value is None:
        return default
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    return value


def _score(item: dict, rank: int) -> float:
    value = item.get("score")
    if value is None or value == 0:
        return 1.0 / (rank + 1)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("score must be numeric")
    score = float(value)
    if not math.isfinite(score):
        raise ValueError("score must be finite")
    return score if score > 0.0 else 1.0 / (rank + 1)


def _contributions(
    engines: list[str],
    positions: list[int | None],
    subquery: str,
    score: float,
) -> tuple[DiscoveryContribution, ...]:
    contributions = []
    seen = set()
    for index, engine in enumerate(engines):
        if engine in seen:
            continue
        seen.add(engine)
        contributions.append(
            DiscoveryContribution(
                subquery=subquery,
                engine=engine,
                position=positions[index] if index < len(positions) else None,
                score=score,
            )
        )
    return tuple(contributions)


def _parse_results(payload: dict, subquery: str) -> list[DiscoveryResult]:
    if not isinstance(payload, dict):
        raise ValueError("response must be an object")
    raw_results = payload.get("results", [])
    if not isinstance(raw_results, list):
        raise ValueError("results must be a list")
    results = []
    for rank, item in enumerate(raw_results):
        if not isinstance(item, dict):
            raise ValueError("result must be an object")
        score = _score(item, rank)
        engines = _engines(item)
        positions = _positions(item)
        contributions = _contributions(engines, positions, subquery, score)
        url = _text(item, "url")
        published_at = item.get("publishedDate")
        if published_at is not None and not isinstance(published_at, str):
            raise ValueError("publishedDate must be a string")
        results.append(
            DiscoveryResult(
                title=_text(item, "title", url) or url,
                url=url,
                snippet=_text(item, "content"),
                engine=_text(item, "engine") or (engines[0] if engines else ""),
                score=score,
                published_at=published_at,
                contributions=contributions,
            )
        )
    return [result for result in results if result.url]


def _parse_failures(payload: dict) -> list[DiscoveryEngineFailure]:
    if not isinstance(payload, dict):
        raise ValueError("response must be an object")
    raw_failures = payload.get("unresponsive_engines", [])
    if not isinstance(raw_failures, list):
        raise ValueError("unresponsive_engines must be a list")
    failures = []
    for item in raw_failures:
        if not isinstance(item, (list, tuple)) or not item:
            continue
        engine = str(item[0]).strip()
        reason = str(item[1]).strip() if len(item) > 1 else "unresponsive"
        if engine:
            failures.append(DiscoveryEngineFailure(engine, reason))
    return failures


class SearxngDiscovery:
    def __init__(
        self,
        base_url: str,
        client: httpx.AsyncClient | None = None,
        api_key: str | None = None,
        timeout_s: float = 20.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._client = client or httpx.AsyncClient(
            timeout=timeout_s,
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
        except httpx.TimeoutException as exc:
            raise DiscoveryUnavailable("timeout") from exc
        except Exception as exc:
            raise DiscoveryUnavailable("transport_error") from exc
        if response.status_code != 200:
            raise DiscoveryUnavailable("upstream_status_error")
        try:
            payload = response.json()
            return DiscoveryOutcome(_parse_results(payload, subquery), _parse_failures(payload))
        except Exception as exc:
            raise DiscoveryUnavailable("malformed_response") from exc
