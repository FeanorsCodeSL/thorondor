# Security Architecture

## 1. Threat Model Summary

Thorondor operates at the boundary between an agent's query and the open web. The primary threats specific to this architecture are:

| Threat | Description | Mitigation |
|---|---|---|
| **SSRF via crawl** | An attacker, page link, sitemap, robots declaration, or compromised search engine returns URLs pointing to internal network resources, cloud metadata endpoints, or localhost. | Safety checks at seed and candidate admission + Crawl4AI 0.9.2 connect-time DNS pinning and non-global-address rejection + changed-final-URL revalidation. |
| **Credential leakage in logs** | API keys, query text, raw page content, or URL query strings appear in structured logs and are shipped to a log aggregation system. | `redact_url()` strips userinfo and query strings; `query_hash()` hashes queries before logging; markdown and secrets are never logged. |
| **Prompt injection via crawled content** | Crawled page text contains adversarial instructions that reach an agent's context and alter its behavior. | All passage text is tagged `trust: "untrusted"` and `provenance: "external_web"` in the response envelope; agents must treat it accordingly. Thorondor does not interpret or execute crawled content. |
| **Query injection to SearXNG** | The user query is forwarded to SearXNG as a URL parameter. SearXNG handles escaping; Thorondor does not construct raw SQL or shell commands from the query. | SearXNG handles search-query sanitization. Thorondor passes the query as a URL parameter value only. |
| **Denial of service via large inputs** | Excessively large queries, fan-out, sitemaps, robots files, or pages with huge segment counts cause resource exhaustion. | Request and response byte limits; URL, depth, page, sitemap, robots, and document caps; route deadlines and process admission; chunker OOM guard and container limits. |

## 2. SSRF Protection

SSRF protection is implemented at two independent layers:

### Layer 1 — Orchestrator pre-crawl URL safety filter (`orchestrator/url_safety.py`)

Before any seed, search result, sitemap entry, robots declaration, or page link is sent to Crawl4AI, the orchestrator resolves it and validates the resolved IPs:

1. The URL scheme must be `http` or `https`.
2. If the hostname is an IP literal (including legacy octal/hex IPv4 forms), it is parsed directly.
3. Otherwise the hostname is resolved via `socket.getaddrinfo` (all address families) in a worker thread so DNS cannot block the event loop.
4. **Every** resolved IP is checked against the `UrlSafetyPolicy`:
   - Blocked IP categories: `loopback`, `link_local`, `private`, `reserved`, `multicast`, `unspecified` (configurable via `URL_SAFETY_BLOCKED_IP_CATEGORIES`).
   - Blocked special IPs: `169.254.169.254` (AWS EC2 metadata), `fd00:ec2::254` (IPv6 EC2 metadata) — configurable via `URL_SAFETY_BLOCKED_SPECIAL_IPS`.
5. IPv6 addresses are expanded to their embedded IPv4 equivalents for NAT64 (`64:ff9b::/96`), 6-to-4 (`2002::/16`), and IPv4-compatible (`::/96`) networks. Each candidate is re-checked.
6. If the host resolves to any unsafe IP, the URL is silently dropped from the candidate set.

The orchestrator does not issue a duplicate target-site preflight request. Crawl4AI validates and pins target connections and redirects, and the orchestrator applies the same policy to every differing final URL before the page crosses the internal fetch boundary. The pinned Docker `/crawl` contract does not expose a redirect-hop setting, so Thorondor bounds traversal with the route deadline rather than claiming an exact hop cap.

### Layer 2 — Crawl4AI connect-time DNS pinning

Crawl4AI 0.9.2 starts a localhost forward proxy and forces Chromium through it. External proxy variables are not used because the hardened server replaces caller proxy configuration with this internal proxy.

On every CONNECT request or plain HTTP target:

1. The target must use HTTP or HTTPS.
2. The proxy resolves the hostname once and rejects the destination if any resolved or transition-embedded address is not globally routable.
3. The proxy opens the connection to the selected pinned IP while preserving the original hostname for HTTP Host and TLS SNI.
4. Chromium never resolves the target independently, closing the validation-to-connect DNS-rebinding gap.
5. Redirect targets are subjected to the same policy before connection.

Crawl4AI's API is attached to `crawl-control`, an internal network shared only with the orchestrator. A separate `crawl-egress` network is selected explicitly as the default gateway, gives the pinning proxy outbound routing, and is not shared with another service. No Crawl4AI port is published. `CRAWL4AI_ALLOW_INTERNAL_URLS=false` keeps the upstream internal-target escape hatch disabled.

