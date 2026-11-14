from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from hub.discovery.health import SourceHealthStore, append_yield_journal
from hub.discovery.pilot_gate import confirm_pilot_audit
from hub.discovery.published import ListingCheck, PublishedListing, SourcePolicy
from hub.discovery.transit import CommuteResult
from hub.pipeline.routine import RunAlreadyActive
from hub.scheduler.jobs_scheduler import PublishedSchedulerService, evaluate_scheduler_gate
from hub.settings import JobsConfig, PublishedJobsSettings
from hub.storage.catalog import CatalogRepository
from hub.storage.db import Database, ISOLATED_DB_MARKER, ensure_published_schema


def _seed_full_pilot(
    tmp_path: Path,
    *,
    now: datetime,
    days_observed: int,
    confirm_audit: bool = True,
    with_commute: int = 10,
) -> tuple[JobsConfig, Path]:
    isolated_root = tmp_path / "data" / "jobs_isolated"
    isolated_root.mkdir(parents=True, exist_ok=True)
    (isolated_root / ISOLATED_DB_MARKER).write_text("isolated\n", encoding="utf-8")
    db_path = isolated_root / "jobs_hub.db"
    ensure_published_schema(db_path, isolated_root=isolated_root)

    catalog = CatalogRepository(Database(db_path))
    policy = SourcePolicy("ats:greenhouse:cafe", True, "Official Cafe Board", 50)
    dep = datetime(2026, 11, 17, 9, 0, tzinfo=ZoneInfo("Australia/Sydney"))
    audit_records = []
    for i in range(20):
        has_coord = i < with_commute
        lat = -33.9524 if has_coord else None
        lng = 151.1376 if has_coord else None
        url = f"https://job-boards.greenhouse.io/cafe/jobs/{200 + i}"
        opp_id = catalog.upsert_published_listing(
            PublishedListing(
                "ats:greenhouse:cafe",
                str(200 + i),
                url,
                f"Barista {i}",
                "Cafe",
                "Rockdale NSW",
                now - timedelta(days=1),
                "Casual" if i < 5 else None,
                None,
                lat,
                lng,
            ),
            ListingCheck("ok", now.timestamp(), url, f"Barista {i}"),
            policy,
            role_bucket="barista",
        )
        if has_coord:
            catalog.record_commute_observation(
                opp_id,
                "Rockdale Station",
                "tuesday_0900",
                dep,
                CommuteResult(18, "https://api.transport.nsw.gov.au/v1/tp/trip", now.timestamp(), "ok"),
            )
        audit_records.append(
            {
                "opportunity_id": opp_id,
                "source_url": url,
                "link_status": "valid",
                "title_company_match": True,
                "evidence_checked": True,
                "collision_checked": True,
                "collision_verdict": "distinct",
                "promoted_lead_or_generic": False,
                "checked_at": now.isoformat(),
            }
        )

    state_dir = tmp_path / "data" / "published-state"
    reports_dir = tmp_path / "data" / "pilot-reports"
    boards_path = tmp_path / "config" / "published_boards.yml"
    boards_path.parent.mkdir(parents=True, exist_ok=True)
    boards_path.write_text("boards: []\n", encoding="utf-8")
    policies_path = tmp_path / "config" / "source_policies.yml"
    policies_path.write_text("sources: {}\n", encoding="utf-8")

    start_date = (now.astimezone(ZoneInfo("Australia/Sydney")).date() - timedelta(days=days_observed - 1))
    for offset in range(days_observed):
        d = (start_date + timedelta(days=offset)).isoformat()
        append_yield_journal(reports_dir, {"sydney_date": d, "requests": 1, "verified": 20})

    if confirm_audit:
        audit_file = tmp_path / "audit.json"
        audit_file.write_text(json.dumps(audit_records), encoding="utf-8")
        confirm_pilot_audit(state_dir, db_path, reports_dir, boards_path, policies_path, audit_records_path=audit_file, now=now)

    cfg = JobsConfig(
        published_jobs=PublishedJobsSettings(
            db_path="data/jobs_isolated/jobs_hub.db",
            isolated_db_dir="data/jobs_isolated",
            policies_path="config/source_policies.yml",
            boards_path="config/published_boards.yml",
            state_dir="data/published-state",
            reports_dir="data/pilot-reports",
            scheduler_enabled=True,
            schedule=["07:30"],
            workable_min_interval_hours=20,
        ),
        root=tmp_path,
    )
    return cfg, state_dir


