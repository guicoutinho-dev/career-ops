"""Honest 7-day pilot scorecard, separated external gates, and 100% per-listing audit."""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal, Mapping

import httpx
from pydantic import BaseModel, Field

from hub.discovery.health import SourceHealthStore
from hub.discovery.published_ats import audit_official_boards_manifest, load_official_boards
from hub.discovery.source_policy import load_source_policies, resolve_source_policy
from hub.storage.catalog import CatalogRepository
from hub.storage.db import Database


class ListingAuditRecord(BaseModel):
    opportunity_id: str = Field(min_length=1)
    source_url: str = Field(min_length=1)
    link_status: Literal["valid", "invalid", "broken"]
    title_company_match: bool
    evidence_checked: bool
    collision_checked: bool
    collision_verdict: Literal["distinct", "duplicate_valid", "wrong_merge"]
    promoted_lead_or_generic: bool
    checked_at: str = Field(min_length=1)


class ExternalGatesStatus(BaseModel):
    workable_policy_approved: bool
    workable_cooldown_clear: bool
    workable_cooldown_until: float | None = None
    official_ats_boards_count: int
    tfnsw_configured: bool
    adzuna_eval_configured: bool
    adzuna_catalog_publish_allowed: bool = False


class PilotScorecard(BaseModel):
    recent_verified_count: int
    recent_commute_le_45_count: int
    recent_explicit_modality_count: int
    exact_coords_count: int
    suburb_only_count: int
    zero_unverified_promoted: bool
    observation_days: int
    seven_day_series: list[dict]
    first_run_passed: bool
    seven_day_status: Literal["pending", "passed", "failed"]
    manual_audit_confirmed: bool
    audit_summary: dict | None = None
    external_gates: ExternalGatesStatus
    blockers: tuple[str, ...]
    audit_sample: list[dict]


def _is_recent_at(row: dict, current: datetime) -> bool:
    pub_raw = row.get("published_at")
    ver_raw = row.get("last_verified_at")
    if not pub_raw or not ver_raw:
        return False
    try:
        posted = datetime.fromisoformat(str(pub_raw).replace("Z", "+00:00"))
        checked = datetime.fromisoformat(str(ver_raw).replace("Z", "+00:00"))
    except ValueError:
        return False
    if posted.tzinfo is None:
        posted = posted.replace(tzinfo=timezone.utc)
    if checked.tzinfo is None:
        checked = checked.replace(tzinfo=timezone.utc)
    return (
        current - timedelta(days=30) <= posted <= current
        and current - timedelta(hours=48) <= checked <= current
    )


def _read_seven_day_series(reports_dir: Path) -> list[dict]:
    journal_path = Path(reports_dir) / "yield_journal.jsonl"
    if not journal_path.is_file():
        return []
    by_day: dict[str, dict] = {}
    for line in journal_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except ValueError:
            continue
        day = str(item.get("sydney_date") or "").strip()
        if not day:
            continue
        bucket = by_day.setdefault(
            day,
            {"sydney_date": day, "runs": 0, "requests": 0, "verified": 0, "transit_measured": 0},
        )
        bucket["runs"] += 1
        bucket["requests"] += int(item.get("requests") or 0)
        bucket["verified"] += int(item.get("verified") or 0)
        bucket["transit_measured"] += int(item.get("transit_measured") or 0)
    return [by_day[d] for d in sorted(by_day)]


