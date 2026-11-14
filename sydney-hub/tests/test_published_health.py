from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
import json

from hub.discovery.health import (
    SourceHealthSnapshot,
    SourceHealthStore,
    append_yield_journal,
    parse_retry_after,
)


def test_parse_retry_after_handles_numeric_http_date_malformed_and_clamping():
    now = 1_700_000_000.0
    assert parse_retry_after("3584", now=now) == 3584
    assert parse_retry_after("5", now=now) == 60
    assert parse_retry_after("999999", now=now) == 86400
    assert parse_retry_after("garbage", now=now, default_seconds=1800) == 1800
    assert parse_retry_after(None, now=now, default_seconds=3600) == 3600

    future_dt = datetime.fromtimestamp(now + 1200, tz=timezone.utc)
    assert parse_retry_after(format_datetime(future_dt), now=now) == 1200


def test_source_health_store_persists_cooldown_and_workable_fetch(tmp_path):
    store = SourceHealthStore(tmp_path / "state")
    assert store.in_cooldown("workable", now=1000.0) == (False, None)
    assert store.last_workable_fetch() is None

    snap = SourceHealthSnapshot(
        source_id="workable",
        last_run_at="2026-11-14T12:00:00+00:00",
        last_outcome="cooldown",
        cooldown_until=2800.0,
        retry_after_seconds=1800,
        consecutive_failures=1,
        last_error="http_429_retry_after_1800",
        requests=1,
        wire_bytes=256,
        duration_ms=42,
        fetched=0,
        eligible=0,
        new_count=0,
        updated_count=0,
        duplicate_within_source=0,
        verified_count=0,
        unverified_count=0,
        recent_count=0,
        explicit_modality_count=0,
        exact_coords_count=0,
        yield_per_request=0.0,
        cost_aud=0.0,
        license="Jobs by Workable (XML)",
    )
    store.record_result(snap)
    store.record_workable_fetch(1000.0)

    reloaded = SourceHealthStore(tmp_path / "state")
    assert reloaded.in_cooldown("workable", now=1500.0) == (True, 2800.0)
    assert reloaded.in_cooldown("workable", now=2900.0) == (False, None)
    assert reloaded.last_workable_fetch() == 1000.0
    assert reloaded.load()["sources"]["workable"]["last_error"] == "http_429_retry_after_1800"


def test_append_yield_journal_redacts_secrets_and_appends_jsonl(tmp_path):
    path = append_yield_journal(
        tmp_path / "reports",
        {
            "run_at": "2026-11-14T12:00:00+00:00",
            "sydney_date": "2026-11-14",
            "requests": 3,
            "api_key": "SECRET-123",
            "nested": {"authorization": "apikey SECRET-456"},
        },
    )
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["api_key"] == "[REDACTED]"
    assert row["nested"]["authorization"] == "[REDACTED]"
    assert "SECRET-123" not in lines[0]
    assert "SECRET-456" not in lines[0]
