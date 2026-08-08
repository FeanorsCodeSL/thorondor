"""Internal URL, cache, and source-document identity policy."""
import hashlib
import re
from typing import Literal, NewType
from urllib.parse import unquote_plus, urldefrag, urlsplit, urlunsplit

import idna
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .url_safety import parse_ip_literal

DEDUP_POLICY_VERSION = "thorondor.url-dedup.v1"
SHA256_PATTERN = r"^[0-9a-f]{64}$"

RequestedUrl = NewType("RequestedUrl", str)
FinalUrl = NewType("FinalUrl", str)
DisplayUrl = NewType("DisplayUrl", str)
SafetyTarget = NewType("SafetyTarget", str)
DedupKey = NewType("DedupKey", str)
EvidenceId = NewType("EvidenceId", str)


class UrlIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    requested_url: RequestedUrl
    final_url: FinalUrl
    display_url: DisplayUrl
    dedup_policy_version: Literal["thorondor.url-dedup.v1"] = DEDUP_POLICY_VERSION
    declared_canonical_url: str | None = None

    @model_validator(mode="after")
    def validate_roles(self) -> "UrlIdentity":
        if safety_target_for(self.final_url) != self.final_url:
            raise ValueError("final_url must not contain a fragment")
        if safety_target_for(self.display_url) != self.final_url:
            raise ValueError("display_url must resolve to final_url after fragment removal")
        return self

    @property
    def safety_target(self) -> SafetyTarget:
        return SafetyTarget(self.final_url)

    @property
    def dedup_key(self) -> DedupKey:
        return dedup_key_for(self.final_url)


class CacheIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    dedup_key: DedupKey = Field(min_length=1)
    retrieval_variant: str = Field(min_length=1)
    extraction_variant: str = Field(min_length=1)
    dedup_policy_version: Literal["thorondor.url-dedup.v1"] = DEDUP_POLICY_VERSION


class DocumentIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    final_url: FinalUrl
    cleaned_markdown_sha256: str = Field(pattern=SHA256_PATTERN)
    document_id: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_document_identity(self) -> "DocumentIdentity":
        if safety_target_for(self.final_url) != self.final_url:
            raise ValueError("final_url must not contain a fragment")
        expected_document_id = _document_id_from_digest(
            self.final_url,
            bytes.fromhex(self.cleaned_markdown_sha256),
        )
        if self.document_id != expected_document_id:
            raise ValueError("document_id must match final_url and cleaned_markdown_sha256")
        return self


class EvidenceIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    final_url: FinalUrl
    cleaned_markdown_sha256: str = Field(pattern=SHA256_PATTERN)
    document_id: str = Field(pattern=SHA256_PATTERN)
    start_index: int = Field(ge=0)
    end_index: int = Field(gt=0)
    evidence_id: EvidenceId = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_evidence_identity(self) -> "EvidenceIdentity":
        if self.end_index <= self.start_index:
            raise ValueError("end_index must be greater than start_index")
        expected_document_id = _document_id_from_digest(
            self.final_url,
            bytes.fromhex(self.cleaned_markdown_sha256),
        )
        if self.document_id != expected_document_id:
            raise ValueError("document_id must match final_url and cleaned_markdown_sha256")
        expected_evidence_id = _evidence_id_from_digest(
            self.final_url,
            bytes.fromhex(self.cleaned_markdown_sha256),
            self.start_index,
            self.end_index,
        )
        if self.evidence_id != expected_evidence_id:
            raise ValueError("evidence_id must match document and span identity")
        return self


def safety_target_for(final_url: str) -> SafetyTarget:
    return SafetyTarget(urldefrag(final_url).url)


def dedup_key_for(final_url: str) -> DedupKey:
    fragment_free_url = safety_target_for(final_url)
    parsed = urlsplit(fragment_free_url)
    query, has_retained_query_component = _dedup_query(parsed.query)
    if not parsed.netloc:
        key = urlunsplit((parsed.scheme.lower(), parsed.netloc, parsed.path, query, ""))
    else:
        scheme = parsed.scheme.lower()
        key = urlunsplit((scheme, _dedup_netloc(parsed, scheme), parsed.path, query, ""))
    if "?" in fragment_free_url and has_retained_query_component and not query:
        key += "?"
    return DedupKey(key)


