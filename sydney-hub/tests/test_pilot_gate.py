from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from hub.discovery.health import SourceHealthSnapshot, SourceHealthStore, append_yield_journal
from hub.discovery.pilot_gate import confirm_pilot_audit, evaluate_pilot_gates
from hub.discovery.published import ListingCheck, PublishedListing, SourcePolicy
from hub.discovery.transit import CommuteResult
from hub.storage.catalog import CatalogRepository
from hub.storage.db import Database, ISOLATED_DB_MARKER, ensure_published_schema


def _make_isolated_db(tmp_path: Path) -> tuple[Path, Path]:
    isolated_root = tmp_path / "jobs_isolated"
    isolated_root.mkdir(parents=True, exist_ok=True)
    (isolated_root / ISOLATED_DB_MARKER).write_text("isolated\n", encoding="utf-8")
    db_path = isolated_root / "jobs_hub.db"
    ensure_published_schema(db_path, isolated_root=isolated_root)
    return db_path, isolated_root


def _seed_verified_jobs(db_path: Path, *, count: int, with_coords: int, with_commute: int, with_modality: int, now: datetime) -> list[tuple[str, str]]:
    catalog = CatalogRepository(Database(db_path))
    policy = SourcePolicy("ats:greenhouse:cafe", True, "Official Cafe Board", 50)
    dep = datetime(2026, 11, 17, 9, 0, tzinfo=ZoneInfo("Australia/Sydney"))
    seeded = []
    for i in range(count):
        has_coord = i < with_coords
        lat = -33.9524 if has_coord else None
        lng = 151.1376 if has_coord else None
        emp_type = "Casual" if i < with_modality else None
        url = f"https://job-boards.greenhouse.io/cafe/jobs/{100 + i}"
        item = PublishedListing(
            source_id="ats:greenhouse:cafe",
            external_id=str(100 + i),
            source_url=url,
            title=f"Barista {i}",
            company="Cafe",
            location="Rockdale NSW",
            published_at=now - timedelta(days=1),
            employment_type=emp_type,
            description=None,
            lat=lat,
            lng=lng,
        )
        check = ListingCheck("ok", now.timestamp(), url, f"Barista {i}")
        opp_id = catalog.upsert_published_listing(item, check, policy, role_bucket="barista")
        if has_coord and i < with_commute:
            catalog.record_commute_observation(
                opp_id,
                "Rockdale Station",
                "tuesday_0900",
                dep,
                CommuteResult(18, "https://api.transport.nsw.gov.au/v1/tp/trip", now.timestamp(), "ok"),
            )
        seeded.append((opp_id, url))
    return seeded


