from orchestrator.crawl_policy import CrawlFrontier, CrawlPolicy
from orchestrator.models import MAX_SITE_URL_BYTES


def _policy(**overrides):
    values = {
        "effective_url": "https://docs.example.com/guide/start",
        "max_depth": 2,
        "max_discovered_urls": 10,
        "include_parent_paths": False,
        "include_subdomains": False,
        "include_paths": (),
        "exclude_paths": (),
        "query_policy": "preserve",
        "allowed_file_extensions": ("", ".html", ".pdf"),
    }
    values.update(overrides)
    return CrawlPolicy(**values)


def test_frontier_applies_scope_query_globs_files_safety_and_robots():
    frontier = CrawlFrontier(
        _policy(
            include_paths=("/guide/*",),
            exclude_paths=("*/private/*",),
        )
    )

    admitted = frontier.discover(
        "/guide/page?lang=en#section",
        source="link",
        depth=1,
        safe=True,
        robots_allowed=True,
    )
    outside = frontier.discover(
        "https://docs.example.com/reference/page",
        source="link",
        depth=1,
        safe=True,
        robots_allowed=True,
    )
    private = frontier.discover(
        "/guide/private/page",
        source="link",
        depth=1,
        safe=True,
        robots_allowed=True,
    )
    file_type = frontier.discover(
        "/guide/archive.zip",
        source="sitemap",
        depth=0,
        safe=True,
        robots_allowed=True,
    )
    unsafe = frontier.discover(
        "/guide/unsafe",
        source="link",
        depth=1,
        safe=False,
        robots_allowed=True,
    )
    denied = frontier.discover(
        "/guide/denied",
        source="link",
        depth=1,
        safe=True,
        robots_allowed=False,
    )

    assert admitted.url == "https://docs.example.com/guide/page?lang=en"
    assert admitted.states == ["discovered", "admitted", "queued"]
    assert outside.reason == "outside_seed_path"
    assert private.reason == "excluded_path"
    assert file_type.reason == "unsupported_file_type"
    assert unsafe.reason == "unsafe_target"
    assert denied.reason == "robots_refused"
    assert all(record.states[-1] == "filtered" for record in frontier.records[1:])


def test_frontier_preserves_repeated_queries_and_exclusion_takes_precedence():
    preserve = CrawlFrontier(
        _policy(
            include_paths=("/guide/*",),
            exclude_paths=("/guide/private/*",),
        )
    )
    exclude_query = CrawlFrontier(_policy(query_policy="exclude"))

    repeated = preserve.discover(
        "/guide/page?a=1&a=2&empty=&a=3#fragment",
        source="link",
        depth=1,
        safe=True,
        robots_allowed=True,
    )
    excluded_path = preserve.discover(
        "/guide/private/page",
        source="link",
        depth=1,
        safe=True,
        robots_allowed=True,
    )
    excluded_query = exclude_query.discover(
        "/guide/page?a=1&a=2",
        source="link",
        depth=1,
        safe=True,
        robots_allowed=True,
    )

    assert repeated.url == "https://docs.example.com/guide/page?a=1&a=2&empty=&a=3"
    assert excluded_path.reason == "excluded_path"
    assert excluded_query.reason == "query_excluded"


def test_frontier_omits_overlong_urls_and_filters_invalid_idna():
    frontier = CrawlFrontier(_policy())

    overlong = frontier.discover(
        "https://docs.example.com/guide/" + "x" * MAX_SITE_URL_BYTES,
        source="link",
        depth=1,
        safe=True,
        robots_allowed=True,
    )
    invalid_idna = frontier.discover(
        "https://exa_mple.com/guide/page",
        source="link",
        depth=1,
        safe=True,
        robots_allowed=True,
    )

    assert overlong is None
    assert frontier.omitted_due_to_limit == 1
    assert invalid_idna.reason == "invalid_url"
    assert invalid_idna.states[-1] == "filtered"


def test_frontier_merges_duplicate_sources_and_keeps_deterministic_queue_order():
    frontier = CrawlFrontier(_policy(query_policy="strip"))

    first = frontier.discover(
        "/guide/a?one=1",
        source="sitemap",
        depth=1,
        safe=True,
        robots_allowed=True,
        modified_at="2026-08-04",
    )
    duplicate = frontier.discover(
        "https://docs.example.com/guide/a?two=2#fragment",
        source="link",
        depth=2,
        safe=True,
        robots_allowed=True,
    )
    second = frontier.discover(
        "/guide/b",
        source="link",
        depth=1,
        safe=True,
        robots_allowed=True,
    )

    assert duplicate is first
    assert first.sources == ["sitemap", "link"]
    assert first.modified_at == "2026-08-04"
    assert frontier.pop().url == first.url
    assert frontier.pop().url == second.url
    assert frontier.pop() is None


