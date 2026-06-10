# Security Architecture

## 1. Threat Model Summary

Thorondor operates at the boundary between an agent's query and the open web. The primary threats specific to this architecture are:

| Threat | Description | Mitigation |
|---|---|---|
| **SSRF via crawl** | An attacker or a compromised upstream search engine returns URLs pointing to internal network resources, cloud metadata endpoints, or localhost. Crawl4AI fetches them, leaking data or enabling internal service access. | Pre-crawl URL safety filter (orchestrator) + SSRF egress proxy (Crawl4AI traffic) + redirect validation. |
| **Credential leakage in logs** | API keys, query text, raw page content, or URL query strings appear in structured logs and are shipped to a log aggregation system. | `redact_url()` strips userinfo and query strings; `query_hash()` hashes queries before logging; markdown and secrets are never logged. |
| **Prompt injection via crawled content** | Crawled page text contains adversarial instructions that reach an agent's context and alter its behavior. | All passage text is tagged `trust: "untrusted"` and `provenance: "external_web"` in the response envelope; agents must treat it accordingly. Thorondor does not interpret or execute crawled content. |
| **Query injection to SearXNG** | The user query is forwarded to SearXNG as a URL parameter. SearXNG handles escaping; Thorondor does not construct raw SQL or shell commands from the query. | SearXNG handles search-query sanitization. Thorondor passes the query as a URL parameter value only. |
| **Denial of service via large inputs** | Excessively large queries, high `max_urls`, or pages with huge segment counts cause resource exhaustion. | `MAX_QUERY_CHARS=500` input cap; `MAX_SELECTED_URLS=20` URL cap; `CHUNKER_MAX_SEGMENTS_DP` OOM guard; `CHUNKER_MEM_LIMIT` container memory limit; `CRAWL_CONCURRENCY` and `CRAWL_TIMEOUT_S` bound crawl parallelism. |

## 2. SSRF Protection

SSRF protection is implemented at two independent layers:

### Layer 1 — Orchestrator pre-crawl URL safety filter (`orchestrator/url_safety.py`)

Before any URL is sent to Crawl4AI, the orchestrator resolves it and validates the resolved IPs:

1. The URL scheme must be `http` or `https`.
2. If the hostname is an IP literal (including legacy octal/hex IPv4 forms), it is parsed directly.
3. Otherwise the hostname is resolved via `socket.getaddrinfo` (all address families).
4. **Every** resolved IP is checked against the `UrlSafetyPolicy`:
   - Blocked IP categories: `loopback`, `link_local`, `private`, `reserved`, `multicast`, `unspecified` (configurable via `URL_SAFETY_BLOCKED_IP_CATEGORIES`).
   - Blocked special IPs: `169.254.169.254` (AWS EC2 metadata), `fd00:ec2::254` (IPv6 EC2 metadata) — configurable via `URL_SAFETY_BLOCKED_SPECIAL_IPS`.
5. IPv6 addresses are expanded to their embedded IPv4 equivalents for NAT64 (`64:ff9b::/96`), 6-to-4 (`2002::/16`), and IPv4-compatible (`::/96`) networks. Each candidate is re-checked.
6. If the host resolves to any unsafe IP, the URL is silently dropped from the candidate set.

The filter also applies during redirect validation: when `CRAWL_VALIDATE_REDIRECTS=true`, the orchestrator follows redirect chains up to `CRAWL_MAX_PREFLIGHT_REDIRECTS` hops using HEAD requests, re-validating the target URL at each hop.

### Layer 2 — SSRF Egress Proxy (`ssrf-proxy/proxy.py`)

A minimal Python asyncio HTTP CONNECT proxy that sits between Crawl4AI and the internet. Crawl4AI's proxy environment variables (`CRAWL4AI_HTTP_PROXY`, `CRAWL4AI_HTTPS_PROXY`, `CRAWL4AI_ALL_PROXY`) route all outbound traffic through it.

On every CONNECT request (and plain HTTP forwarding):

1. The target hostname is normalised (strip brackets, IDNA-encode).
2. If it is an IP literal, it is checked immediately.
3. Otherwise the hostname is resolved asynchronously via `asyncio.to_thread(socket.getaddrinfo)`.
4. The same IP expansion logic (NAT64, 6-to-4, IPv4-compat) and blocked category/special-IP checks apply.
5. If any resolved IP is unsafe, the proxy returns `403 Forbidden` and logs the blocked target.
6. If the host is safe, the proxy opens a TCP connection to the resolved IP and establishes a bidirectional relay.

