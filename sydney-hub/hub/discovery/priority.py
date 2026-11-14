"""Priority queue and check-outcome classifier for Track 1 employer enrichment."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlparse

from hub.discovery.models import EmployerCandidate

_PILOT_SUBURBS = ("Rockdale", "Kogarah", "Hurstville", "Mascot")
HOSPITALITY_CATEGORIES = frozenset({"cafe", "restaurant", "bar", "hotel", "cleaning service"})
SOCIAL_HOSTS = ("instagram.com", "facebook.com", "fb.com", "tiktok.com", "linktr.ee")

CheckState = Literal["reachable_ok", "reachable_no_title", "blocked", "timeout", "error", "uninspected"]


@dataclass(frozen=True)
class PriorityQueueResult:
    selected: list[EmployerCandidate]
    counts: dict[str, int]


def _canonical_url_key(url: str) -> str:
    parsed = urlparse((url or "").strip())
    scheme = "https" if parsed.scheme.lower() in ("http", "https") else parsed.scheme.lower()
    netloc = parsed.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    return parsed._replace(scheme=scheme, netloc=netloc, path=parsed.path.rstrip("/") or "/", fragment="").geturl()


def is_social_url(url: str) -> bool:
    host = (urlparse(url or "").hostname or "").lower()
    return any(host == domain or host.endswith(f".{domain}") for domain in SOCIAL_HOSTS)


def classify_check_outcome(
    checked_at: float | None,
    excerpt: str | None,
    *,
    check_outcome: str | None = None,
) -> CheckState:
    raw = (check_outcome or "").strip().lower()
    if not raw and isinstance(excerpt, str) and excerpt.startswith("Website check outcome:"):
        raw = excerpt.split(":", 1)[1].strip().lower()
    if raw in ("blocked", "timeout", "error"):
        return raw  # Failed inspections never masquerade as reachable even if checked_at is set.
    if raw == "ok_no_title":
        return "reachable_no_title"
    if raw == "ok" or checked_at is not None:
        return "reachable_ok"
    return "uninspected"


def _round_robin_by_suburb(candidates: list[EmployerCandidate]) -> list[EmployerCandidate]:
    buckets: dict[str, deque[EmployerCandidate]] = {suburb: deque() for suburb in _PILOT_SUBURBS}
    extras: deque[EmployerCandidate] = deque()
    for candidate in candidates:
        if candidate.suburb in buckets:
            buckets[candidate.suburb].append(candidate)
        else:
            extras.append(candidate)
    ordered: list[EmployerCandidate] = []
    while any(buckets.values()):
        for suburb in _PILOT_SUBURBS:
            if buckets[suburb]:
                ordered.append(buckets[suburb].popleft())
    ordered.extend(extras)
    return ordered


def build_priority_queue(
    pbf_candidates: list[EmployerCandidate],
    db_states: list[dict],
    *,
    max_employers: int = 60,
    priority: Literal["hospitality", "all"] = "hospitality",
) -> PriorityQueueResult:
    if max_employers < 0:
        raise ValueError("max_employers must be >= 0")

    counts = {
        "retail_preserved_skipped": 0,
        "social_only_skipped": 0,
        "failed_history_skipped": 0,
        "tier1_reachable": 0,
        "tier2_fresh_osm": 0,
    }
    seen_keys: set[str] = set()
    tier1_ok: list[EmployerCandidate] = []
    tier1_no_title: list[EmployerCandidate] = []
    tier1_uninspected: list[EmployerCandidate] = []

    for row in db_states:
        url = (row.get("source_url") or "").strip()
        if not url:
            continue
        key = _canonical_url_key(url)
        if key in seen_keys:
            continue
        seen_keys.add(key)

        category = row.get("category")
        if priority == "hospitality" and category not in HOSPITALITY_CATEGORIES:
            counts["retail_preserved_skipped"] += 1
            continue
        if is_social_url(url):
            counts["social_only_skipped"] += 1
            continue

        state = classify_check_outcome(
            row.get("checked_at"),
            row.get("excerpt"),
            check_outcome=row.get("check_outcome"),
        )
        if state in ("blocked", "timeout", "error"):
            counts["failed_history_skipped"] += 1
            continue

        candidate = EmployerCandidate(
            website_url=url,
            discovery_source="OpenStreetMap discovery",
            suburb=row.get("suburb") or "",
            category=category or "cafe",
            source_key=row.get("id"),
            business_name=row.get("company"),
            lat=row.get("lat"),
            lng=row.get("lng"),
        )
        if state == "reachable_ok":
            tier1_ok.append(candidate)
        elif state == "reachable_no_title":
            tier1_no_title.append(candidate)
        else:
            tier1_uninspected.append(candidate)

    tier2_pool: list[EmployerCandidate] = []
    for candidate in pbf_candidates:
        key = _canonical_url_key(candidate.website_url)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        if priority == "hospitality" and candidate.category not in HOSPITALITY_CATEGORIES:
            counts["retail_preserved_skipped"] += 1
            continue
        if is_social_url(candidate.website_url):
            counts["social_only_skipped"] += 1
            continue
        tier2_pool.append(candidate)

    tier1_ordered = (
        _round_robin_by_suburb(tier1_ok)
        + _round_robin_by_suburb(tier1_no_title)
        + _round_robin_by_suburb(tier1_uninspected)
    )
    tier2_ordered = _round_robin_by_suburb(tier2_pool)

    selected_tier1 = tier1_ordered[:max_employers]
    remaining = max(0, max_employers - len(selected_tier1))
    selected_tier2 = tier2_ordered[:remaining]
    counts["tier1_reachable"] = len(selected_tier1)
    counts["tier2_fresh_osm"] = len(selected_tier2)
    return PriorityQueueResult(selected=selected_tier1 + selected_tier2, counts=counts)