def _write_audit_file(path: Path, seeded: list[tuple[str, str]], now: datetime, *, overrides: dict[int, dict] | None = None) -> Path:
    overrides = overrides or {}
    records = []
    for idx, (opp_id, url) in enumerate(seeded):
        rec = {
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
        rec.update(overrides.get(idx, {}))
        records.append(rec)
    path.write_text(json.dumps(records, indent=2), encoding="utf-8")
    return path


def test_scorecard_reports_honest_blockers_without_claiming_targets_when_sources_blocked(tmp_path):
    now = datetime(2026, 11, 14, 12, tzinfo=timezone.utc)
    db_path, _ = _make_isolated_db(tmp_path)
    # Seed 1 employer_lead only
    with Database(db_path).connect() as conn:
        conn.execute(
            "INSERT INTO opportunities (id, title, source, source_url, verification_state, first_seen_at) "
            "VALUES ('lead-1', 'Barista', 'osm', 'https://cafe.com.au', 'employer_lead', 1000.0)"
        )

    state_dir = tmp_path / "state"
    reports_dir = tmp_path / "reports"
    boards_path = tmp_path / "published_boards.yml"
    boards_path.write_text("boards: []\n", encoding="utf-8")
    policies_path = tmp_path / "source_policies.yml"
    policies_path.write_text("sources:\n  workable:\n    publish_allowed: false\n", encoding="utf-8")

    SourceHealthStore(state_dir).record_result(
        SourceHealthSnapshot(
            source_id="workable",
            last_run_at=now.isoformat(),
            last_outcome="cooldown",
            cooldown_until=now.timestamp() + 1800,
        )
    )

    scorecard = evaluate_pilot_gates(
        db_path, state_dir, reports_dir, boards_path, policies_path, now=now, env={}
    )
    assert scorecard.first_run_passed is False
    assert scorecard.seven_day_status == "pending"
    assert scorecard.manual_audit_confirmed is False
    for expected in (
        "20_recent",
        "10_commute_le_45",
        "5_explicit_modality",
        "workable_cooldown",
        "workable_policy_unapproved",
        "ats_manifest_empty",
        "tfnsw_unconfigured",
    ):
        assert expected in scorecard.blockers

    # Now seed 20 verified jobs WITH TfNSW key present, but lat/lng missing -> commute still blocked!
    _seed_verified_jobs(db_path, count=20, with_coords=0, with_commute=0, with_modality=5, now=now)
    scorecard2 = evaluate_pilot_gates(
        db_path,
        state_dir,
        reports_dir,
        boards_path,
        policies_path,
        now=now,
        env={"TFNSW_FREE_QUOTA_CONFIRMED": "1", "TFNSW_API_KEY": "k"},
    )
    assert scorecard2.recent_verified_count == 20
    assert scorecard2.recent_commute_le_45_count == 0
    assert scorecard2.suburb_only_count == 20
    assert scorecard2.exact_coords_count == 0
    assert scorecard2.first_run_passed is False
    assert "10_commute_le_45_blocked_missing_exact_coords" in scorecard2.blockers


def test_confirm_pilot_audit_requires_per_listing_records_for_100_percent_of_verified_jobs(tmp_path):
    now = datetime(2026, 11, 14, 12, tzinfo=timezone.utc)
    db_path, _ = _make_isolated_db(tmp_path)
    seeded = _seed_verified_jobs(db_path, count=20, with_coords=10, with_commute=10, with_modality=5, now=now)
    state_dir = tmp_path / "state"
    reports_dir = tmp_path / "reports"
    boards_path = tmp_path / "boards.yml"
    boards_path.write_text("boards: []\n", encoding="utf-8")
    policies_path = tmp_path / "policies.yml"
    policies_path.write_text("sources: {}\n", encoding="utf-8")

    # Missing 1 record (19/20) -> incomplete_audit_coverage
    partial = _write_audit_file(tmp_path / "partial.json", seeded[:-1], now)
    with pytest.raises(ValueError, match="incomplete_audit_coverage"):
        confirm_pilot_audit(
            state_dir, db_path, reports_dir, boards_path, policies_path, audit_records_path=partial, now=now
        )

    # Wrong merge on 1 record -> rejected
    wrong_merge = _write_audit_file(tmp_path / "wrong.json", seeded, now, overrides={0: {"collision_verdict": "wrong_merge"}})
    with pytest.raises(ValueError, match="wrong_merge"):
        confirm_pilot_audit(
            state_dir, db_path, reports_dir, boards_path, policies_path, audit_records_path=wrong_merge, now=now
        )

    # 2/20 broken links (90% < 95%) -> rejected
    broken_two = _write_audit_file(
        tmp_path / "broken2.json",
        seeded,
        now,
        overrides={0: {"link_status": "broken"}, 1: {"link_status": "broken"}},
    )
    with pytest.raises(ValueError, match="link_validity_below_95_percent"):
        confirm_pilot_audit(
            state_dir, db_path, reports_dir, boards_path, policies_path, audit_records_path=broken_two, now=now
        )

    # 19/20 valid = 95% valid, 0 wrong merges -> passes and persists pilot_gate.json
    valid_audit = _write_audit_file(tmp_path / "valid.json", seeded, now, overrides={0: {"link_status": "broken"}})
    persisted = confirm_pilot_audit(
        state_dir, db_path, reports_dir, boards_path, policies_path, audit_records_path=valid_audit, now=now
    )
    assert persisted["manual_audit_confirmed"] is True
    assert persisted["valid_link_ratio"] == 0.95
    scorecard = evaluate_pilot_gates(db_path, state_dir, reports_dir, boards_path, policies_path, now=now)
    assert scorecard.manual_audit_confirmed is True


def test_seven_day_status_requires_7_distinct_sydney_days_in_yield_journal(tmp_path):
    now = datetime(2026, 11, 20, 12, tzinfo=timezone.utc)
    db_path, _ = _make_isolated_db(tmp_path)
    state_dir = tmp_path / "state"
    reports_dir = tmp_path / "reports"
    boards_path = tmp_path / "boards.yml"
    boards_path.write_text("boards: []\n", encoding="utf-8")
    policies_path = tmp_path / "policies.yml"
    policies_path.write_text("sources: {}\n", encoding="utf-8")

    for d in ("2026-11-14", "2026-11-14", "2026-11-15", "2026-11-16"):
        append_yield_journal(reports_dir, {"sydney_date": d, "requests": 1, "verified": 0})
    sc3 = evaluate_pilot_gates(db_path, state_dir, reports_dir, boards_path, policies_path, now=now)
    assert sc3.observation_days == 3
    assert sc3.seven_day_status == "pending"

    for d in ("2026-11-17", "2026-11-18", "2026-11-19", "2026-11-20"):
        append_yield_journal(reports_dir, {"sydney_date": d, "requests": 1, "verified": 0})
    sc7 = evaluate_pilot_gates(db_path, state_dir, reports_dir, boards_path, policies_path, now=now)
    assert sc7.observation_days == 7
    assert sc7.seven_day_status == "passed"