def _load_gate_confirmation(state_dir: Path, verified_ids: set[str]) -> tuple[bool, dict | None]:
    gate_path = Path(state_dir) / "pilot_gate.json"
    if not gate_path.is_file():
        return False, None
    try:
        data = json.loads(gate_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False, None
    if not isinstance(data, dict) or data.get("manual_audit_confirmed") is not True:
        return False, data if isinstance(data, dict) else None
    audited_ids = set(data.get("audited_ids") or [])
    if not verified_ids or audited_ids != verified_ids:
        return False, data
    if float(data.get("valid_link_ratio") or 0.0) < 0.95 or int(data.get("wrong_merges") or 0) > 0:
        return False, data
    return True, data


def evaluate_pilot_gates(
    db_path: Path,
    state_dir: Path,
    reports_dir: Path,
    boards_path: Path,
    policies_path: Path,
    *,
    now: datetime | None = None,
    env: Mapping[str, str] | None = None,
) -> PilotScorecard:
    """Compute an honest, unpadded PilotScorecard across catalog, journal, and external gates."""
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    env_map = env if env is not None else os.environ
    policies = load_source_policies(policies_path)
    health_store = SourceHealthStore(state_dir)
    in_cd, cd_until = health_store.in_cooldown("workable", current.timestamp())

    try:
        boards_count = len(load_official_boards(boards_path)) if Path(boards_path).is_file() else 0
    except ValueError:
        boards_count = 0

    external = ExternalGatesStatus(
        workable_policy_approved=resolve_source_policy(policies, "workable").publish_allowed,
        workable_cooldown_clear=not in_cd,
        workable_cooldown_until=cd_until,
        official_ats_boards_count=boards_count,
        tfnsw_configured=bool(env_map.get("TFNSW_FREE_QUOTA_CONFIRMED") == "1" and env_map.get("TFNSW_API_KEY")),
        adzuna_eval_configured=bool(
            env_map.get("ADZUNA_FREE_QUOTA_CONFIRMED") == "1"
            and env_map.get("ADZUNA_APP_ID")
            and env_map.get("ADZUNA_APP_KEY")
        ),
        adzuna_catalog_publish_allowed=False,
    )

    verified_rows: list[dict] = []
    all_rows: list[dict] = []
    if Path(db_path).is_file():
        catalog = CatalogRepository(Database(db_path))
        all_rows = catalog.list(limit=2000)
        verified_rows = [row for row in all_rows if row.get("verification_state") == "verified_job"]

    recent_rows = [row for row in verified_rows if _is_recent_at(row, current)]
    recent_commute_le_45 = sum(
        1
        for row in recent_rows
        if row.get("lat") is not None
        and row.get("lng") is not None
        and any(0 <= int(mins) <= 45 for mins in (row.get("commute_windows") or {}).values())
    )
    recent_explicit_modality = sum(
        1 for row in recent_rows if str(row.get("employment_type") or "").strip()
    )
    exact_coords_count = sum(
        1 for row in verified_rows if row.get("lat") is not None and row.get("lng") is not None
    )
    suburb_only_count = len(verified_rows) - exact_coords_count
    zero_unverified_promoted = all(
        any(
            e.get("source_type") == "job_listing" and e.get("supports_job") and e.get("checked_at") is not None
            for e in (row.get("evidence") or [])
        )
        for row in verified_rows
    )

    first_run_passed = (
        len(recent_rows) >= 20
        and recent_commute_le_45 >= 10
        and recent_explicit_modality >= 5
        and zero_unverified_promoted
    )

    series = _read_seven_day_series(reports_dir)
    observation_days = len(series)
    seven_day_status: Literal["pending", "passed", "failed"] = (
        "passed" if observation_days >= 7 else "pending"
    )

    verified_ids = {str(row["id"]) for row in verified_rows}
    manual_audit_confirmed, audit_summary = _load_gate_confirmation(state_dir, verified_ids)

    blockers: list[str] = []
    if not Path(db_path).is_file():
        blockers.append("db_missing")
    if len(recent_rows) < 20:
        blockers.append("20_recent")
    if recent_commute_le_45 < 10:
        if external.tfnsw_configured and exact_coords_count < 10:
            blockers.append("10_commute_le_45_blocked_missing_exact_coords")
        else:
            blockers.append("10_commute_le_45")
    if recent_explicit_modality < 5:
        blockers.append("5_explicit_modality")
    if not zero_unverified_promoted:
        blockers.append("unverified_promoted")
    if not external.workable_cooldown_clear:
        blockers.append("workable_cooldown")
    if not external.workable_policy_approved:
        blockers.append("workable_policy_unapproved")
    if external.official_ats_boards_count == 0:
        blockers.append("ats_manifest_empty")
    if not external.tfnsw_configured:
        blockers.append("tfnsw_unconfigured")
    if seven_day_status != "passed":
        blockers.append("seven_day_observation_pending")
    if not manual_audit_confirmed:
        blockers.append("manual_audit_unconfirmed")

    audit_sample = [
        {
            "opportunity_id": row["id"],
            "title": row.get("title"),
            "company": row.get("company"),
            "source_url": row.get("source_url"),
            "suburb": row.get("suburb"),
            "has_exact_coords": row.get("lat") is not None and row.get("lng") is not None,
        }
        for row in verified_rows[:20]
    ]

    return PilotScorecard(
        recent_verified_count=len(recent_rows),
        recent_commute_le_45_count=recent_commute_le_45,
        recent_explicit_modality_count=recent_explicit_modality,
        exact_coords_count=exact_coords_count,
        suburb_only_count=suburb_only_count,
        zero_unverified_promoted=zero_unverified_promoted,
        observation_days=observation_days,
        seven_day_series=series,
        first_run_passed=first_run_passed,
        seven_day_status=seven_day_status,
        manual_audit_confirmed=manual_audit_confirmed,
        audit_summary=audit_summary,
        external_gates=external,
        blockers=tuple(blockers),
        audit_sample=audit_sample,
    )


def export_pilot_audit_template(db_path: Path, out_path: Path) -> list[dict]:
    """Export current verified_job IDs and source URLs for 100% per-listing manual review."""
    rows = CatalogRepository(Database(db_path)).list(verification_state="verified_job", limit=2000)
    template = [
        {
            "opportunity_id": row["id"],
            "title": row.get("title"),
            "company": row.get("company"),
            "source_url": row.get("source_url"),
            "link_status": "valid",
            "title_company_match": True,
            "evidence_checked": True,
            "collision_checked": True,
            "collision_verdict": "distinct",
            "promoted_lead_or_generic": False,
            "checked_at": "",
        }
        for row in rows
    ]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(template, indent=2, ensure_ascii=False), encoding="utf-8")
    return template


