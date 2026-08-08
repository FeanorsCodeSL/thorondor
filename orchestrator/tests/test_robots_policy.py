from orchestrator.robots_policy import RobotsCache, RobotsSnapshot, parse_robots, robots_body


def test_robots_merges_matching_groups_and_uses_longest_allow_tie():
    body = """
User-agent: ThorondorBot
Disallow: /private/
Allow: /private/public$
Crawl-delay: 2

User-agent: thorondorbot
Disallow: /private/public
Allow: /private/public$
Sitemap: /sitemap.xml

User-agent: *
Disallow: /
"""

    snapshot = parse_robots(
        body,
        origin="https://example.com",
        user_agent="ThorondorBot",
        fetched_at=10,
    )

    assert snapshot.state == "available"
    assert snapshot.allows("https://example.com/private/public") is True
    assert snapshot.allows("https://example.com/private/other") is False
    assert snapshot.allows("https://example.com/open") is True
    assert snapshot.crawl_delay_s == 2
    assert snapshot.sitemaps == ("https://example.com/sitemap.xml",)


def test_robots_does_not_match_partial_user_agent_tokens():
    snapshot = parse_robots(
        """
User-agent: bot
Allow: /

User-agent: *
Disallow: /
""",
        origin="https://example.com",
        user_agent="ThorondorBot",
        fetched_at=0,
    )

    assert snapshot.allows("https://example.com/private") is False


def test_robots_supports_wildcards_end_anchor_and_percent_equivalence():
    snapshot = parse_robots(
        """
User-agent: *
Disallow: /*?session=*
Disallow: /caf%C3%A9$
Allow: /caf%C3%A9/menu
""",
        origin="https://example.com",
        user_agent="ThorondorBot",
        fetched_at=0,
    )

    assert snapshot.allows("https://example.com/news?session=abc") is False
    assert snapshot.allows("https://example.com/caf%C3%A9") is False
    assert snapshot.allows("https://example.com/café") is False
    assert snapshot.allows("https://example.com/caf%C3%A9/menu") is True


def test_robots_recovers_parseable_rules_and_ignores_empty_disallow():
    snapshot = parse_robots(
        """
not a field
User-agent: ThorondorBot
Disallow:
Disallow: /blocked
broken
Allow: /blocked/safe
""",
        origin="https://example.com",
        user_agent="ThorondorBot",
        fetched_at=0,
    )

    assert snapshot.allows("https://example.com/anything") is True
    assert snapshot.allows("https://example.com/blocked") is False
    assert snapshot.allows("https://example.com/blocked/safe") is True


def test_robots_http_states_fail_open_for_4xx_and_closed_for_5xx():
    unavailable = RobotsSnapshot.from_http(
        origin="https://example.com",
        user_agent="ThorondorBot",
        status_code=404,
        body="",
        fetched_at=0,
    )
    unreachable = RobotsSnapshot.from_http(
        origin="https://example.com",
        user_agent="ThorondorBot",
        status_code=503,
        body="",
        fetched_at=0,
    )

    assert unavailable.state == "unavailable"
    assert unavailable.allows("https://example.com/a") is True
    assert unreachable.state == "unreachable"
    assert unreachable.allows("https://example.com/a") is False


def test_robots_redirect_or_timeout_requires_a_valid_cached_snapshot():
    cached = parse_robots(
        "User-agent: *\nDisallow: /blocked",
        origin="https://example.com",
        user_agent="ThorondorBot",
        fetched_at=100,
    )

    redirect_without_cache = RobotsSnapshot.from_http(
        origin="https://example.com",
        user_agent="ThorondorBot",
        status_code=302,
        body="",
        fetched_at=200,
    )
    timeout_without_cache = RobotsSnapshot.from_http(
        origin="https://example.com",
        user_agent="ThorondorBot",
        status_code=None,
        body="",
        fetched_at=200,
    )
    redirect_with_cache = RobotsSnapshot.from_http(
        origin="https://example.com",
        user_agent="ThorondorBot",
        status_code=307,
        body="",
        fetched_at=200,
        cached=cached,
    )

    assert redirect_without_cache.state == "unreachable"
    assert timeout_without_cache.state == "unreachable"
    assert redirect_with_cache is cached


def test_robots_cache_is_absolute_and_supplies_valid_copy_on_failure():
    cache = RobotsCache(ttl_s=86400)
    valid = parse_robots(
        "User-agent: *\nDisallow: /blocked",
        origin="https://example.com",
        user_agent="ThorondorBot",
        fetched_at=100,
    )
    cache.put(valid)

    assert cache.get("https://example.com", now=86499) is valid
    assert cache.get("https://example.com", now=86501) is None
    fallback = RobotsSnapshot.from_http(
        origin="https://example.com",
        user_agent="ThorondorBot",
        status_code=503,
        body="",
        fetched_at=200,
        cached=valid,
    )
    assert fallback is valid


def test_robots_body_prefers_unescaped_plain_text_html():
    body = robots_body(
        "User-agent: \\*\nDisallow: /private",
        "<html><body><pre>User-agent: *\nDisallow: /private</pre></body></html>",
        "text/plain; charset=utf-8",
    )

    assert body == "User-agent: *\nDisallow: /private"


def test_robots_cache_bounds_unavailable_and_unreachable_results():
    cache = RobotsCache(ttl_s=86400)
    unavailable = RobotsSnapshot.from_http(
        origin="https://missing.test",
        user_agent="ThorondorBot",
        status_code=404,
        body="",
        fetched_at=100,
    )
    unreachable = RobotsSnapshot.from_http(
        origin="https://down.test",
        user_agent="ThorondorBot",
        status_code=503,
        body="",
        fetched_at=100,
    )
    cache.put(unavailable)
    cache.put(unreachable)

    assert cache.get("https://missing.test", now=200) is unavailable
    assert cache.get("https://down.test", now=159) is unreachable
    assert cache.get("https://down.test", now=160) is None


def test_robots_rejects_oversized_body():
    snapshot = RobotsSnapshot.from_http(
        origin="https://example.com",
        user_agent="ThorondorBot",
        status_code=200,
        body="User-agent: *\nDisallow: /" + "x" * 20,
        fetched_at=0,
        max_bytes=16,
    )

    assert snapshot.state == "unreachable"
    assert snapshot.reason == "body_too_large"
