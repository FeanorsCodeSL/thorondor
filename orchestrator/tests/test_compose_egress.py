from scripts.check_compose_egress import validate


def _config() -> dict:
    proxy_url = "http://egress-proxy:8888"
    return {
        "networks": {
            "internal": {"internal": True},
            "egress": {},
        },
        "services": {
            "crawl4ai": {
                "networks": ["internal"],
                "environment": {
                    "HTTP_PROXY": proxy_url,
                    "HTTPS_PROXY": proxy_url,
                    "ALL_PROXY": proxy_url,
                    "http_proxy": proxy_url,
                    "https_proxy": proxy_url,
                    "all_proxy": proxy_url,
                    "NO_PROXY": "",
                    "no_proxy": "",
                },
            },
            "egress-proxy": {"networks": ["internal", "egress"]},
            "searxng": {"networks": ["internal", "egress"]},
        },
    }


def test_compose_egress_contract_accepts_proxy_only_crawler_networking():
    assert validate(_config(), "crawl4ai", "egress-proxy", ["searxng"]) == []


def test_compose_egress_contract_checks_lowercase_proxy_variables():
    config = _config()
    config["services"]["crawl4ai"]["environment"]["http_proxy"] = "http://other:8888"

    assert validate(config, "crawl4ai", "egress-proxy", ["searxng"]) == [
        "crawl4ai http_proxy must target egress-proxy"
    ]


def test_compose_egress_contract_rejects_non_loopback_proxy_bypass():
    config = _config()
    config["services"]["crawl4ai"]["environment"]["NO_PROXY"] = "*"
    config["services"]["crawl4ai"]["environment"]["no_proxy"] = "*"

    assert validate(config, "crawl4ai", "egress-proxy", ["searxng"]) == [
        "crawl4ai NO_PROXY may contain only loopback targets"
    ]
