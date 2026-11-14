from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class EmployerCandidate:
    website_url: str | None
    discovery_source: str
    suburb: str
    category: str
    source_key: str | None = None
    business_name: str | None = None
    lat: float | None = None
    lng: float | None = None


@dataclass(frozen=True)
class DiscoveryBatch:
    candidates: list[EmployerCandidate]
    requests: int
    cost_estimate: float | None
    total_businesses: int | None = None
    missing_websites: int = 0
    duplicate_websites: int = 0


@dataclass(frozen=True)
class WebsiteFinding:
    verification_state: Literal["verified_job", "unverified_job_post", "employer_lead"]
    source_url: str
    employer_name: str | None
    title: str | None
    excerpt: str | None
    checked_at: float
    listing_source: str | None = None
    ats_provider: str | None = None
    role_bucket: str | None = None
    suburb_match_basis: str | None = None
    discovery_source: str | None = None


@dataclass(frozen=True)
class SiteInspection:
    outcome: Literal["ok", "timeout", "blocked", "error"]
    findings: list[WebsiteFinding]
    requests: int
    outbound_urls: tuple[str, ...] = ()