def test_frontier_tracks_fetch_failure_cancellation_and_discovery_limit():
    frontier = CrawlFrontier(_policy(max_discovered_urls=2))
    first = frontier.discover(
        "/guide/a", source="seed", depth=0, safe=True, robots_allowed=True
    )
    second = frontier.discover(
        "/guide/b", source="link", depth=1, safe=True, robots_allowed=True
    )
    omitted = frontier.discover(
        "/guide/c", source="link", depth=1, safe=True, robots_allowed=True
    )

    frontier.mark_fetched(first)
    frontier.mark_failed(second, "upstream_timeout")
    frontier.cancel_queued()

    assert first.states[-1] == "fetched"
    assert second.states[-1] == "failed"
    assert second.reason == "upstream_timeout"
    assert omitted is None
    assert frontier.omitted_due_to_limit == 1


def test_parent_path_and_subdomain_are_explicit_opt_ins():
    strict = CrawlFrontier(_policy())
    subdomains_only = CrawlFrontier(_policy(include_subdomains=True))
    broad = CrawlFrontier(
        _policy(include_parent_paths=True, include_subdomains=True)
    )

    strict_parent = strict.discover(
        "https://docs.example.com/reference",
        source="link",
        depth=1,
        safe=True,
        robots_allowed=True,
    )
    strict_subdomain = strict.discover(
        "https://api.docs.example.com/guide/item",
        source="link",
        depth=1,
        safe=True,
        robots_allowed=True,
    )
    scoped_subdomain_parent = subdomains_only.discover(
        "https://api.docs.example.com/reference/item",
        source="link",
        depth=1,
        safe=True,
        robots_allowed=True,
    )
    broad_parent = broad.discover(
        "https://docs.example.com/reference",
        source="link",
        depth=1,
        safe=True,
        robots_allowed=True,
    )
    broad_subdomain = broad.discover(
        "https://api.docs.example.com/guide/item",
        source="link",
        depth=1,
        safe=True,
        robots_allowed=True,
    )
    broad_subdomain_parent = broad.discover(
        "https://api.docs.example.com/reference/item",
        source="link",
        depth=1,
        safe=True,
        robots_allowed=True,
    )

    assert strict_parent.reason == "outside_seed_path"
    assert strict_subdomain.reason == "outside_origin"
    assert scoped_subdomain_parent.reason == "outside_seed_path"
    assert broad_parent.states[-1] == "queued"
    assert broad_subdomain.states[-1] == "queued"
    assert broad_subdomain_parent.states[-1] == "queued"


def test_default_ports_are_same_origin_but_nondefault_ports_are_not():
    frontier = CrawlFrontier(_policy())

    explicit_default = frontier.discover(
        "https://docs.example.com:443/guide/default",
        source="link",
        depth=1,
        safe=True,
        robots_allowed=True,
    )
    nondefault = frontier.discover(
        "https://docs.example.com:444/guide/nondefault",
        source="link",
        depth=1,
        safe=True,
        robots_allowed=True,
    )

    assert explicit_default.states[-1] == "queued"
    assert nondefault.reason == "outside_origin"


def test_absolute_urls_remove_literal_and_encoded_dot_segments_before_policy_checks():
    policy = _policy()

    assert policy.normalize(
        "https://docs.example.com/guide/../private/secret"
    ) == "https://docs.example.com/private/secret"
    assert policy.normalize(
        "https://docs.example.com/guide/.%2E/private/secret"
    ) == "https://docs.example.com/private/secret"


def test_frontier_reconciles_redirect_scope_and_final_url_collisions():
    frontier = CrawlFrontier(_policy())
    target = frontier.discover(
        "/guide/target",
        source="sitemap",
        depth=1,
        safe=True,
        robots_allowed=True,
    )
    redirect = frontier.discover(
        "/guide/redirect",
        source="link",
        depth=1,
        safe=True,
        robots_allowed=True,
    )
    outside = frontier.discover(
        "/guide/outside-redirect",
        source="link",
        depth=1,
        safe=True,
        robots_allowed=True,
    )

    merged = frontier.reconcile_final(
        redirect,
        "https://docs.example.com/guide/target#fragment",
        safe=True,
        robots_allowed=True,
    )
    rejected = frontier.reconcile_final(
        outside,
        "https://other.example.com/guide/page",
        safe=True,
        robots_allowed=True,
    )

    assert merged is target
    assert target.states[-1] == "fetched"
    assert target.sources == ["sitemap", "link"]
    assert redirect.states[-1] == "failed"
    assert redirect.reason == "duplicate_final_url"
    assert rejected is None
    assert outside.states[-1] == "failed"
    assert outside.reason == "outside_origin"