def _dedup_query(query: str) -> tuple[str, bool]:
    retained = [
        component
        for component in query.split("&")
        if not unquote_plus(component.partition("=")[0]).lower().startswith("utm_")
    ]
    return "&".join(retained), bool(retained)


def _dedup_netloc(parsed, scheme: str) -> str:
    host = _canonical_host(parsed.hostname)
    if not host:
        return parsed.netloc
    port = parsed.port
    netloc = f"[{host}]" if ":" in host else host
    if port is not None and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        netloc = f"{netloc}:{port}"
    userinfo, separator, _ = parsed.netloc.rpartition("@")
    return f"{userinfo}@{netloc}" if separator else netloc


def _canonical_host(host: str | None) -> str:
    if not host:
        return ""
    normalized = host.rstrip(".").lower()
    literal = parse_ip_literal(normalized)
    if literal is not None:
        return str(literal)
    return idna.encode(normalized, uts46=True, transitional=False).decode("ascii")


def build_url_identity(
    requested_url: str,
    final_url: str,
    declared_canonical_url: str | None = None,
) -> UrlIdentity:
    return UrlIdentity(
        requested_url=requested_url,
        final_url=safety_target_for(final_url),
        display_url=final_url,
        declared_canonical_url=declared_canonical_url,
    )


def build_cache_identity(
    identity: UrlIdentity,
    retrieval_variant: str,
    extraction_variant: str,
) -> CacheIdentity:
    return CacheIdentity(
        dedup_key=identity.dedup_key,
        retrieval_variant=retrieval_variant,
        extraction_variant=extraction_variant,
    )


def cleaned_markdown_sha256(cleaned_markdown: str) -> str:
    return hashlib.sha256(cleaned_markdown.encode("utf-8")).hexdigest()


def document_id_for(final_url: str, cleaned_markdown: str) -> str:
    content_digest = hashlib.sha256(cleaned_markdown.encode("utf-8")).digest()
    return _document_id_from_digest(safety_target_for(final_url), content_digest)


def _document_id_from_digest(final_url: str, content_digest: bytes) -> str:
    return hashlib.sha256(final_url.encode("utf-8") + b"\x00" + content_digest).hexdigest()


def build_document_identity(final_url: str, cleaned_markdown: str) -> DocumentIdentity:
    fragment_free_final_url = safety_target_for(final_url)
    return DocumentIdentity(
        final_url=fragment_free_final_url,
        cleaned_markdown_sha256=cleaned_markdown_sha256(cleaned_markdown),
        document_id=document_id_for(fragment_free_final_url, cleaned_markdown),
    )


def evidence_id_for(
    final_url: str,
    cleaned_markdown_digest: str,
    start_index: int,
    end_index: int,
) -> EvidenceId:
    if start_index < 0 or end_index <= start_index:
        raise ValueError("evidence span must be non-empty and end-exclusive")
    if not re.fullmatch(r"[0-9a-f]{64}", cleaned_markdown_digest):
        raise ValueError("cleaned_markdown_digest must be a lowercase SHA-256 digest")
    return EvidenceId(
        _evidence_id_from_digest(
            safety_target_for(final_url),
            bytes.fromhex(cleaned_markdown_digest),
            start_index,
            end_index,
        )
    )


def _evidence_id_from_digest(
    final_url: str,
    content_digest: bytes,
    start_index: int,
    end_index: int,
) -> str:
    span = f"{start_index}:{end_index}".encode("ascii")
    return hashlib.sha256(final_url.encode("utf-8") + b"\x00" + content_digest + b"\x00" + span).hexdigest()


def build_evidence_identity(
    final_url: str,
    cleaned_markdown: str,
    start_index: int,
    end_index: int,
) -> EvidenceIdentity:
    document = build_document_identity(final_url, cleaned_markdown)
    return EvidenceIdentity(
        final_url=document.final_url,
        cleaned_markdown_sha256=document.cleaned_markdown_sha256,
        document_id=document.document_id,
        start_index=start_index,
        end_index=end_index,
        evidence_id=evidence_id_for(
            document.final_url,
            document.cleaned_markdown_sha256,
            start_index,
            end_index,
        ),
    )
