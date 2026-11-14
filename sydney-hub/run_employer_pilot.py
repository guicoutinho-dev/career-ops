"""Run the bounded, manual four-suburb employer website & Track 1 ATS pilot."""
from __future__ import annotations

import argparse
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import time
from typing import Callable
from urllib.parse import urlsplit

import httpx

from hub.discovery.ats_bridge import AtsBridge
from hub.discovery.ats_filter import classify_role_bucket, fetch_ats_detail_html, filter_ats_jobs
from hub.discovery.models import DiscoveryBatch, EmployerCandidate, SiteInspection, WebsiteFinding
from hub.discovery.osm import discover_businesses as discover_overpass_businesses
from hub.discovery.osm_pbf import index_pbf_businesses
from hub.discovery.priority import build_priority_queue
from hub.discovery.website import inspect_website, safe_website_client
from hub.settings import load_settings
from hub.storage.catalog import CatalogRepository
from hub.storage.db import Database, snapshot_catalog_for_pilot

SUBURBS = ("Rockdale", "Kogarah", "Hurstville", "Mascot")
CATEGORIES = ("cafe", "restaurant", "bar", "hotel", "cleaning service", "retail store")
_MAX_SITE_REQUESTS = 16  # Up to 6 pages + bounded redirects.

_ATS_DISPLAY_NAMES = {
    "pageup": "PageUp",
    "greenhouse": "Greenhouse",
    "lever": "Lever",
    "ashby": "Ashby",
    "smartrecruiters": "SmartRecruiters",
    "workday": "Workday",
    "bamboohr": "BambooHR",
    "teamtailor": "Teamtailor",
    "pinpoint": "Pinpoint",
    "breezy": "Breezy HR",
    "recruitee": "Recruitee",
}


def _provider_display_source(provider_id: str | None) -> str:
    if not provider_id:
        return "Official employer website"
    return _ATS_DISPLAY_NAMES.get(provider_id, provider_id.replace("-", " ").title())


def _read_db_check_states_readonly(db_path: Path) -> list[dict]:
    """Read OSM check states without modifying or migrating a v3/v4 DB during --dry-run."""
    if not db_path.is_file():
        return []
    uri = f"file:{db_path.resolve().as_posix()}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        opp_cols = {r[1] for r in conn.execute("PRAGMA table_info(opportunities)")}
        ev_cols = {r[1] for r in conn.execute("PRAGMA table_info(opportunity_evidence)")}
        disc_filter = (
            "(o.discovery_source = 'OpenStreetMap discovery' OR o.source = 'OpenStreetMap discovery')"
            if "discovery_source" in opp_cols
            else "o.source = 'OpenStreetMap discovery'"
        )
        outcome_col = "ev.check_outcome" if "check_outcome" in ev_cols else "NULL AS check_outcome"
        sql = (
            f"SELECT o.id, o.source_url, e.name AS company, o.suburb, o.category, o.lat, o.lng, "
            f"o.verification_state, ev.checked_at, ev.excerpt, {outcome_col} "
            "FROM opportunities o "
            "LEFT JOIN employers e ON e.id = o.employer_id "
            "LEFT JOIN opportunity_evidence ev ON ev.id = ("
            "  SELECT ev2.id FROM opportunity_evidence ev2 "
            "  WHERE ev2.opportunity_id = o.id AND ev2.source_type = 'employer_website' "
            "  ORDER BY ev2.observed_at DESC, ev2.id DESC LIMIT 1"
            ") "
            f"WHERE {disc_filter} "
            "ORDER BY o.first_seen_at DESC, o.id"
        )
        return [dict(row) for row in conn.execute(sql).fetchall()]


