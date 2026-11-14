"""Run the bounded, manual four-suburb employer website pilot."""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import httpx

from hub.discovery.models import DiscoveryBatch, SiteInspection
from hub.discovery.osm import discover_businesses
from hub.discovery.website import inspect_website, safe_website_client
from hub.settings import load_settings
from hub.storage.catalog import CatalogRepository
from hub.storage.db import Database, snapshot_catalog_for_pilot

SUBURBS = ('Rockdale', 'Kogarah', 'Hurstville', 'Mascot')
CATEGORIES = ('cafe', 'restaurant', 'bar', 'hotel', 'cleaning service', 'retail store')
_MAX_SITE_REQUESTS = 16  # Four pages, each with at most three redirects.


def run_pilot(suburbs: tuple[str, ...], categories: tuple[str, ...], *,
              discover: Callable[[str, str, int], DiscoveryBatch],
              inspect: Callable[[str], SiteInspection], catalog: CatalogRepository,
              max_requests: int, dry_run: bool) -> dict:
    started = time.monotonic()
    metrics = ('employer_leads', 'unverified_job_posts', 'verified_jobs', 'existing_url_duplicates',
               'reachable_source_links', 'unknown_link_checks', 'broken_source_links', 'new_records',
               'requests', 'errors')
    report = {key: 0 for key in metrics}
    report.update({'suburbs': {}, 'provider_cost_estimate': None, 'dry_run': dry_run,
                   'budget_exhausted': False, 'error_types': []})
    cost_known = True
    seen_site_urls: set[str] = set()
    for suburb in suburbs:
        counts = {key: 0 for key in metrics}
        report['suburbs'][suburb] = counts
        for category in categories:
            if report['requests'] >= max_requests:
                report['budget_exhausted'] = True
                break
            try:
                batch = discover(suburb, category, 10)
                counts['requests'] += batch.requests
                report['requests'] += batch.requests
                if batch.cost_estimate is None:
                    cost_known = False
                else:
                    report['provider_cost_estimate'] = (report['provider_cost_estimate'] or 0) + batch.cost_estimate
            except Exception as exc:
                counts['requests'] += 1
                report['requests'] += 1
                counts['errors'] += 1
                report['errors'] += 1
                report['error_types'].append({'suburb': suburb, 'category': category, 'type': type(exc).__name__})
                break
            for candidate in batch.candidates:
                if report['requests'] + _MAX_SITE_REQUESTS > max_requests:
                    report['budget_exhausted'] = True
                    break
                if candidate.website_url in seen_site_urls:
                    counts['existing_url_duplicates'] += 1
                    report['existing_url_duplicates'] += 1
                    continue
                seen_site_urls.add(candidate.website_url)
                try:
                    inspection = inspect(candidate.website_url)
                except Exception as exc:
                    inspection = SiteInspection('error', [], 1)
                    counts['errors'] += 1
                    report['errors'] += 1
                    report['error_types'].append({'suburb': suburb, 'category': category, 'type': type(exc).__name__})
                counts['requests'] += inspection.requests
                report['requests'] += inspection.requests
                link_metric = 'reachable_source_links' if inspection.outcome == 'ok' else 'unknown_link_checks'
                counts[link_metric] += 1
                report[link_metric] += 1
                for finding in inspection.findings if inspection.outcome == 'ok' else []:
                    field = {'employer_lead': 'employer_leads', 'unverified_job_post': 'unverified_job_posts',
                             'verified_job': 'verified_jobs'}[finding.verification_state]
                    counts[field] += 1
                    report[field] += 1
                if not dry_run and inspection.outcome == 'ok':
                    new_ids, existing_ids = catalog.upsert_site_inspection(candidate, inspection)
                    counts['new_records'] += len(new_ids)
                    report['new_records'] += len(new_ids)
                    counts['existing_url_duplicates'] += len(existing_ids)
                    report['existing_url_duplicates'] += len(existing_ids)
        if report['budget_exhausted']:
            break
    if not cost_known:
        report['provider_cost_estimate'] = None
    report['cost_per_verified_job'] = (report['provider_cost_estimate'] / report['verified_jobs']
                                       if report['provider_cost_estimate'] is not None and report['verified_jobs'] else None)
    report['elapsed_seconds'] = round(time.monotonic() - started, 3)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', help='Persist inspected results to the local catalog')
    parser.add_argument('--config', type=Path, default=Path(__file__).parent / 'config' / 'settings.yml')
    args = parser.parse_args()
    settings = load_settings(args.config)
    if not settings.employer_discovery.enabled:
        parser.error('Employer discovery is disabled in settings; no source requests sent')
    db_path = settings.resolve(settings.paths.db)
    catalog = CatalogRepository(Database(db_path))
    backup = snapshot_catalog_for_pilot(db_path) if args.live else None
    with httpx.Client() as source_client, safe_website_client() as site_client:
        report = run_pilot(SUBURBS, CATEGORIES,
                           discover=lambda suburb, category, limit: discover_businesses(
                               suburb, category, limit=limit, client=source_client),
                           inspect=lambda url: inspect_website(url, client=site_client, checked_at=time.time()),
                           catalog=catalog, max_requests=settings.employer_discovery.max_requests,
                           dry_run=not args.live)
    report['snapshot'] = str(backup) if backup else None
    report['run_at'] = datetime.now(timezone.utc).isoformat()
    report_dir = Path(__file__).parent / 'data' / 'pilot-reports'
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"employer-pilot-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
    print(json.dumps({'report': str(report_path), 'counts': {key: report[key] for key in (
        'employer_leads', 'unverified_job_posts', 'verified_jobs', 'errors', 'requests')}}, ensure_ascii=False))
    return 0 if report['errors'] == 0 else 2


if __name__ == '__main__':
    raise SystemExit(main())