The proxy lives on the internal Docker network (`internal`) and has a second attachment to the `egress` network. Crawl4AI communicates with it over the internal network; the proxy reaches the internet over the egress network.

## 3. URL Safety Policy

The following variables configure the orchestrator-level URL safety policy:

| Variable | Default | Purpose |
|---|---|---|
| `URL_SAFETY_BLOCKED_IP_CATEGORIES` | `loopback,link_local,private,reserved,multicast,unspecified` | Python `ipaddress` attribute checks applied to every resolved IP. |
| `URL_SAFETY_BLOCKED_SPECIAL_IPS` | `169.254.169.254,fd00:ec2::254` | Specific IPs blocked by address equality, regardless of category. |
| `URL_SAFETY_NAT64_NETWORKS` | `64:ff9b::/96` | IPv6 networks where the low 32 bits are an embedded IPv4 address. |
| `URL_SAFETY_SIX_TO_FOUR_NETWORKS` | `2002::/16` | IPv6 6-to-4 networks where bits 17–48 are an embedded IPv4 address. |
| `URL_SAFETY_IPV4_COMPAT_NETWORKS` | `::/96` | IPv4-compatible IPv6 networks where the low 32 bits are an IPv4 address. |

The same variables with `PROXY_` prefix configure the egress proxy layer independently, so both layers can be tuned separately.

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

All five core services and both model containers attach to the `internal` Docker network, which is declared `internal: true` — it has no default route to the external network. Services cannot initiate outbound connections from this network.

Two services additionally attach to the `egress` network (which has a default route):
- `egress-proxy` — the SSRF proxy, which enforces the IP blocklist before forwarding.
- `searxng` — SearXNG must reach upstream search engines directly.
- `crawl4ai` — Crawl4AI must reach the web, but all its outbound traffic is forced through the SSRF proxy via the `CRAWL4AI_HTTP_PROXY` / `CRAWL4AI_HTTPS_PROXY` environment variables.
- `orchestrator` — the orchestrator attaches to `egress` for healthcheck probes to external services if configured, but in the default Compose setup all its operational traffic is internal.

The orchestrator's port `ORCHESTRATOR_PORT` is the only published port; all other service ports are internal-only.

## 8. Operator Responsibilities

Thorondor provides SSRF protection, log sanitization, and network isolation. The following are operator responsibilities:

- **TLS termination** — the orchestrator does not serve HTTPS. A reverse proxy with a valid TLS certificate must be placed in front.
- **Access control** — there is no authentication on `POST /v1/search`, `POST /search`, or the MCP endpoint. Restrict access at the network or reverse-proxy layer.
- **Host firewall** — ensure `ORCHESTRATOR_PORT` is not exposed to untrusted networks.
- **Secret hygiene** — `.env` contains sensitive values. Do not commit it to version control. Inject secrets from a secrets manager at deploy time.
- **`ALLOWLIST_ONLY=false` responsibility** — with the default setting, the service will crawl any URL that passes the IP safety filter. Set `ALLOWLIST_ONLY=true` and populate `DOMAIN_ALLOWLIST` in high-risk environments.
- **robots.txt compliance** — `CRAWL_RESPECT_ROBOTS_TXT=true` by default. Changing this to `false` may violate the terms of service of crawled sites. The operator is responsible for compliance with applicable ToS and legal requirements.

## 9. Known Limitations

- **No authentication on orchestrator endpoints** — `POST /v1/search` and the MCP endpoint accept any request without authentication. This is by design for development convenience; operators must add auth at the reverse-proxy layer.
- **No rate limiting** — the service does not enforce per-client rate limits. A high-volume client can exhaust SearXNG or Crawl4AI capacity.
- **Crawled content is untrusted but not sandboxed** — page text is tagged `trust: "untrusted"` in the response, but it is not executed, sandboxed at the OS level, or scanned for malicious patterns. Prompt injection via crawled content is a risk that agents consuming the passages must mitigate.
- **SearXNG has no per-request auth by default** — `SEARXNG_API_KEY` is optional. Without it, the SearXNG `/search` endpoint is accessible to any service on the internal Docker network.
- **Single-layer redirect validation** — the orchestrator pre-validates redirects via HEAD requests, but Crawl4AI may follow additional redirects internally after the preflight. The SSRF proxy provides the second line of defence for these cases.