def run_pilot(
    suburbs: tuple[str, ...],
    categories: tuple[str, ...],
    *,
    discover: Callable[[str, str, int], DiscoveryBatch],
    inspect: Callable[[str], SiteInspection],
    catalog: CatalogRepository,
    max_requests: int,
    dry_run: bool,
) -> dict:
    started = time.monotonic()
    metrics = (
        "businesses_discovered", "missing_websites", "source_duplicate_websites", "businesses_with_websites",
        "employer_leads", "unverified_job_posts", "verified_jobs", "existing_url_duplicates",
        "reachable_source_links", "unknown_link_checks", "failed_checks", "broken_source_links",
        "new_records", "requests", "errors",
    )
    report = {key: 0 for key in metrics}
    report.update({
        "suburbs": {}, "provider_cost_estimate": None, "dry_run": dry_run,
        "budget_exhausted": False, "error_types": [], "failed_check_outcomes": {},
    })
    cost_known = True
    seen_site_urls: set[str] = set()

    def record_upsert(counts: dict, ids_pair: tuple[list[str], list[str]]) -> None:
        new_ids, existing_ids = ids_pair
        counts["new_records"] += len(new_ids)
        report["new_records"] += len(new_ids)
        counts["existing_url_duplicates"] += len(existing_ids)
        report["existing_url_duplicates"] += len(existing_ids)

    for suburb in suburbs:
        counts = {key: 0 for key in metrics}
        report["suburbs"][suburb] = counts
        for category in categories:
            if report["requests"] >= max_requests:
                report["budget_exhausted"] = True
                break
            try:
                batch = discover(suburb, category, 10)
                counts["requests"] += batch.requests
                report["requests"] += batch.requests
                batch_total = (
                    batch.total_businesses
                    if batch.total_businesses is not None
                    else len(batch.candidates) + batch.missing_websites + batch.duplicate_websites
                )
                for target in (counts, report):
                    target["businesses_discovered"] += batch_total
                    target["missing_websites"] += batch.missing_websites
                    target["source_duplicate_websites"] += batch.duplicate_websites
                    target["businesses_with_websites"] += len(batch.candidates)
                if batch.cost_estimate is None:
                    cost_known = False
                else:
                    report["provider_cost_estimate"] = (report["provider_cost_estimate"] or 0) + batch.cost_estimate
            except Exception as exc:
                counts["requests"] += 1
                report["requests"] += 1
                counts["errors"] += 1
                report["errors"] += 1
                report["error_types"].append({"suburb": suburb, "category": category, "type": type(exc).__name__})
                break
            for candidate in batch.candidates:
                if not candidate.website_url:
                    continue
                if report["requests"] + _MAX_SITE_REQUESTS > max_requests:
                    report["budget_exhausted"] = True
                    break
                if candidate.website_url in seen_site_urls:
                    counts["existing_url_duplicates"] += 1
                    report["existing_url_duplicates"] += 1
                    continue
                seen_site_urls.add(candidate.website_url)
                try:
                    inspection = inspect(candidate.website_url)
                except Exception as exc:
                    inspection = SiteInspection("error", [], 1)
                    counts["errors"] += 1
                    report["errors"] += 1
                    report["error_types"].append({"suburb": suburb, "category": category, "type": type(exc).__name__})
                counts["requests"] += inspection.requests
                report["requests"] += inspection.requests
                link_metric = "reachable_source_links" if inspection.outcome == "ok" else "unknown_link_checks"
                counts[link_metric] += 1
                report[link_metric] += 1
                if inspection.outcome == "ok":
                    for finding in inspection.findings:
                        field = {
                            "employer_lead": "employer_leads",
                            "unverified_job_post": "unverified_job_posts",
                            "verified_job": "verified_jobs",
                        }[finding.verification_state]
                        counts[field] += 1
                        report[field] += 1
                    if not inspection.findings and candidate.business_name:
                        counts["employer_leads"] += 1
                        report["employer_leads"] += 1
                        if not dry_run:
                            record_upsert(counts, catalog.upsert_discovered_lead(
                                candidate, check_outcome="ok_no_title", observed_at=time.time()
                            ))
                    elif not dry_run:
                        record_upsert(counts, catalog.upsert_site_inspection(candidate, inspection))
                else:
                    counts["failed_checks"] += 1
                    report["failed_checks"] += 1
                    report["failed_check_outcomes"][inspection.outcome] = (
                        report["failed_check_outcomes"].get(inspection.outcome, 0) + 1
                    )
                    if candidate.business_name:
                        counts["employer_leads"] += 1
                        report["employer_leads"] += 1
                        if not dry_run:
                            record_upsert(counts, catalog.upsert_discovered_lead(
                                candidate, check_outcome=inspection.outcome, observed_at=time.time()
                            ))
        if report["budget_exhausted"]:
            break
    if not cost_known:
        report["provider_cost_estimate"] = None
    report["cost_per_verified_job"] = (
        report["provider_cost_estimate"] / report["verified_jobs"]
        if report["provider_cost_estimate"] is not None and report["verified_jobs"]
        else None
    )
    report["elapsed_seconds"] = round(time.monotonic() - started, 3)
    return report


