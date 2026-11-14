"""Serve the built UI with an isolated local API for browser checks."""
import sys
import tempfile
import json
import time
from pathlib import Path

import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hub.api.app import AppDeps, create_app
from hub.storage.db import Database
from hub.storage.catalog import CatalogRepository
from hub.discovery.models import EmployerCandidate, SiteInspection, WebsiteFinding
from hub.discovery.published import ListingCheck, PublishedListing, SourcePolicy
from hub.storage.db import migrate_published_catalog
from datetime import datetime, timezone, timedelta
from hub.storage.groups import GroupRepository
from hub.storage.posts import PostRepository
from hub.storage.runs import RunRepository
from web.build_dashboard import build as build_dashboard
from tests.test_api import FakeRunner, FakeScheduler


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as temp:
        db = Database(Path(temp) / "hub.db")
        catalog = CatalogRepository(db)
        seed = Path(temp) / 'jobs.json'
        seed.write_text(json.dumps([
            {'id': 'legacy-barista', 'title': 'Barista', 'company': 'Cafe Fixture', 'source': 'Seek',
             'url': 'https://example.com/jobs/barista', 'suburb': 'Rockdale', 'location': 'Rockdale, NSW', 'state': 'NSW',
             'pay_display': '$99/h', 'whv_eligible': True},
            {'id': 'legacy-lead', 'title': 'Cleaner', 'company': 'Hotel Fixture', 'source': 'Direct Employer',
             'url': 'https://example.com/hotel', 'suburb': 'Mascot', 'location': 'Mascot, NSW', 'state': 'NSW'},
        ]), encoding='utf-8')
        catalog.import_legacy_seed(seed)
        stale_ts = int(time.time()) - 20 * 86400
        fresh_ts = int(time.time()) - 3600
        catalog.upsert_site_inspection(
            EmployerCandidate('https://cafe.example/', 'OpenStreetMap discovery', 'Rockdale', 'cafe', 'node/1', 'Cafe Website', -33.9542, 151.1403),
            SiteInspection('ok', [
                WebsiteFinding('employer_lead', 'https://cafe.example/', 'Cafe Website', None, None, stale_ts),
                WebsiteFinding('verified_job', 'https://cafe.example/careers/barista', 'Cafe Website',
                               'Barista', 'Barista — apply online', stale_ts),
            ], 2),
        )
        catalog.upsert_site_inspection(
            EmployerCandidate('https://hotel.example.com.au/', 'OpenStreetMap discovery', 'Mascot', 'hotel', 'node/2', 'Harbour Hotel', -33.926, 151.187),
            SiteInspection('ok', [
                WebsiteFinding(
                    'verified_job', 'https://careers.harbour.example/job/101', 'Harbour Hotel',
                    'Housekeeper', 'Housekeeper in Mascot NSW.', fresh_ts,
                    listing_source='PageUp', ats_provider='pageup',
                    role_bucket='hospitality_cleaning', suburb_match_basis='listing_location',
                    discovery_source='OpenStreetMap discovery',
                ),
            ], 1),
        )
        migrate_published_catalog(Path(temp) / "hub.db")
        published = PublishedListing(
            'workable', 'PUB1', 'https://apply.workable.com/fixture/j/PUB1/',
            'Bar Staff', 'Mascot Bar', 'Mascot NSW', datetime.now(timezone.utc) - timedelta(days=1),
            'Casual', None, None, None,
        )
        pub_id = catalog.upsert_published_listing(
            published, ListingCheck('ok', time.time(), published.source_url, 'Bar Staff application'),
            SourcePolicy('workable', True, 'Workable'), role_bucket='bartender',
        )
        with db.connect() as conn:
            conn.execute(
                "UPDATE opportunities SET requirements=?, pay_display=?, pay_hourly_min=? WHERE id=?",
                ('RSA; RCG', '$31/h', 31.0, pub_id),
            )
        catalog.sync_posts()
        web_dir = Path(__file__).resolve().parents[1] / 'web'
        build_dashboard(web_dir)
        port = int(sys.argv[1]) if len(sys.argv) > 1 else 8767
        deps = AppDeps(
            groups=GroupRepository(db), posts=PostRepository(db), runs=RunRepository(db),
            catalog=catalog,
            runner=FakeRunner(), scheduler=FakeScheduler(),
            web_dir=web_dir,
            allowed_hosts={f"127.0.0.1:{port}", f"localhost:{port}"},
        )
        uvicorn.run(create_app(deps), host="127.0.0.1", port=port, log_level="warning")
