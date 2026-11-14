from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class EmployerCandidate:
    website_url: str
    discovery_source: str
    suburb: str
    category: str
    source_key: str | None = None


@dataclass(frozen=True)
class DiscoveryBatch:
    candidates: list[EmployerCandidate]
    requests: int
    cost_estimate: float | None


@dataclass(frozen=True)
class WebsiteFinding:
    verification_state: Literal["verified_job", "unverified_job_post", "employer_lead"]
    source_url: str
    employer_name: str | None
    title: str | None
    excerpt: str | None
    checked_at: float


@dataclass(frozen=True)
class SiteInspection:
    outcome: Literal["ok", "timeout", "blocked", "error"]
    findings: list[WebsiteFinding]
    requests: int