def confirm_pilot_audit(
    state_dir: Path,
    db_path: Path,
    reports_dir: Path,
    boards_path: Path,
    policies_path: Path,
    *,
    audit_records_path: Path,
    now: datetime | None = None,
    env: Mapping[str, str] | None = None,
) -> dict:
    """Validate 100% per-listing audit records before persisting `manual_audit_confirmed=True`."""
    scorecard = evaluate_pilot_gates(
        db_path, state_dir, reports_dir, boards_path, policies_path, now=now, env=env
    )
    if not scorecard.first_run_passed:
        raise ValueError(f"first_run_targets_not_met: {', '.join(scorecard.blockers)}")

    verified_rows = CatalogRepository(Database(db_path)).list(verification_state="verified_job", limit=2000)
    expected_map = {str(r["id"]): str(r["source_url"]) for r in verified_rows}
    if not expected_map:
        raise ValueError("no_verified_jobs_present")

    raw_records = json.loads(Path(audit_records_path).read_text(encoding="utf-8"))
    if not isinstance(raw_records, list):
        raise ValueError("audit_file_must_be_json_list")

    parsed = [ListingAuditRecord(**item) for item in raw_records]
    seen_ids: set[str] = set()
    valid_links = 0
    wrong_merges = 0
    promoted_leads = 0
    for rec in parsed:
        if rec.opportunity_id not in expected_map or rec.source_url != expected_map[rec.opportunity_id]:
            raise ValueError(f"audit_record_mismatch: {rec.opportunity_id}")
        if rec.opportunity_id in seen_ids:
            raise ValueError(f"duplicate_audit_record: {rec.opportunity_id}")
        if not (rec.title_company_match and rec.evidence_checked and rec.collision_checked):
            raise ValueError(f"incomplete_listing_checks: {rec.opportunity_id}")
        seen_ids.add(rec.opportunity_id)
        if rec.collision_verdict == "wrong_merge":
            wrong_merges += 1
        if rec.promoted_lead_or_generic:
            promoted_leads += 1
        if rec.link_status == "valid":
            valid_links += 1

    if seen_ids != set(expected_map):
        missing = sorted(set(expected_map) - seen_ids)
        raise ValueError(f"incomplete_audit_coverage: missing {len(missing)} verified_job records")
    if wrong_merges > 0:
        raise ValueError(f"wrong_merge_detected: {wrong_merges}")
    if promoted_leads > 0:
        raise ValueError(f"promoted_lead_or_generic_detected: {promoted_leads}")

    ratio = round(valid_links / len(parsed), 4)
    if ratio < 0.95:
        raise ValueError(f"link_validity_below_95_percent: {ratio}")

    payload = {
        "manual_audit_confirmed": True,
        "audited_count": len(parsed),
        "audited_ids": sorted(seen_ids),
        "valid_link_ratio": ratio,
        "wrong_merges": wrong_merges,
        "promoted_leads": promoted_leads,
        "confirmed_at": (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(),
    }
    Path(state_dir).mkdir(parents=True, exist_ok=True)
    gate_path = Path(state_dir) / "pilot_gate.json"
    tmp = gate_path.with_name(f"{gate_path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(gate_path)
    return payload