The first-party `ssrf-proxy` service remains packaged temporarily for deployment compatibility but is not in Crawl4AI 0.9.2's target-site path.

## 3. URL Safety Policy

The following variables configure the orchestrator-level URL safety policy:

| Variable | Default | Purpose |
|---|---|---|
| `URL_SAFETY_BLOCKED_IP_CATEGORIES` | `loopback,link_local,private,reserved,multicast,unspecified` | Python `ipaddress` attribute checks applied to every resolved IP. |
| `URL_SAFETY_BLOCKED_SPECIAL_IPS` | `169.254.169.254,fd00:ec2::254` | Specific IPs blocked by address equality, regardless of category. |
| `URL_SAFETY_NAT64_NETWORKS` | `64:ff9b::/96` | IPv6 networks where the low 32 bits are an embedded IPv4 address. |
| `URL_SAFETY_SIX_TO_FOUR_NETWORKS` | `2002::/16` | IPv6 6-to-4 networks where bits 17–48 are an embedded IPv4 address. |
| `URL_SAFETY_IPV4_COMPAT_NETWORKS` | `::/96` | IPv4-compatible IPv6 networks where the low 32 bits are an IPv4 address. |

The same variables with `PROXY_` prefix configure the retained first-party proxy independently. They do not alter Crawl4AI 0.9.2's built-in global-address policy.

## 4. Domain Allow/Block Lists

Three variables provide layered domain control on top of IP-level SSRF protection:

**`DOMAIN_BLOCKLIST`** — a comma-separated list of hostnames blocked before URL selection. Supports subdomain matching: blocking `example.com` also blocks `www.example.com`. Applied at selection time (before crawl), combined with per-call `exclude_domains`.

**`DOMAIN_ALLOWLIST`** — a comma-separated list of permitted hostnames. Used only when `ALLOWLIST_ONLY=true`.

**`ALLOWLIST_ONLY`** — when `true`, only URLs whose host is in `DOMAIN_ALLOWLIST` (or per-call `domains`) are considered. This is the most restrictive mode and is recommended for deployments where the crawl target set should be tightly controlled.

Per-call `domains` and `exclude_domains` fields allow callers to further constrain or expand the domain scope for a single request. When `ALLOWLIST_ONLY=true`, the per-call `domains` list is intersected with `DOMAIN_ALLOWLIST`; a caller cannot escape the operator allowlist.

## 5. Log Sanitization

The following data is explicitly excluded from all log output:

| Data | How excluded |
|---|---|
| Raw query text | Never logged; only a 16-hex-char SHA-256 prefix (`query_hash`) is emitted. |
| Page markdown | Never logged at any level in the orchestrator pipeline. |
| Chunk text | Never logged in the orchestrator. |
| Secrets and API keys | Never passed to the logger. The settings loader reads them from environment variables and stores them in the frozen `Settings` dataclass; they are never interpolated into log messages. |
| URL userinfo (username:password) | `redact_url()` strips userinfo from any URL before logging. |
| URL query strings | `redact_url()` removes the query string component from any URL logged by the orchestrator. |

The `JsonFormatter` in `orchestrator/observability.py` is the sole structured log formatter. It only copies explicitly listed fields from log records into the JSON payload; arbitrary `extra={}` fields not in the approved list are ignored.

## 6. API Key and Secret Management

**`SEARXNG_SECRET`** — the SearXNG shared secret. It must be set before SearXNG starts. The deploy script (`deploy.ps1`) generates a cryptographically random 32-byte base64 value using `System.Security.Cryptography.RandomNumberGenerator` when the field is blank. Rotate by blanking the value in `.env` and re-running the deploy script, which will regenerate it and restart SearXNG.

**Per-seam `*_API_KEY` variables** — `SEARXNG_API_KEY`, `CRAWL4AI_API_KEY`, `CHUNKER_API_KEY`, `RERANKER_API_KEY`, `EMBEDDING_API_KEY`, and `LLM_API_KEY` are optional bearer tokens forwarded in `Authorization: Bearer <key>` headers to the respective seams. Blank means no authentication header is sent. Set these when the corresponding service is accessible beyond the internal Compose network.

**What happens with a blank API key** — `_configured_optional()` in the settings loader returns `None` for blank values. Clients check `if self.api_key` before adding the `Authorization` header; no header is sent for `None` or empty string.

**Secret rotation** — rotate any `*_API_KEY` by updating `.env`, then restarting only the orchestrator container (`docker compose restart orchestrator`). The orchestrator reads all settings at startup; no code change is needed.

