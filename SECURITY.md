# Security Policy

Thorondor is intended for self-hosted use. The default Compose configuration binds the orchestrator to `127.0.0.1`; do not expose it to an untrusted network without a reverse proxy that provides TLS, authentication, authorization, and rate limiting.

## Supported Versions

Security fixes target the default branch until tagged releases exist.

## Reporting a Vulnerability

Open a private security advisory in the hosting platform if available. If not, contact the maintainer privately before opening a public issue. Include:

- affected endpoint, script, or container
- reproduction steps
- expected impact
- whether the issue requires non-default configuration

Do not include live secrets, private URLs, or third-party data in reports.

## Known Security Boundaries

- REST `/v1/search`, compatibility `/search`, `/v1/fetch`, `/v1/map`, `/v1/crawl`, crawl jobs, and MCP `/mcp` do not implement built-in authentication.
- There is no built-in per-client rate limiting.
- Crawled content and structured extraction values are returned as untrusted external data; downstream agents must treat them as prompt-injection-prone. JSON Schema extraction uses a fixed system instruction, a bounded local schema subset, independent validation, and exact value-bearing evidence checks, but it does not make page content trusted.
- Crawl egress is protected by orchestrator URL safety checks and Crawl4AI 0.9.2's connect-time DNS-pinning proxy on its dedicated outbound network. The retained first-party SSRF proxy is not in Crawl4AI's target-site path. Operators remain responsible for firewalling internal networks and restricting allowed domains where needed.
- The optional page cache and durable crawl-job store persist fetched content and request metadata only when explicitly enabled. Protect the `/var/lib/thorondor` volume, use the documented retention settings, and keep raw HTML persistence disabled unless it is required.
