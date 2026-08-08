import asyncio
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Literal
from urllib.parse import urljoin, urlsplit
from weakref import WeakValueDictionary

UNRESERVED = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
)
ROBOTS_FAILURE_CACHE_TTL_S = 60.0


class _PreTextParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, _attrs) -> None:
        if tag.casefold() == "pre":
            self.depth += 1
        elif self.depth and tag.casefold() == "br":
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "pre" and self.depth:
            self.depth -= 1

    def handle_data(self, data: str) -> None:
        if self.depth:
            self.parts.append(data)


def robots_body(markdown: str | None, html: str | None, content_type: str | None) -> str:
    media_type = (content_type or "").split(";", 1)[0].strip().casefold()
    if media_type == "text/plain" and html:
        parser = _PreTextParser()
        parser.feed(html)
        body = "".join(parser.parts)
        if body:
            return body
    return html or markdown or ""


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


class _RobotsParser:
    def __init__(self, origin: str):
        self._origin = origin
        self._groups: list[_RobotsGroup] = []
        self._current: _RobotsGroup | None = None
        self._sitemaps: list[str] = []

    def parse(self, body: str, user_agent: str, fetched_at: float) -> RobotsSnapshot:
        for raw_line in body.splitlines():
            directive = self._directive(raw_line)
            if directive is not None:
                self._add(*directive)
        selected = self._selected_groups(user_agent)
        rules = tuple(rule for group in selected for rule in group.rules)
        delays = [delay for group in selected for delay in group.crawl_delays]
        return RobotsSnapshot(
            origin=self._origin,
            user_agent=user_agent,
            state="available",
            rules=rules,
            sitemaps=tuple(self._sitemaps),
            crawl_delay_s=max(delays) if delays else None,
            fetched_at=fetched_at,
        )

    @staticmethod
    def _directive(raw_line: str) -> tuple[str, str] | None:
        line = raw_line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            return None
        field, value = line.split(":", 1)
        return field.strip().casefold(), value.strip()

    def _add(self, field: str, value: str) -> None:
        if field == "sitemap" and value:
            self._add_sitemap(value)
            return
        if field == "user-agent" and value:
            self._add_agent(value)
            return
        self._add_group_directive(field, value)

    def _add_sitemap(self, value: str) -> None:
        sitemap = urljoin(f"{self._origin.rstrip('/')}/", value)
        if sitemap not in self._sitemaps:
            self._sitemaps.append(sitemap)

    def _add_agent(self, value: str) -> None:
        if self._current is None or self._current.rules or self._current.crawl_delays:
            self._current = _RobotsGroup([], [], [])
            self._groups.append(self._current)
        self._current.agents.append(value.casefold())

    def _add_group_directive(self, field: str, value: str) -> None:
        if self._current is None or not self._current.agents:
            return
        if field in {"allow", "disallow"}:
            if value:
                self._current.rules.append(RobotsRule(field == "allow", value))
            return
        if field == "crawl-delay":
            self._add_delay(value)

    def _add_delay(self, value: str) -> None:
        try:
            delay = float(value)
        except ValueError:
            return
        if 0 <= delay <= 86400 and self._current is not None:
            self._current.crawl_delays.append(delay)

    def _selected_groups(self, user_agent: str) -> list[_RobotsGroup]:
        token = user_agent.casefold()
        matching = [
            group
            for group in self._groups
            if any(agent != "*" and agent == token for agent in group.agents)
        ]
        if matching:
            return matching
        return [group for group in self._groups if "*" in group.agents]


def parse_robots(
    body: str,
    *,
    origin: str,
    user_agent: str,
    fetched_at: float,
) -> RobotsSnapshot:
    return _RobotsParser(origin).parse(body, user_agent, fetched_at)


class RobotsCache:
    def __init__(self, ttl_s: float):
        if not 0 < ttl_s <= 86400:
            raise ValueError("robots cache ttl must be between 0 and 86400 seconds")
        self.ttl_s = ttl_s
        self._values: dict[str, RobotsSnapshot] = {}
        self._locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()

    def get(self, origin: str, *, now: float) -> RobotsSnapshot | None:
        snapshot = self._values.get(origin)
        if snapshot is None:
            return None
        ttl_s = self.ttl_s if snapshot.state != "unreachable" else min(
            self.ttl_s,
            ROBOTS_FAILURE_CACHE_TTL_S,
        )
        if now >= snapshot.fetched_at + ttl_s:
            self._values.pop(origin, None)
            return None
        return snapshot

    def put(self, snapshot: RobotsSnapshot) -> None:
        self._values[snapshot.origin] = snapshot

    def lock_for(self, origin: str) -> asyncio.Lock:
        lock = self._locks.get(origin)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[origin] = lock
        return lock