## 7. Network Isolation

The orchestrator, chunker, SearXNG, retained egress proxy, and bundled model containers attach to the general `internal` network, which has no default route. Crawl4AI is intentionally excluded from that shared network.

Services with provider-plane routing attach to `egress`:
- `egress-proxy` — retained for deployment compatibility.
- `searxng` — SearXNG must reach upstream search engines directly.

Crawl4AI attaches to exactly two dedicated networks: internal `crawl-control`, shared only with the orchestrator, and outbound `crawl-egress`, shared with no other service and selected as the default gateway. The release guard rejects the internal-target escape hatch, external proxy variables, shared or missing networks, an ambiguous default route, a published API port, missing container hardening, and a non-digest image.

The orchestrator's port `ORCHESTRATOR_PORT` is the only published port; all other service ports are internal-only. Compose binds that published port to `ORCHESTRATOR_HOST`, which is `127.0.0.1` in `.env.example` for local-only access by default.

## 8. Operator Responsibilities

Thorondor provides SSRF protection, log sanitization, and network isolation. The following are operator responsibilities:

- **TLS termination** — the orchestrator does not serve HTTPS. A reverse proxy with a valid TLS certificate must be placed in front.
- **Access control** — there is no authentication on `POST /v1/search`, `POST /search`, `POST /v1/fetch`, `POST /v1/map`, `POST /v1/crawl`, or the MCP endpoint. Restrict access at the network or reverse-proxy layer.
- **Host binding and firewall** — keep `ORCHESTRATOR_HOST=127.0.0.1` for personal/local deployments. Use `ORCHESTRATOR_HOST=0.0.0.0` only behind firewall, TLS, authentication, and rate limiting.
- **Secret hygiene** — `.env` contains sensitive values. Do not commit it to version control. Inject secrets from a secrets manager at deploy time.
- **`ALLOWLIST_ONLY=false` responsibility** — with the default setting, the service will crawl any URL that passes the IP safety filter. Set `ALLOWLIST_ONLY=true` and populate `DOMAIN_ALLOWLIST` in high-risk environments.
- **robots.txt compliance** — `CRAWL_RESPECT_ROBOTS_TXT=true` by default for Crawl4AI page requests. Map and crawl additionally enforce Thorondor's RFC 9309 policy with the configured stable identity and do not expose a per-request bypass. The operator remains responsible for applicable terms and law.
- **Page-cache privacy and retention** — leave `PAGE_CACHE_ENABLED=false` unless persistent copies of fetched URLs, query strings, cleaned content, metadata, and links are acceptable. Keep `PAGE_CACHE_RAW_HTML_ENABLED=false` unless raw DOM retention is explicitly required. Protect and exclude the `thorondor-page-cache` volume from broad backups when its content is not meant to be retained, choose the shortest useful absolute retention, and use the local scoped-clear command for sensitive URLs. Runtime cleanup removes untouched records at their retention deadline, and reads plus `304` responses never extend it. Disabling the feature stops new writes but does not erase an existing volume.

## 9. Known Limitations

- **No authentication on orchestrator endpoints** — search, fetch, map, crawl, and MCP accept any request without authentication. This is by design for development convenience; operators must add auth at the reverse-proxy layer.
- **No per-client quota** — process-wide admission rejects excess search, fetch, map, or crawl work with bounded HTTP 429 responses, but it does not identify callers or allocate fair per-client quotas. Operators exposing the service to multiple clients must add authenticated rate limiting at the reverse proxy.
- **Crawled content is untrusted but not sandboxed** — page text is tagged `trust: "untrusted"` in the response, but it is not executed, sandboxed at the OS level, or scanned for malicious patterns. Prompt injection via crawled content is a risk that agents consuming the passages must mitigate.
- **SearXNG has no per-request auth by default** — `SEARXNG_API_KEY` is optional. Without it, the SearXNG `/search` endpoint is accessible to any service on the internal Docker network.
- **Pinned egress enforcement depends on Compose topology** — custom deployments must preserve Crawl4AI's isolated control network, dedicated egress network, disabled internal-target escape hatch, and absence of external proxy overrides. The release guard checks the first-party local, CLI, and production Compose configurations.
- **Target watches are invocation-time checks, not schedules** — selectors are bounded and declarative, no caller JavaScript or regular expression runs, and missing or ambiguous targets fail closed. The caller remains responsible for schedule frequency and actions after `condition_met=true`.
