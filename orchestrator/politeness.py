import asyncio
import hashlib
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime


def parse_retry_after(
    value: str | None,
    *,
    now: datetime | None = None,
    max_delay_s: int,
) -> int | None:
    if not value:
        return None
    stripped = value.strip()
    if stripped.isascii() and stripped.isdigit():
        return min(int(stripped), max_delay_s)
    try:
        target = parsedate_to_datetime(stripped)
    except (TypeError, ValueError, OverflowError):
        return None
    if target.tzinfo is None:
        target = target.replace(tzinfo=UTC)
    current = now or datetime.now(UTC)
    delay = max(0, int((target - current).total_seconds()))
    return min(delay, max_delay_s)


class HostPoliteness:
    def __init__(
        self,
        *,
        default_delay_s: float,
        max_jitter_s: float,
        max_cooldown_s: int,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        self.default_delay_s = default_delay_s
        self.max_jitter_s = max_jitter_s
        self.max_cooldown_s = max_cooldown_s
        self._clock = clock
        self._sleep = sleep
        self._next_allowed: dict[str, float] = {}
        self._failures: dict[str, int] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def _jitter(self, host: str) -> float:
        if self.max_jitter_s <= 0:
            return 0
        value = int.from_bytes(hashlib.sha256(host.encode("utf-8")).digest()[:2], "big")
        return self.max_jitter_s * (value / 65535)

    def _evict_idle(self, retained_host: str) -> None:
        now = self._clock()
        for host, lock in list(self._locks.items()):
            if (
                host != retained_host
                and not lock.locked()
                and self._next_allowed.get(host, 0) <= now
            ):
                self._locks.pop(host, None)
                self._next_allowed.pop(host, None)
                self._failures.pop(host, None)

    async def wait(self, host: str, *, robots_delay_s: float | None) -> None:
        if host not in self._locks:
            self._evict_idle(host)
            self._locks[host] = asyncio.Lock()
        lock = self._locks[host]
        async with lock:
            while True:
                now = self._clock()
                delay = max(0.0, self._next_allowed.get(host, now) - now)
                if not delay:
                    break
                await self._sleep(delay)
            spacing = max(self.default_delay_s, robots_delay_s or 0) + self._jitter(host)
            self._next_allowed[host] = max(
                self._next_allowed.get(host, 0),
                self._clock() + spacing,
            )

    def record(
        self,
        host: str,
        *,
        status_code: int | None,
        retry_after_s: int | None,
    ) -> None:
        if status_code in {403, 429}:
            failures = self._failures.get(host, 0) + 1
            self._failures[host] = failures
            adaptive = min(self.max_cooldown_s, 2 ** min(failures, 10))
            cooldown = max(adaptive, retry_after_s or 0)
            self._next_allowed[host] = max(
                self._next_allowed.get(host, 0),
                self._clock() + cooldown,
            )
        elif status_code is not None and 200 <= status_code < 400:
            self._failures.pop(host, None)

    def cooldown_failures(self, host: str) -> int:
        return self._failures.get(host, 0)
