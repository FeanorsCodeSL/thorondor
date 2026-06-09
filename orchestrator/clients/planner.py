"""Query planner clients."""
import json

import httpx


class IdentityPlanner:
    async def plan(self, query: str) -> list[str]:
        return [query]


class LlmPlanner:
    def __init__(self, endpoint: str, model: str):
        self.endpoint = endpoint.rstrip("/")
        self.model = model

    async def plan(self, query: str) -> list[str]:
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(
                    f"{self.endpoint}/v1/chat/completions",
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
                return planned or [query]
        except Exception:
            return [query]
        return [query]
