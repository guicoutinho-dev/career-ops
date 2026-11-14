"""Conservative recheck, staleness, and expiry policy for verified opportunities."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any


RECHECK_INTERVAL_HOURS = 24
STALE_AFTER_DAYS = 7
CONSECUTIVE_MISS_EXPIRY_THRESHOLD = 2

EXPIRING_OUTCOMES = frozenset({"closed_or_expired", "http_404", "http_410"})
TRANSIENT_UNKNOWN_OUTCOMES = frozenset({
    "timeout_or_network",
    "http_403",
    "http_429",
    "http_500",
    "http_502",
    "http_503",
    "http_504",
    "blocked",
    "oversize",
    "unconfirmed",
})


def is_explicit_expiry(outcome: str | None) -> bool:
    return (outcome or "").strip().lower() in EXPIRING_OUTCOMES


def is_transient_or_unknown(outcome: str | None) -> bool:
    norm = (outcome or "").strip().lower()
    if norm == "ok" or norm in EXPIRING_OUTCOMES:
        return False
    return norm in TRANSIENT_UNKNOWN_OUTCOMES or norm.startswith("http_5") or norm.startswith("http_3")


def _parse_iso(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def should_recheck(
    last_verified_at: str | None,
    *,
    now: datetime,
    recheck_interval_hours: int = RECHECK_INTERVAL_HOURS,
    force_recheck: bool = False,
) -> bool:
    """Return True if a listing has never been verified or exceeded recheck_interval_hours."""
    if force_recheck or not last_verified_at:
        return True
    verified_dt = _parse_iso(last_verified_at)
    if verified_dt is None:
        return True
    current = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    return (current - verified_dt) >= timedelta(hours=max(1, recheck_interval_hours))


def select_incremental_candidates(
    eligible: list[tuple[Any, Any]],
    last_verified_by_url: dict[str, str | None],
    *,
    now: datetime,
    recheck_interval_hours: int = RECHECK_INTERVAL_HOURS,
    force_recheck: bool = False,
) -> tuple[list[tuple[Any, Any]], int]:
    """Filter out recently verified listings and prioritize unchecked/oldest-checked items."""
    normalized_map = {
        str(k).strip().lower(): v for k, v in last_verified_by_url.items() if k
    }
    to_check: list[tuple[datetime, int, tuple[Any, Any]]] = []
    skipped_fresh = 0
    for idx, pair in enumerate(eligible):
        item = pair[0]
        raw_url = getattr(item, "source_url", "").strip()
        last_ver = last_verified_by_url.get(raw_url) or normalized_map.get(raw_url.lower())
        if not should_recheck(
            last_ver,
            now=now,
            recheck_interval_hours=recheck_interval_hours,
            force_recheck=force_recheck,
        ):
            skipped_fresh += 1
            continue
        parsed = _parse_iso(last_ver) or datetime.min.replace(tzinfo=timezone.utc)
        to_check.append((parsed, idx, pair))

    to_check.sort(key=lambda row: (row[0], row[1]))
    return [row[2] for row in to_check], skipped_fresh
