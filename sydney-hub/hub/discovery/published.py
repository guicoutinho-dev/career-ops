"""Source facts for published job listings; missing facts stay missing."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


@dataclass(frozen=True)
class PublishedListing:
    source_id: str
    external_id: str | None
    source_url: str
    title: str
    company: str
    location: str | None
    published_at: datetime | None
    employment_type: str | None
    description: str | None
    lat: float | None
    lng: float | None


@dataclass(frozen=True)
class ListingCheck:
    outcome: str
    checked_at: float
    evidence_url: str
    excerpt: str | None


@dataclass(frozen=True)
class SourcePolicy:
    source_id: str
    publish_allowed: bool = False
    attribution: str = ''
    max_requests: int = 0


def is_recent(listing: PublishedListing, check: ListingCheck, now: datetime) -> bool:
    if listing.published_at is None or check.outcome != 'ok':
        return False
    current = now.astimezone(timezone.utc)
    posted = listing.published_at.astimezone(timezone.utc)
    checked = datetime.fromtimestamp(check.checked_at, timezone.utc)
    return (current - timedelta(days=30) <= posted <= current
            and current - timedelta(hours=48) <= checked <= current)
