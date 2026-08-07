from dataclasses import dataclass
from typing import Literal
from urllib.parse import urljoin, urlsplit

UNRESERVED = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
)


def _normalize_octets(value: str) -> str:
    output: list[str] = []
    index = 0
    while index < len(value):
        character = value[index]
        if character == "%" and index + 2 < len(value):
            token = value[index + 1 : index + 3]
            try:
                byte = int(token, 16)
            except ValueError:
                output.append(character)
                index += 1
                continue
            decoded = chr(byte)
            output.append(decoded if decoded in UNRESERVED else f"%{byte:02X}")
            index += 3
            continue
        if ord(character) > 127:
            output.extend(f"%{byte:02X}" for byte in character.encode("utf-8"))
        else:
            output.append(character)
        index += 1
    return "".join(output)


@dataclass(frozen=True)
class RobotsRule:
    allow: bool
    pattern: str

    @property
    def specificity(self) -> int:
        value = _normalize_octets(self.pattern).removesuffix("$").replace("*", "")
        return len(value.encode("utf-8"))

    def matches(self, target: str) -> bool:
        pattern = _normalize_octets(self.pattern)
        anchored = pattern.endswith("$")
        if anchored:
            pattern = pattern[:-1]
        pattern_index = 0
        target_index = 0
        star_index = -1
        star_target = 0
        while target_index < len(target):
            if not anchored and pattern_index == len(pattern):
                return True
            if pattern_index < len(pattern) and pattern[pattern_index] == target[target_index]:
                pattern_index += 1
                target_index += 1
            elif pattern_index < len(pattern) and pattern[pattern_index] == "*":
                star_index = pattern_index
                star_target = target_index
                pattern_index += 1
            elif star_index >= 0:
                pattern_index = star_index + 1
                star_target += 1
                target_index = star_target
            else:
                return False
        while pattern_index < len(pattern) and pattern[pattern_index] == "*":
            pattern_index += 1
        return pattern_index == len(pattern)


@dataclass(frozen=True)
class RobotsSnapshot:
    origin: str
    user_agent: str
    state: Literal["available", "unavailable", "unreachable"]
    rules: tuple[RobotsRule, ...]
    sitemaps: tuple[str, ...]
    crawl_delay_s: float | None
    fetched_at: float
    reason: str | None = None

    def allows(self, url: str) -> bool:
        if self.state == "unavailable":
            return True
        if self.state == "unreachable":
            return False
        parsed = urlsplit(url)
        path_query = (parsed.path or "/") + (f"?{parsed.query}" if parsed.query else "")
        target = _normalize_octets(path_query)
        matches = [rule for rule in self.rules if rule.matches(target)]
        if not matches:
            return True
        longest = max(rule.specificity for rule in matches)
        return any(rule.allow for rule in matches if rule.specificity == longest)

    @classmethod
    def from_http(
        cls,
        *,
        origin: str,
        user_agent: str,
        status_code: int | None,
        body: str,
        fetched_at: float,
        max_bytes: int = 524288,
        cached: "RobotsSnapshot | None" = None,
    ) -> "RobotsSnapshot":
        if status_code is not None and 400 <= status_code <= 499:
            return cls(
                origin,
                user_agent,
                "unavailable",
                (),
                (),
                None,
                fetched_at,
                "http_4xx",
            )
        if status_code is None or status_code >= 500 or 300 <= status_code <= 399:
            if cached is not None:
                return cached
            return cls(
                origin,
                user_agent,
                "unreachable",
                (),
                (),
                None,
                fetched_at,
                "upstream_unreachable",
            )
        if len(body.encode("utf-8")) > max_bytes:
            return cls(
                origin,
                user_agent,
                "unreachable",
                (),
                (),
                None,
                fetched_at,
                "body_too_large",
            )
        return parse_robots(
            body,
            origin=origin,
            user_agent=user_agent,
            fetched_at=fetched_at,
        )


@dataclass
class _RobotsGroup:
    agents: list[str]
    rules: list[RobotsRule]
    crawl_delays: list[float]


def parse_robots(
    body: str,
    *,
    origin: str,
    user_agent: str,
    fetched_at: float,
) -> RobotsSnapshot:
    groups: list[_RobotsGroup] = []
    current: _RobotsGroup | None = None
    sitemaps: list[str] = []
    for raw_line in body.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        field, value = line.split(":", 1)
        field = field.strip().casefold()
        value = value.strip()
        if field == "sitemap" and value:
            sitemap = urljoin(f"{origin.rstrip('/')}/", value)
            if sitemap not in sitemaps:
                sitemaps.append(sitemap)
            continue
        if field == "user-agent" and value:
            if current is None or current.rules or current.crawl_delays:
                current = _RobotsGroup([], [], [])
                groups.append(current)
            current.agents.append(value.casefold())
            continue
        if current is None or not current.agents:
            continue
        if field in {"allow", "disallow"}:
            if not value:
                continue
            current.rules.append(RobotsRule(field == "allow", value))
        elif field == "crawl-delay":
            try:
                delay = float(value)
            except ValueError:
                continue
            if 0 <= delay <= 86400:
                current.crawl_delays.append(delay)
    token = user_agent.casefold()
    matching = [
        group
        for group in groups
        if any(agent != "*" and agent == token for agent in group.agents)
    ]
    if matching:
        selected = matching
    else:
        selected = [group for group in groups if "*" in group.agents]
    rules = tuple(rule for group in selected for rule in group.rules)
    delays = [delay for group in selected for delay in group.crawl_delays]
    return RobotsSnapshot(
        origin=origin,
        user_agent=user_agent,
        state="available",
        rules=rules,
        sitemaps=tuple(sitemaps),
        crawl_delay_s=max(delays) if delays else None,
        fetched_at=fetched_at,
    )


class RobotsCache:
    def __init__(self, ttl_s: float):
        if not 0 < ttl_s <= 86400:
            raise ValueError("robots cache ttl must be between 0 and 86400 seconds")
        self.ttl_s = ttl_s
        self._values: dict[str, RobotsSnapshot] = {}

    def get(self, origin: str, *, now: float) -> RobotsSnapshot | None:
        snapshot = self._values.get(origin)
        if snapshot is None or now >= snapshot.fetched_at + self.ttl_s:
            return None
        return snapshot

    def put(self, snapshot: RobotsSnapshot) -> None:
        if snapshot.state == "available":
            self._values[snapshot.origin] = snapshot