def test_scheduler_gate_blocks_day_1_even_when_20_10_5_and_audit_pass(tmp_path):
    now = datetime(2026, 11, 14, 12, tzinfo=timezone.utc)
    cfg, _ = _seed_full_pilot(tmp_path, now=now, days_observed=1, confirm_audit=True)

    gate = evaluate_scheduler_gate(cfg, now=now)
    assert gate.allowed is False
    assert "seven_day_observation_pending" in gate.reasons

    svc = PublishedSchedulerService(cfg, clock=lambda: now.timestamp())
    assert svc.start() is False
    assert svc.is_active is False
    assert svc.next_run_at() is None


def test_scheduler_gate_fails_closed_until_pilot_audit_confirmed(tmp_path):
    now = datetime(2026, 11, 20, 12, tzinfo=timezone.utc)
    cfg, _ = _seed_full_pilot(tmp_path, now=now, days_observed=7, confirm_audit=False)
    gate = evaluate_scheduler_gate(cfg, now=now)
    assert gate.allowed is False
    assert "manual_audit_unconfirmed" in gate.reasons

    disabled_cfg = cfg.model_copy(
        update={"published_jobs": cfg.published_jobs.model_copy(update={"scheduler_enabled": False})}
    )
    assert "scheduler_disabled" in evaluate_scheduler_gate(disabled_cfg, now=now).reasons

    too_many_slots = cfg.model_copy(
        update={"published_jobs": cfg.published_jobs.model_copy(update={"schedule": ["07:00", "12:00", "18:00"]})}
    )
    assert "invalid_schedule_slots" in evaluate_scheduler_gate(too_many_slots, now=now).reasons

    outside_db_cfg = cfg.model_copy(
        update={"published_jobs": cfg.published_jobs.model_copy(update={"db_path": "outside.db"})}
    )
    gate_out = evaluate_scheduler_gate(outside_db_cfg, now=now)
    assert gate_out.allowed is False

    # Also fail closed when commute target is blocked (<10 exact coords)
    cfg_no_commute, _ = _seed_full_pilot(tmp_path / "nc", now=now, days_observed=7, confirm_audit=False, with_commute=0)
    gate_nc = evaluate_scheduler_gate(cfg_no_commute, now=now)
    assert gate_nc.allowed is False
    assert "first_run_targets_not_met" in gate_nc.reasons


def test_scheduler_starts_only_after_7day_gate_and_enforces_workable_daily_floor(tmp_path):
    now = datetime(2026, 11, 20, 12, tzinfo=timezone.utc)
    cfg, state_dir = _seed_full_pilot(tmp_path, now=now, days_observed=7, confirm_audit=True)

    calls = []

    def fake_run(*, skip_workable: bool = False):
        calls.append({"skip_workable": skip_workable})
        return {"ok": True, "skip_workable": skip_workable}

    SourceHealthStore(state_dir).record_workable_fetch(now.timestamp() - 3600)
    svc = PublishedSchedulerService(cfg, run_fn=fake_run, clock=lambda: now.timestamp())
    try:
        assert svc.gate_result.allowed is True
        assert svc.start() is True
        assert svc.is_active is True
        assert svc.next_run_at() is not None

        res = svc.trigger_manual()
        assert res["skip_workable"] is True
        assert calls == [{"skip_workable": True}]

        svc._run_lock.acquire()
        try:
            with pytest.raises(RunAlreadyActive):
                svc.trigger_manual()
        finally:
            svc._run_lock.release()
    finally:
        svc.shutdown()
