"""Persistent source health, HTTP 429 cooldowns, and 7-day yield journaling."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field


_SECRET_KEYS = ("key", "token", "secret", "password", "authorization", "cookie")


class SourceHealthSnapshot(BaseModel):
    source_id: str
    last_run_at: str
    last_outcome: Literal["ok", "cooldown", "error", "skipped_unapproved", "skipped"]
    cooldown_until: float | None = None
    retry_after_seconds: int | None = None
    consecutive_failures: int = 0
    last_error: str | None = None
    requests: int = 0
    wire_bytes: int = 0
    duration_ms: int = 0
    fetched: int = 0
    eligible: int = 0
    new_count: int = 0
    updated_count: int = 0
    duplicate_within_source: int = 0
    verified_count: int = 0
    unverified_count: int = 0
    recent_count: int = 0
    explicit_modality_count: int = 0
    exact_coords_count: int = 0
    yield_per_request: float = 0.0
    cost_aud: float = 0.0
    license: str = ""


def parse_retry_after(
    header_value: str | None,
    *,
    now: float,
    default_seconds: int = 3600,
) -> int:
    """Parse numeric seconds or HTTP-date Retry-After and clamp strictly to [60, 86400]."""
    raw = (header_value or "").strip()
    seconds: int | None = None
    if raw:
        try:
            seconds = int(float(raw))
        except ValueError:
            try:
                parsed = parsedate_to_datetime(raw)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                seconds = int(parsed.timestamp() - now)
            except (TypeError, ValueError, OverflowError):
                seconds = None
    if seconds is None or seconds < 0:
        seconds = default_seconds
    return max(60, min(86400, seconds))


def redact_secrets(payload: Any) -> Any:
    """Recursively mask any credential-bearing keys before persisting reports or journals."""
    if isinstance(payload, dict):
        cleaned: dict[str, Any] = {}
        for k, v in payload.items():
            key_lower = str(k).lower()
            if any(token in key_lower for token in _SECRET_KEYS):
                cleaned[k] = "[REDACTED]" if v not in (None, "") else v
            else:
                cleaned[k] = redact_secrets(v)
        return cleaned
    if isinstance(payload, list):
        return [redact_secrets(item) for item in payload]
    return payload


class SourceHealthStore:
    """Atomic JSON persistence for per-source cooldowns and daily Workable cadence."""

    def __init__(self, state_dir: Path) -> None:
        self._dir = Path(state_dir)
        self._path = self._dir / "source_health.json"

    def load(self) -> dict[str, Any]:
        if not self._path.is_file():
            return {"sources": {}, "workable_last_fetch": None}
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data.setdefault("sources", {})
                data.setdefault("workable_last_fetch", None)
                return data
        except (OSError, ValueError):
            pass
        return {"sources": {}, "workable_last_fetch": None}

    def _save(self, data: dict[str, Any]) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_name(f"{self._path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(redact_secrets(data), indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self._path)

    def get(self, source_id: str) -> dict[str, Any] | None:
        return self.load().get("sources", {}).get(source_id)

    def in_cooldown(self, source_id: str, now: float) -> tuple[bool, float | None]:
        entry = self.get(source_id)
        if not isinstance(entry, dict):
            return False, None
        until = entry.get("cooldown_until")
        if isinstance(until, (int, float)) and until > now:
            return True, float(until)
        return False, None

    def record_result(self, snapshot: SourceHealthSnapshot) -> None:
        data = self.load()
        data["sources"][snapshot.source_id] = snapshot.model_dump()
        self._save(data)

    def record_workable_fetch(self, now: float) -> None:
        data = self.load()
        data["workable_last_fetch"] = float(now)
        self._save(data)

    def last_workable_fetch(self) -> float | None:
        val = self.load().get("workable_last_fetch")
        return float(val) if isinstance(val, (int, float)) else None


def append_yield_journal(reports_dir: Path, entry: dict[str, Any]) -> Path:
    """Append a single redacted JSON telemetry entry to data/pilot-reports/yield_journal.jsonl."""
    reports_path = Path(reports_dir)
    reports_path.mkdir(parents=True, exist_ok=True)
    journal_path = reports_path / "yield_journal.jsonl"
    redacted = redact_secrets(entry)
    with journal_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(redacted, ensure_ascii=False) + "\n")
    return journal_path
