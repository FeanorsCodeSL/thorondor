# URL Identity and Outcome Contracts

## Scope and current integration

This contract is used internally, exposed through additive fields on the existing REST and MCP `thorondor.search.v1` response, and returned directly by the bounded REST/MCP `thorondor.fetch.v1` contract. It adds no cache implementation or ingress enforcement.

Document and exact-evidence identities are carried through passages, citations, REST, and MCP. URL and cache identities are not yet wired into persistence. The internal `Page` boundary and Crawl4AI client capture requested and final URLs, status, content type, allowlisted validators, links, and bounded metadata. The known-URL fetch surface exposes these values through typed, bounded per-URL outcomes; future map, crawl, cache, and job work must reuse the same identities and codes.

## URL roles

`UrlIdentity` keeps URL roles separate instead of applying one normalizer everywhere.

| Field | Definition | Uses | Explicit non-use |
|---|---|---|---|
| `requested_url` | Exact URL submitted for a fetch. | Request provenance and redirect-chain reporting. | It is not replaced by a declared canonical URL. |
| `final_url` | Fragment-free terminal URL reported by the fetch path. | Transport/safety identity and source-document identity. | It is not a display URL or cache key by itself. |
| `display_url` | Exact final string passed to the builder, including an optional fragment. | Caller-facing display and provenance. | It is not used as a safety, deduplication, or cache key. |
| `safety_target` | Derived fragment-free `final_url`. | URL safety validation before target-site dispatch. | A declared canonical URL never supplies this value. |
| `dedup_key` | Derived conservative serialization of `final_url`. | Discovery and page-identity comparisons. | It never uses a declared canonical URL. |
| `declared_canonical_url` | Untrusted page declaration retained as advisory provenance. | Metadata and later human/auditable analysis. | It is never auto-followed, never a safety target, and never changes a dedup key. |

The model derives `safety_target` and `dedup_key`; callers cannot provide conflicting values. It rejects a direct `final_url` with a fragment or a `display_url` that does not reduce to that final URL after fragment removal.

## Dedup serializer

`dedup_key` uses the versioned `thorondor.url-dedup.v1` serializer in `orchestrator/url_identity.py`. It does not call `normalize_url`; the legacy normalizer remains unchanged for its current discovery and citation callers.

The serializer removes the fragment, lowercases the scheme, canonicalizes IP literals, and canonicalizes DNS hosts with the existing runtime `idna` encoder using IDNA2008 plus UTS-46 processing with `transitional=False`. It deliberately does not use Python's legacy RFC 3490 codec: `faß.de` serializes as `xn--fa-hia.de` and remains distinct from `fass.de`. An encoder rejection remains a rejection; the serializer does not fall back to a potentially lossy host spelling. It retains `www` and apex hosts as distinct, and elides a port only for `http:80` or `https:443`. IPv6 literals remain bracketed in the authority and non-default ports remain present. Path parameters and trailing slashes are retained.

It examines each raw `&`-separated query component. The form-decoded name before the first `=` is compared case-insensitively with the `utm_` prefix; matching components are omitted. Every other component is retained without decoding, reordering, collapsing, or re-encoding, including repeated keys, blank values, empty components, and encoded values. It does not remove `gclid`, `ref`, `utmx`, or any other key. No declared canonical URL participates in this serializer, for either same-origin or cross-origin declarations.

`CacheIdentity` is deliberately separate from the display URL. It consists of the dedup key, policy version, retrieval variant, and extraction variant. Browser-rendered and static results, or distinct extraction variants, cannot share an entry merely because they display the same URL. Cleaner version and cache persistence are Phase 4 concerns and are not introduced here.

## Source-document identity

For exact cleaned Markdown bytes, define:

```text
content_digest = SHA-256(UTF-8(full exact cleaned Markdown))
document_id = SHA-256(UTF-8(fragment-free final_url) || 0x00 || raw content_digest bytes)
```

No line-ending, Unicode, whitespace, or case normalization occurs before hashing. The fragment-free final URL and the full document are the only variable inputs. Cleaner version, retrieval timestamp, status, content type, and retrieval method remain provenance and do not change `document_id`. Direct `DocumentIdentity` construction validates the lowercase SHA-256 values and verifies that its stored ID derives from its stored final URL and content digest.

For a verbatim span in that exact cleaned Markdown, define:

```text
evidence_id = SHA-256(UTF-8(fragment-free final_url) || 0x00 || raw content_digest bytes || 0x00 || ASCII(start_index:end_index))
```

`start_index` and `end_index` are Unicode code-point offsets into the cleaned Markdown, and the end is exclusive. The chunk service's `ORCHESTRATOR_MARKDOWN` mode skips destructive pre-cleaning, derives contiguous segment positions without search fallback, and returns chunk text only as `cleaned_markdown[start_index:end_index]`. A response that cannot prove that equality is marked `verbatim=false` and receives no `evidence_id`. Ranking order, embedding backend, cleaner version labels, and chunker strategy labels are not identity inputs.

Passages expose the span and both IDs directly. Citations expose the shared `document_id` and their bounded `evidence_spans`. When `include_raw_markdown=true`, each entry retains the original crawled Markdown for compatibility and adds the exact cleaned Markdown plus `document_id`, allowing callers to verify every exact slice.

## Evidence metadata

Citation metadata is deterministic and bounded. Selected values retain `source` and `confidence`, while conflicting distinct values retain all bounded candidates. Sources include HTML title/meta/canonical/language, JSON-LD, Crawl4AI page metadata, SearXNG discovery dates, and future sitemap modification dates.

Publication and modification timestamps are normalized to UTC only when parsing succeeds with an explicit timezone or a date-only value. JSON-LD `datePublished`, page publication metadata, and SearXNG `publishedDate` may populate `published`; modification metadata remains in `modified_at`. Sitemap `lastmod` is modification-only, HTTP validators are not silently treated as page dates, and body prose is never date-mined.

`orchestrator/content_dedup.py` hashes the full whitespace-normalized Markdown for destructive exact-duplicate removal. It does not use declared canonical metadata, and it does not delete documents merely because they share a long prefix. This operational dedup fingerprint remains separate from the exact-byte, final-URL-bound `document_id` used for evidence identity.

## Closed outcome codes

The internal `str` enums in `orchestrator/outcome_codes.py` are the only control-flow codes for their stated stages. Their names and lowercase values are closed and have no aliases. Human-readable messages are separate diagnostic data and must not drive control flow.

| Stage | Codes |
|---|---|
| Fetch | `content`, `empty_shell`, `challenge`, `robots_refused`, `upstream_timeout`, `deadline_cancelled`, `unsafe_redirect`, `unsupported_content`, `unsupported_capability`, `content_too_large`, `extraction_empty`, `malformed_upstream_response`, `upstream_failure`, `rate_limited`, `capacity_unavailable`, `unsafe_target`, `local_processing_failure` |
| Map | `completed`, `partial`, `cancelled`, `unsafe_seed`, `unsafe_redirect`, `robots_refused`, `limit_reached`, `upstream_timeout`, `deadline_cancelled`, `malformed_upstream_response`, `upstream_failure`, `rate_limited`, `unsafe_target`, `local_processing_failure` |
| Crawl | `completed`, `partial`, `failed`, `cancelled`, `no_admitted_urls`, `deadline_cancelled`, `upstream_timeout`, `upstream_failure`, `rate_limited`, `unsafe_seed`, `unsafe_target`, `unsafe_redirect`, `local_processing_failure` |
| Cache | `miss`, `fresh`, `stale`, `revalidated`, `bypass`, `corrupt`, `unsupported_capability` |
| Job | `queued`, `running`, `completed`, `partial`, `failed`, `cancelled`, `expired` |

`rate_limited`, `capacity_unavailable`, `unsafe_target`, `unsafe_redirect`, `unsafe_seed`, and `local_processing_failure` remain distinct so per-URL outcome aggregation can distinguish upstream throttling, local capacity, unsafe source classes, and local faults.

## Endpoint access and egress decisions

New endpoints must remain deployment-internal behind an operator-supplied protected ingress/access layer until a future approved first-party-auth design broadens exposure. A loopback default is not evidence that a proxy or protected ingress exists. The fetch endpoint preserves this access decision and does not add first-party ingress authentication.

The first-party Compose configurations attach Crawl4AI to an isolated internal control network and a dedicated outbound network. Crawl4AI's built-in proxy owns connect-time DNS pinning and rejects non-global targets; the orchestrator performs non-blocking safety checks before dispatch and revalidates every changed final URL before accepting content. The identity models do not themselves authorize dispatch or bypass either enforcement layer.
