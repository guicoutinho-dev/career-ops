"""Pure schedule arithmetic. Times are naive local times."""
from __future__ import annotations

from datetime import datetime, time, timedelta


def parse_slots(values: list[str]) -> list[time]:
    slots = []
    for value in values:
        try:
            hour, minute = (int(part) for part in value.split(":"))
            slots.append(time(hour, minute))
        except ValueError as exc:
            raise ValueError(f"Invalid schedule slot {value!r}; use HH:MM") from exc
    return sorted(slots)


def latest_due_slot(now: datetime, slots: list[time]) -> datetime:
    today = [datetime.combine(now.date(), s) for s in slots]
    due = [d for d in today if d <= now]
    if due:
        return max(due)
    return datetime.combine(now.date() - timedelta(days=1), max(slots))


def next_slot(now: datetime, slots: list[time]) -> datetime:
    today = [datetime.combine(now.date(), s) for s in slots]
    upcoming = [d for d in today if d > now]
    if upcoming:
        return min(upcoming)
    return datetime.combine(now.date() + timedelta(days=1), min(slots))


def needs_catchup(now: datetime, slots: list[time], last_success_ts: float | None) -> bool:
    """True when a scheduled slot passed since the last successful run (e.g. the PC was off)."""
    if last_success_ts is None:
        return False
    return datetime.fromtimestamp(last_success_ts) < latest_due_slot(now, slots)
