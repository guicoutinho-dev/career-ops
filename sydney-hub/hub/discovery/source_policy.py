"""Fail-closed persisted source policy registry for Published Jobs sources."""
from __future__ import annotations

import ipaddress
import re
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

import yaml
from pydantic import BaseModel, Field, model_validator

from hub.discovery.published import SourcePolicy


_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ALLOWED_ACCESS_METHODS = frozenset({
    "public_xml_feed",
    "public_board_api",
    "public_job_page",
    "open_data_api",
})


class SourcePolicyEntry(BaseModel):
    """Persisted approval and attribution rules for a source or single ATS board."""

    source_id: str = Field(min_length=1)
    publish_allowed: bool = False
    eval_only: bool = False
    attribution: str = ""
    terms_url: str | None = None
    terms_reviewed_at: str | None = None
    access_method: str | None = None
    retention_days: int | None = Field(default=None, ge=1, le=365)
    cost_cap_aud: float = Field(0.0, ge=0.0)
    max_requests: int = Field(0, ge=0)
    url_alteration_allowed: bool = False

    @staticmethod
    def _valid_iso_date(value: str | None) -> bool:
        raw = (value or "").strip()
        if not _ISO_DATE_RE.match(raw):
            return False
        try:
            date.fromisoformat(raw)
        except ValueError:
            return False
        return True

    @staticmethod
    def _valid_public_terms_url(value: str | None) -> bool:
        raw = (value or "").strip()
        if not raw:
            return False
        try:
            parsed = urlsplit(raw)
        except ValueError:
            return False
        host = (parsed.hostname or "").lower().rstrip(".")
        if parsed.scheme != "https" or parsed.port not in {None, 443} or not host:
            return False
        if host == "localhost" or host.endswith((".local", ".localhost", ".internal")):
            return False
        try:
            addr = ipaddress.ip_address(host)
            return not (addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved or addr.is_multicast)
        except ValueError:
            return "." in host and not parsed.username and not parsed.password

    @model_validator(mode="after")
    def _enforce_fail_closed_invariants(self) -> "SourcePolicyEntry":
        if self.cost_cap_aud > 0.0:
            raise ValueError(f"Paid sources are forbidden (cost_cap_aud > 0) for {self.source_id}")
        if self.publish_allowed:
            if self.source_id.startswith("adzuna"):
                raise ValueError(f"{self.source_id} cannot set publish_allowed=true without a commercial license")
            if self.eval_only:
                raise ValueError(f"eval_only source {self.source_id} cannot set publish_allowed=true")
            if not self._valid_iso_date(self.terms_reviewed_at):
                raise ValueError(f"publish_allowed=true requires valid YYYY-MM-DD terms_reviewed_at for {self.source_id}")
            if not self._valid_public_terms_url(self.terms_url):
                raise ValueError(f"publish_allowed=true requires valid public https:// terms_url for {self.source_id}")
            if len(self.attribution.strip()) < 3:
                raise ValueError(f"publish_allowed=true requires non-empty attribution for {self.source_id}")
            if self.max_requests <= 0:
                raise ValueError(f"publish_allowed=true requires max_requests > 0 for {self.source_id}")
            if not (self.access_method and self.access_method.strip() in _ALLOWED_ACCESS_METHODS):
                raise ValueError(
                    f"publish_allowed=true requires valid access_method {sorted(_ALLOWED_ACCESS_METHODS)} for {self.source_id}"
                )
            if self.retention_days is None or self.retention_days <= 0:
                raise ValueError(f"publish_allowed=true requires retention_days > 0 for {self.source_id}")
        return self

    def to_source_policy(self) -> SourcePolicy:
        allowed = (
            self.publish_allowed
            and not self.eval_only
            and self.cost_cap_aud == 0.0
            and self._valid_iso_date(self.terms_reviewed_at)
            and self._valid_public_terms_url(self.terms_url)
            and len(self.attribution.strip()) >= 3
            and self.max_requests > 0
            and bool(self.access_method and self.access_method.strip() in _ALLOWED_ACCESS_METHODS)
            and self.retention_days is not None
            and self.retention_days > 0
            and not self.source_id.startswith("adzuna")
        )
        return SourcePolicy(
            source_id=self.source_id,
            publish_allowed=allowed,
            attribution=self.attribution.strip() if allowed else "",
            max_requests=self.max_requests if allowed else 0,
        )


def load_source_policies(path: Path) -> dict[str, SourcePolicyEntry]:
    """Load persisted source policies; returns empty dict if file is missing (fail-closed)."""
    if not path.is_file():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    sources = raw.get("sources") if isinstance(raw, dict) else {}
    if not isinstance(sources, dict):
        raise ValueError("source_policies.yml must contain a 'sources' mapping")
    entries: dict[str, SourcePolicyEntry] = {}
    for source_id, payload in sources.items():
        if not isinstance(payload, dict):
            raise ValueError(f"Policy entry for {source_id!r} must be a mapping")
        entries[str(source_id)] = SourcePolicyEntry(source_id=str(source_id), **payload)
    return entries


def resolve_source_policy(policies: dict[str, SourcePolicyEntry], source_id: str) -> SourcePolicy:
    """Resolve exact source_id policy fail-closed (no inheritance or fallback approval)."""
    entry = policies.get(source_id)
    if entry is None:
        return SourcePolicy(source_id=source_id, publish_allowed=False, attribution="", max_requests=0)
    return entry.to_source_policy()


def resolve_board_policy(
    policies: dict[str, SourcePolicyEntry],
    provider_id: str,
    slug: str,
) -> SourcePolicy:
    """Resolve ATS policy strictly per individual board (ats:<provider>:<slug>)."""
    board_key = f"ats:{provider_id}:{slug}"
    return resolve_source_policy(policies, board_key)
