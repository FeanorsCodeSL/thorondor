"""Query planner clients."""
import json

import httpx

MAX_PLANNED_SUBQUERIES = 3


class IdentityPlanner:
    async def plan(self, query: str) -> list[str]:
        return [query]


class LlmPlanner:
    def __init__(self, endpoint: str, model: str, client: httpx.AsyncClient | None = None, api_key: str | None = None):
        self.endpoint = endpoint.rstrip("/")
        self.model = model
        self.api_key = api_key
        self._client = client or httpx.AsyncClient(
            timeout=15.0,
            limits=httpx.Limits(max_connections=5, max_keepalive_connections=5),
        )
        self._owns_client = client is None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def plan(self, query: str) -> list[str]:
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else None
        try:
            response = await self._client.post(
                f"{self.endpoint}/v1/chat/completions",
                headers=headers,
                json={
                    "model": self.model,
                    "messages": [
                        {
                            "role": "system",
                            "content": "Return 1-3 search subqueries as a JSON array of strings.",
                        },
                        {"role": "user", "content": query},
                    ],
                },
            )
            if response.status_code != 200:
                return [query]
            content = response.json()["choices"][0]["message"]["content"]
            parsed = json.loads(content)
            if isinstance(parsed, list):
                planned = [str(item).strip() for item in parsed if str(item).strip()]
                return planned[:MAX_PLANNED_SUBQUERIES] or [query]
        except Exception:
            return [query]
        return [query]