def run_track1_enrichment(
    candidates: list[EmployerCandidate],
    *,
    inspect: Callable[[str], SiteInspection],
    catalog: CatalogRepository,
    bridge: AtsBridge | None = None,
    queue_counts: dict[str, int] | None = None,
    max_employers: int = 60,
    max_requests: int = 1000,
    priority: str = "hospitality",
    dry_run: bool = True,
    fetch_detail: Callable[[str], str | None] | None = None,
    site_client: httpx.Client | None = None,
) -> dict:
    started = time.monotonic()
    selected = list(candidates[:max_employers])
    qc = queue_counts or {}
    report: dict = {
        "mode": "track1",
        "priority": priority,
        "dry_run": dry_run,
        "employers_in_priority_queue": len(selected),
        "retail_preserved_skipped": int(qc.get("retail_preserved_skipped", 0)),
        "social_only_skipped": int(qc.get("social_only_skipped", 0)),
        "failed_history_skipped": int(qc.get("failed_history_skipped", 0)),
        "tier1_reachable": int(qc.get("tier1_reachable", 0)),
        "tier2_fresh_osm": int(qc.get("tier2_fresh_osm", 0)),
        "root_and_internal_pages_checked": 0,
        "career_pages_found": 0,
        "ats_detected_by_provider": {},
        "ats_detected_unsupported": {},
        "ats_jobs_total_returned": 0,
        "ats_jobs_kept_target_suburbs": 0,
        "ats_jobs_dropped_outside_suburbs": 0,
        "ats_jobs_dropped_ambiguous_location": 0,
        "ats_jobs_dropped_non_target_role": 0,
        "verified_jobs_from_website": 0,
        "verified_jobs_from_ats": 0,
        "verified_jobs": 0,
        "unverified_job_posts": 0,
        "employer_leads_preserved": 0,
        "failed_checks_by_outcome": {},
        "new_records": 0,
        "existing_url_duplicates": 0,
        "requests_python": 0,
        "requests_ats_bridge": 0,
        "errors": 0,
        "error_types": [],
        "budget_exhausted": False,
        "provider_cost_estimate": 0.0,
        "suburbs": {
            suburb: {
                "employers_checked": 0,
                "verified_jobs": 0,
                "unverified_job_posts": 0,
                "employer_leads_preserved": 0,
            }
            for suburb in SUBURBS
        },
    }

    def record_upsert(ids_pair: tuple[list[str], list[str]]) -> None:
        new_ids, existing_ids = ids_pair
        report["new_records"] += len(new_ids)
        report["existing_url_duplicates"] += len(existing_ids)

    seen_ats_boards: set[tuple[str, str, str]] = set()

    for candidate in selected:
        if report["requests_python"] + report["requests_ats_bridge"] >= max_requests:
            report["budget_exhausted"] = True
            break
        suburb_bucket = report["suburbs"].setdefault(
            candidate.suburb,
            {"employers_checked": 0, "verified_jobs": 0, "unverified_job_posts": 0, "employer_leads_preserved": 0},
        )
        suburb_bucket["employers_checked"] += 1
        now = time.time()

        try:
            inspection = inspect(candidate.website_url)
        except Exception as exc:
            report["errors"] += 1
            report["error_types"].append({"suburb": candidate.suburb, "url": candidate.website_url, "type": type(exc).__name__})
            inspection = SiteInspection("error", [], 1)

        report["requests_python"] += inspection.requests
        report["root_and_internal_pages_checked"] += max(1, inspection.requests)

        if inspection.outcome == "ok":
            if inspection.outbound_urls:
                report["career_pages_found"] += 1
            if not inspection.findings:
                report["employer_leads_preserved"] += 1
                suburb_bucket["employer_leads_preserved"] += 1
                if not dry_run and candidate.business_name:
                    record_upsert(catalog.upsert_discovered_lead(
                        candidate, check_outcome="ok_no_title", observed_at=now, checked_at=now
                    ))
            else:
                enriched_findings: list[WebsiteFinding] = []
                for finding in inspection.findings:
                    if finding.verification_state == "verified_job":
                        report["verified_jobs_from_website"] += 1
                        report["verified_jobs"] += 1
                        suburb_bucket["verified_jobs"] += 1
                        enriched_findings.append(
                            replace(
                                finding,
                                listing_source=finding.listing_source or "Official employer website",
                                ats_provider=finding.ats_provider,
                                role_bucket=finding.role_bucket or classify_role_bucket(finding.title or "", finding.excerpt),
                                suburb_match_basis=finding.suburb_match_basis or "employer_website",
                                discovery_source=candidate.discovery_source,
                            )
                        )
                    else:
                        if finding.verification_state == "unverified_job_post":
                            report["unverified_job_posts"] += 1
                            suburb_bucket["unverified_job_posts"] += 1
                        else:
                            report["employer_leads_preserved"] += 1
                            suburb_bucket["employer_leads_preserved"] += 1
                        enriched_findings.append(
                            replace(finding, discovery_source=candidate.discovery_source)
                        )
                if not dry_run:
                    record_upsert(catalog.upsert_site_inspection(
                        candidate,
                        SiteInspection("ok", enriched_findings, inspection.requests, inspection.outbound_urls),
                    ))
        elif inspection.outcome == "blocked" and inspection.outbound_urls:
            # An offsite redirect to a verified public ATS/career URL is reachable, not a WAF block.
            report["career_pages_found"] += 1
            report["employer_leads_preserved"] += 1
            suburb_bucket["employer_leads_preserved"] += 1
            if not dry_run and candidate.business_name:
                record_upsert(catalog.upsert_discovered_lead(
                    candidate, check_outcome="ok_no_title", observed_at=now, checked_at=now
                ))
        else:
            report["failed_checks_by_outcome"][inspection.outcome] = (
                report["failed_checks_by_outcome"].get(inspection.outcome, 0) + 1
            )
            if candidate.business_name:
                report["employer_leads_preserved"] += 1
                suburb_bucket["employer_leads_preserved"] += 1
                if not dry_run:
                    record_upsert(catalog.upsert_discovered_lead(
                        candidate, check_outcome=inspection.outcome, observed_at=now
                    ))

        if bridge is not None and inspection.outbound_urls:
            try:
                matches = bridge.detect(list(inspection.outbound_urls), company=candidate.business_name or "")
            except Exception as exc:
                report["errors"] += 1
                report["error_types"].append({"suburb": candidate.suburb, "stage": "ats_detect", "type": type(exc).__name__})
                matches = []

            for match in matches:
                if not match.supported or not match.provider_id:
                    if match.unsupported_vendor:
                        report["ats_detected_unsupported"][match.unsupported_vendor] = (
                            report["ats_detected_unsupported"].get(match.unsupported_vendor, 0) + 1
                        )
                    continue

                board_key = (candidate.suburb, match.provider_id, match.url.split("#", 1)[0].rstrip("/"))
                if board_key in seen_ats_boards:
                    continue
                seen_ats_boards.add(board_key)
                report["ats_detected_by_provider"][match.provider_id] = (
                    report["ats_detected_by_provider"].get(match.provider_id, 0) + 1
                )

                try:
                    fetch_res = bridge.fetch(
                        match.url,
                        company=candidate.business_name or "",
                        provider_id=match.provider_id,
                        timeout_s=20.0,
                    )
                except Exception as exc:
                    report["errors"] += 1
                    report["error_types"].append({
                        "suburb": candidate.suburb,
                        "provider": match.provider_id,
                        "stage": "ats_fetch",
                        "type": type(exc).__name__,
                    })
                    continue

                report["requests_ats_bridge"] += fetch_res.requests
                if not fetch_res.ok:
                    report["errors"] += 1
                    report["error_types"].append({
                        "suburb": candidate.suburb,
                        "provider": match.provider_id,
                        "stage": "ats_fetch",
                        "error": fetch_res.error,
                    })
                    continue

                report["ats_jobs_total_returned"] += len(fetch_res.jobs)
                board_host = urlsplit(match.url).hostname or ""
                detail_fn = fetch_detail
                if detail_fn is None and site_client is not None:
                    detail_fn = lambda detail_url, h=board_host: fetch_ats_detail_html(
                        detail_url, allowlisted_host=h, client=site_client
                    )
                summary = filter_ats_jobs(
                    fetch_res.jobs,
                    expected_suburb=candidate.suburb,
                    allowlisted_host=board_host,
                    fetch_detail=detail_fn,
                    max_detail_requests=10,
                )
                report["requests_python"] += summary.detail_requests
                report["ats_jobs_dropped_outside_suburbs"] += summary.dropped_outside_suburbs
                report["ats_jobs_dropped_ambiguous_location"] += summary.dropped_ambiguous_location
                report["ats_jobs_dropped_non_target_role"] += summary.dropped_non_target_role
                report["ats_jobs_kept_target_suburbs"] += len(summary.kept)

                for item in summary.kept:
                    report["verified_jobs_from_ats"] += 1
                    report["verified_jobs"] += 1
                    item_bucket = report["suburbs"].setdefault(
                        item.matched_suburb,
                        {"employers_checked": 0, "verified_jobs": 0, "unverified_job_posts": 0, "employer_leads_preserved": 0},
                    )
                    item_bucket["verified_jobs"] += 1
                    if not dry_run:
                        verified_now = time.time()
                        excerpt = (item.location_evidence or (item.job.description or "")[:240] or item.job.title)[:400]
                        same_suburb = item.matched_suburb == candidate.suburb
                        ats_candidate = replace(
                            candidate,
                            suburb=item.matched_suburb,
                            lat=candidate.lat if same_suburb else None,
                            lng=candidate.lng if same_suburb else None,
                        )
                        finding = WebsiteFinding(
                            verification_state="verified_job",
                            source_url=item.job.url,
                            employer_name=item.job.company or candidate.business_name,
                            title=item.job.title,
                            excerpt=excerpt,
                            checked_at=verified_now,
                            listing_source=_provider_display_source(match.provider_id),
                            ats_provider=match.provider_id,
                            role_bucket=item.role_bucket,
                            suburb_match_basis=item.match_basis,
                            discovery_source=candidate.discovery_source,
                        )
                        record_upsert(catalog.upsert_site_inspection(
                            ats_candidate,
                            SiteInspection("ok", [finding], fetch_res.requests),
                        ))

    report["elapsed_seconds"] = round(time.monotonic() - started, 3)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Persist inspected results to the local catalog")
    parser.add_argument("--config", type=Path, default=Path(__file__).parent / "config" / "settings.yml")
    parser.add_argument("--limit", type=int, default=None, help="Optional per-category candidate cap for PBF mode")
    parser.add_argument("--priority", choices=("hospitality", "all"), default="hospitality")
    parser.add_argument("--max-employers", type=int, default=60)
    parser.add_argument("--report-dir", type=Path, default=Path(__file__).parent / "data" / "pilot-reports")
    args = parser.parse_args()
    settings = load_settings(args.config)
    if not settings.employer_discovery.enabled:
        parser.error("Employer discovery is disabled in settings; no source requests sent")
    db_path = settings.resolve(settings.paths.db)
    backup = None
    if args.live:
        catalog = CatalogRepository(Database(db_path))
        backup = snapshot_catalog_for_pilot(db_path)
    else:
        catalog = CatalogRepository(Database(db_path)) if not db_path.is_file() else None  # dry-run avoids migrating existing DB

    with httpx.Client() as source_client, safe_website_client() as site_client:
        if settings.employer_discovery.provider == "osm_pbf":
            indexed = index_pbf_businesses(
                settings.resolve(settings.employer_discovery.pbf_path),
                suburbs=SUBURBS,
                categories=CATEGORIES,
            )
            pbf_candidates = [
                cand
                for suburb in SUBURBS
                for category in CATEGORIES
                for cand in indexed[(suburb, category)].candidates[: (args.limit if args.limit else None)]
            ]
            db_states = catalog.list_employer_check_states() if catalog is not None else _read_db_check_states_readonly(db_path)
            queue = build_priority_queue(
                pbf_candidates,
                db_states,
                max_employers=args.max_employers,
                priority=args.priority,
            )
            providers_dir = Path(__file__).resolve().parents[1] / "providers"
            bridge = AtsBridge(providers_dir) if providers_dir.is_dir() else None
            report = run_track1_enrichment(
                queue.selected,
                inspect=lambda url: inspect_website(url, client=site_client, checked_at=time.time()),
                bridge=bridge,
                catalog=catalog if catalog is not None else CatalogRepository(Database(args.report_dir / "_dry_run_unused.db")),
                queue_counts=queue.counts,
                max_employers=args.max_employers,
                max_requests=settings.employer_discovery.max_requests,
                priority=args.priority,
                dry_run=not args.live,
                site_client=site_client,
            )
            unused_dry_db = args.report_dir / "_dry_run_unused.db"
            if unused_dry_db.exists():
                unused_dry_db.unlink(missing_ok=True)
        else:
            discover_fn = lambda suburb, category, limit: discover_overpass_businesses(
                suburb, category, limit=args.limit or limit, client=source_client
            )
            report = run_pilot(
                SUBURBS,
                CATEGORIES,
                discover=discover_fn,
                inspect=lambda url: inspect_website(url, client=site_client, checked_at=time.time()),
                catalog=catalog if catalog is not None else CatalogRepository(Database(db_path)),
                max_requests=settings.employer_discovery.max_requests,
                dry_run=not args.live,
            )

    report["provider"] = settings.employer_discovery.provider
    report["snapshot"] = str(backup) if backup else None
    report["run_at"] = datetime.now(timezone.utc).isoformat()
    args.report_dir.mkdir(parents=True, exist_ok=True)
    report_path = args.report_dir / f"employer-pilot-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    summary_keys = (
        ("employers_in_priority_queue", "career_pages_found", "ats_jobs_total_returned",
         "ats_jobs_kept_target_suburbs", "verified_jobs_from_website", "verified_jobs_from_ats",
         "verified_jobs", "employer_leads_preserved", "errors", "requests_python", "requests_ats_bridge")
        if report.get("mode") == "track1"
        else ("businesses_discovered", "missing_websites", "businesses_with_websites",
              "reachable_source_links", "failed_checks", "employer_leads",
              "unverified_job_posts", "verified_jobs", "errors", "requests")
    )
    print(json.dumps({
        "report": str(report_path),
        "counts": {key: report.get(key, 0) for key in summary_keys},
    }, ensure_ascii=False))
    return 0 if report["errors"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
