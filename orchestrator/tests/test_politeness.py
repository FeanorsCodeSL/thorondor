from datetime import UTC, datetime

from orchestrator.politeness import HostPoliteness, parse_retry_after


def test_retry_after_accepts_delta_seconds_and_http_date_with_bounds():
    now = datetime(2026, 8, 7, 10, 0, tzinfo=UTC)

    assert parse_retry_after("12", now=now, max_delay_s=60) == 12
    assert parse_retry_after("120", now=now, max_delay_s=60) == 60
    assert (
        parse_retry_after(
            "Fri, 07 Aug 2026 10:00:30 GMT",
            now=now,
            max_delay_s=60,
        )
        == 30
    )
    assert parse_retry_after("invalid", now=now, max_delay_s=60) is None
    assert parse_retry_after("\u00b2", now=now, max_delay_s=60) is None


def test_host_politeness_applies_delay_jitter_and_adaptive_cooldown():
    clock = [0.0]
    sleeps = []

    async def sleep(delay):
        sleeps.append(delay)
        clock[0] += delay

    policy = HostPoliteness(
        default_delay_s=1.0,
        max_jitter_s=0.2,
        max_cooldown_s=60,
        clock=lambda: clock[0],
        sleep=sleep,
    )

    async def exercise():
        await policy.wait("example.com", robots_delay_s=2.0)
        policy.record("example.com", status_code=429, retry_after_s=10)
        await policy.wait("example.com", robots_delay_s=2.0)
        policy.record("example.com", status_code=403, retry_after_s=None)
        await policy.wait("example.com", robots_delay_s=2.0)
        policy.record("example.com", status_code=200, retry_after_s=None)

    import anyio

    anyio.run(exercise)

    assert sleeps[0] >= 10
    assert sleeps[1] >= 2
    assert policy.cooldown_failures("example.com") == 0


def test_host_politeness_does_not_shorten_cooldown_added_during_wait():
    clock = [0.0]
    sleeps = []
    policy = None

    async def sleep(delay):
        sleeps.append(delay)
        if len(sleeps) == 1:
            policy.record("example.com", status_code=429, retry_after_s=10)
        clock[0] += delay

    policy = HostPoliteness(
        default_delay_s=2,
        max_jitter_s=0,
        max_cooldown_s=60,
        clock=lambda: clock[0],
        sleep=sleep,
    )

    async def exercise():
        await policy.wait("example.com", robots_delay_s=None)
        await policy.wait("example.com", robots_delay_s=None)

    import anyio

    anyio.run(exercise)

    assert sum(sleeps) >= 10
    assert len(sleeps) == 2


def test_host_politeness_evicts_idle_host_state():
    clock = [0.0]
    policy = HostPoliteness(
        default_delay_s=1,
        max_jitter_s=0,
        max_cooldown_s=60,
        clock=lambda: clock[0],
        sleep=lambda _delay: None,
    )

    async def exercise():
        await policy.wait("first.example", robots_delay_s=None)
        policy.record("first.example", status_code=200, retry_after_s=None)
        clock[0] = 2
        await policy.wait("second.example", robots_delay_s=None)

    import anyio

    anyio.run(exercise)

    assert "first.example" not in policy._locks
    assert "first.example" not in policy._next_allowed
