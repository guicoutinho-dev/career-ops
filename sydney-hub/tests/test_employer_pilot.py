import httpx
import pytest
from pathlib import Path
from urllib.parse import parse_qs

from hub.discovery.osm import discover_businesses
from hub.settings import load_settings
from hub.discovery.models import DiscoveryBatch, EmployerCandidate, SiteInspection, WebsiteFinding
from hub.storage.catalog import CatalogRepository
from hub.storage.db import Database
from run_employer_pilot import run_pilot
from run_employer_pilot import main


def _client(payload, status=200):
    seen = []

    def respond(request):
        seen.append(request)
        return httpx.Response(status, json=payload)

    return httpx.Client(transport=httpx.MockTransport(respond)), seen


def test_osm_query_is_bounded_and_keeps_only_discovery_pointer():
    client, seen = _client({'elements': [{
        'type': 'node', 'id': 123, 'tags': {
            'website': 'https://cafe.example/', 'name': 'Unverified OSM Name', 'amenity': 'cafe',
            'addr:street': 'Unverified Street',
        },
    }]})
    batch = discover_businesses('Rockdale', 'cafe', limit=10, client=client)
    assert batch.requests == 1 and batch.cost_estimate == 0
    assert len(batch.candidates) == 1
    candidate = batch.candidates[0]
    assert (candidate.website_url, candidate.discovery_source, candidate.suburb,
            candidate.category, candidate.source_key) == (
        'https://cafe.example/', 'OpenStreetMap discovery', 'Rockdale', 'cafe', 'node/123'
    )
    assert not hasattr(candidate, 'name') and not hasattr(candidate, 'address')
    assert len(seen) == 1 and seen[0].method == 'POST'
    assert seen[0].url.host == 'overpass.private.coffee'
    query = parse_qs(seen[0].content.decode())['data'][0]
    assert '-33.9654123,151.1282155,-33.943146,151.1528745' in query
    assert 'out tags 10' in query
    assert 'SydneyStays' in seen[0].headers['user-agent']


def test_missing_website_is_skipped():
    client, _ = _client({'elements': [
        {'type': 'node', 'id': 1, 'tags': {'name': 'Cafe with no site'}},
        {'type': 'node', 'id': 2, 'tags': {'contact:website': 'https://valid.example/'}},
        {'type': 'node', 'id': 3, 'tags': {'website': 'javascript:alert(1)'}},
    ]})
    batch = discover_businesses('Kogarah', 'cafe', limit=10, client=client)
    assert [c.website_url for c in batch.candidates] == ['https://valid.example/']


def test_unknown_suburb_or_category_sends_no_request():
    client, seen = _client({'elements': []})
    with pytest.raises(ValueError):
        discover_businesses('Unknown', 'cafe', limit=10, client=client)
    with pytest.raises(ValueError):
        discover_businesses('Rockdale', 'factory', limit=10, client=client)
    assert seen == []


def test_rate_limit_failure_is_exposed_without_retry():
    client, seen = _client({'remark': 'rate limited'}, status=429)
    with pytest.raises(RuntimeError, match='429'):
        discover_businesses('Mascot', 'restaurant', limit=10, client=client)
    assert len(seen) == 1


def test_disabled_provider_is_default():
    settings = load_settings(Path(__file__).resolve().parents[1] / 'config' / 'settings.example.yml')
    assert settings.employer_discovery.enabled is False
    assert settings.employer_discovery.provider == 'osm_overpass'
    assert settings.employer_discovery.max_requests == 80


def test_four_suburbs_are_isolated(tmp_path):
    catalog = CatalogRepository(Database(tmp_path / 'hub.db'))
    visited = []

    def discover(suburb, category, limit):
        visited.append(suburb)
        if suburb == 'Kogarah':
            raise RuntimeError('provider unavailable')
        return DiscoveryBatch([EmployerCandidate(f'https://{suburb.lower()}.example/', 'OpenStreetMap discovery',
                                                 suburb, category)], 1, 0.0)

    def inspect(url):
        return SiteInspection('ok', [WebsiteFinding('employer_lead', url, 'Named on site', None, None, 123.0)], 1)

    report = run_pilot(('Rockdale', 'Kogarah', 'Hurstville', 'Mascot'), ('cafe', 'bar'),
                       discover=discover, inspect=inspect, catalog=catalog, max_requests=80, dry_run=False)
    assert visited == ['Rockdale', 'Rockdale', 'Kogarah', 'Hurstville', 'Hurstville', 'Mascot', 'Mascot']
    assert report['suburbs']['Kogarah']['errors'] == 1
    assert all(report['suburbs'][s]['employer_leads'] == 1 for s in ('Rockdale', 'Hurstville', 'Mascot'))
    assert len(catalog.list()) == 3


def test_repeated_run_counts_existing_urls(tmp_path):
    catalog = CatalogRepository(Database(tmp_path / 'hub.db'))
    candidate = EmployerCandidate('https://local.example/', 'OpenStreetMap discovery', 'Rockdale', 'cafe')
    batch = DiscoveryBatch([candidate], 1, 0.0)
    inspected = SiteInspection('ok', [WebsiteFinding('employer_lead', candidate.website_url, 'Local Cafe', None,
                                                     None, 123.0)], 1)
    kwargs = dict(discover=lambda *args: batch, inspect=lambda *args: inspected, catalog=catalog,
                  max_requests=80, dry_run=False)
    first = run_pilot(('Rockdale',), ('cafe',), **kwargs)
    second = run_pilot(('Rockdale',), ('cafe',), **kwargs)
    assert first['new_records'] == 1
    assert second['new_records'] == 0 and second['existing_url_duplicates'] == 1
    assert len(catalog.list()) == 1


def test_report_separates_unknown_links_and_cost(tmp_path):
    catalog = CatalogRepository(Database(tmp_path / 'hub.db'))
    candidate = EmployerCandidate('https://timeout.example/', 'OpenStreetMap discovery', 'Mascot', 'hotel')
    report = run_pilot(('Mascot',), ('hotel',),
                       discover=lambda *args: DiscoveryBatch([candidate], 1, None),
                       inspect=lambda *args: SiteInspection('timeout', [], 1),
                       catalog=catalog, max_requests=80, dry_run=False)
    assert report['provider_cost_estimate'] is None
    assert report['cost_per_verified_job'] is None
    assert report['unknown_link_checks'] == 1
    assert report['reachable_source_links'] == 0
    assert report['broken_source_links'] == 0
    assert catalog.list() == []


def test_partial_site_failure_does_not_count_or_persist_verified_job(tmp_path):
    catalog = CatalogRepository(Database(tmp_path / 'hub.db'))
    candidate = EmployerCandidate('https://partial.example/', 'OpenStreetMap discovery', 'Mascot', 'hotel')
    result = SiteInspection('timeout', [WebsiteFinding('verified_job', 'https://partial.example/jobs/barista',
                                                        'Partial Hotel', 'Barista', 'Apply now', 123.0)], 2)
    report = run_pilot(('Mascot',), ('hotel',),
                       discover=lambda *args: DiscoveryBatch([candidate], 1, 0.0),
                       inspect=lambda *args: result, catalog=catalog, max_requests=80, dry_run=False)
    assert report['verified_jobs'] == 0
    assert report['unknown_link_checks'] == 1
    assert catalog.list() == []


def test_dry_run_does_not_write(tmp_path):
    catalog = CatalogRepository(Database(tmp_path / 'hub.db'))
    candidate = EmployerCandidate('https://lead.example/', 'OpenStreetMap discovery', 'Rockdale', 'cafe')
    report = run_pilot(('Rockdale',), ('cafe',),
                       discover=lambda *args: DiscoveryBatch([candidate], 1, 0.0),
                       inspect=lambda url: SiteInspection('ok', [WebsiteFinding('employer_lead', url,
                                  'Cafe', None, None, 123.0)], 1),
                       catalog=catalog, max_requests=80, dry_run=True)
    assert report['employer_leads'] == 1
    assert catalog.list() == []


def test_disabled_provider_sends_no_request(tmp_path, monkeypatch):
    config = tmp_path / 'config' / 'settings.yml'
    config.parent.mkdir()
    config.write_text('schedule: []\nscrape: {script_path: unused}\n', encoding='utf-8')
    monkeypatch.setattr('sys.argv', ['run_employer_pilot.py', '--live', '--config', str(config)])
    with pytest.raises(SystemExit, match='2'):
        main()
    assert not (tmp_path / 'data' / 'hub.db').exists()


def test_pilot_tracks_coverage_missing_websites_and_persists_named_blocked_lead(tmp_path):
    catalog = CatalogRepository(Database(tmp_path / 'hub.db'))
    candidate = EmployerCandidate(
        'https://blocked-cafe.example/', 'OpenStreetMap discovery', 'Rockdale', 'cafe', 'node/55',
        business_name='Cafe Blocked', lat=-33.954, lng=151.140,
    )
    batch = DiscoveryBatch(
        [candidate], requests=0, cost_estimate=0.0,
        total_businesses=5, missing_websites=3, duplicate_websites=1,
    )
    report = run_pilot(
        ('Rockdale',), ('cafe',),
        discover=lambda *args: batch,
        inspect=lambda url: SiteInspection('blocked', [], 1),
        catalog=catalog, max_requests=80, dry_run=False,
    )
    assert report['businesses_discovered'] == 5
    assert report['missing_websites'] == 3
    assert report['source_duplicate_websites'] == 1
    assert report['businesses_with_websites'] == 1
    assert report['reachable_source_links'] == 0
    assert report['unknown_link_checks'] == 1
    assert report['failed_checks'] == 1
    assert report['failed_check_outcomes'] == {'blocked': 1}
    assert report['employer_leads'] == 1
    assert report['verified_jobs'] == 0
    assert report['new_records'] == 1
    rows = catalog.list()
    assert len(rows) == 1
    assert rows[0]['verification_state'] == 'employer_lead'
    assert rows[0]['company'] == 'Cafe Blocked'
    assert rows[0]['lat'] == -33.954 and rows[0]['lng'] == 151.140


def test_pilot_main_runs_osm_pbf_provider_from_config(tmp_path, monkeypatch):
    import sys
    vendor = Path(__file__).resolve().parents[1] / 'data' / 'vendor'
    if vendor.is_dir() and str(vendor) not in sys.path:
        sys.path.insert(0, str(vendor))
    osmium = pytest.importorskip('osmium')

    pbf = tmp_path / 'local.osm.pbf'
    writer = osmium.SimpleWriter(str(pbf))
    writer.add_node(osmium.osm.mutable.Node(
        id=901, location=(151.1403, -33.9542),
        tags={'amenity': 'cafe', 'name': 'Pilot Cafe', 'website': 'https://pilotcafe.example/'},
    ))
    writer.close()

    config = tmp_path / 'config' / 'settings.yml'
    config.parent.mkdir()
    config.write_text(
        'schedule: []\n'
        'scrape: {script_path: unused}\n'
        'paths: {db: data/hub.db}\n'
        'employer_discovery:\n'
        '  enabled: true\n'
        '  provider: osm_pbf\n'
        f'  pbf_path: "{pbf.as_posix()}"\n'
        '  max_requests: 100\n',
        encoding='utf-8',
    )
    report_dir = tmp_path / 'data' / 'pilot-reports'
    monkeypatch.setattr(
        'run_employer_pilot.inspect_website',
        lambda url, *, client, checked_at: SiteInspection(
            'ok',
            [WebsiteFinding('verified_job', f'{url}careers/barista', 'Pilot Cafe', 'Barista', 'Apply now', checked_at)],
            2,
        ),
    )
    monkeypatch.setattr(
        'sys.argv',
        ['run_employer_pilot.py', '--live', '--config', str(config), '--report-dir', str(report_dir)],
    )
    assert main() == 0
    catalog = CatalogRepository(Database(tmp_path / 'data' / 'hub.db'))
    rows = catalog.list(verification_state='verified_job')
    assert len(rows) == 1 and rows[0]['company'] == 'Pilot Cafe'
    assert len(list(report_dir.glob('employer-pilot-*.json'))) == 1


def test_track1_enrichment_converts_ats_jobs_keeps_unlocated_as_lead_and_zero_results_never_expire(tmp_path):
    from hub.discovery.ats_bridge import AtsFetchResult, AtsJob, AtsMatch
    from run_employer_pilot import run_track1_enrichment

    catalog = CatalogRepository(Database(tmp_path / 'hub.db'))
    hotel = EmployerCandidate('https://airport-hotel.example/', 'OpenStreetMap discovery', 'Mascot', 'hotel',
                              source_key='osm-1', business_name='Airport Hotel', lat=-33.928, lng=151.195)
    cafe = EmployerCandidate('https://zero-cafe.example/', 'OpenStreetMap discovery', 'Rockdale', 'cafe',
                             source_key='osm-2', business_name='Zero Cafe', lat=-33.954, lng=151.140)
    bistro = EmployerCandidate('https://blocked-bistro.example/', 'OpenStreetMap discovery', 'Kogarah', 'restaurant',
                               source_key='osm-3', business_name='Blocked Bistro', lat=-33.968, lng=151.135)
    broken = EmployerCandidate('https://broken-pub.example/', 'OpenStreetMap discovery', 'Hurstville', 'bar',
                               source_key='osm-4', business_name='Broken Pub', lat=-33.961, lng=151.101)

    for cand in (hotel, cafe, bistro, broken):
        catalog.upsert_discovered_lead(cand, check_outcome='ok', observed_at=100.0, checked_at=100.0)

    def fake_inspect(url: str):
        if 'airport-hotel' in url:
            return SiteInspection('ok', [WebsiteFinding('employer_lead', url, 'Airport Hotel', None, None, 200.0)], 2,
                                  outbound_urls=('https://careers.pageuppeople.com/999/cw/en/listing/',))
        if 'zero-cafe' in url:
            return SiteInspection('ok', [WebsiteFinding('employer_lead', url, 'Zero Cafe', None, None, 200.0)], 1,
                                  outbound_urls=('https://boards.greenhouse.io/zerocafe',
                                                 'https://jobs.dayforcehcm.com/en-US/zerocafe/CANDIDATEPORTAL'))
        if 'blocked-bistro' in url:
            return SiteInspection('blocked', [], 1)
        return SiteInspection('ok', [WebsiteFinding('employer_lead', url, 'Broken Pub', None, None, 200.0)], 1,
                              outbound_urls=('https://jobs.lever.co/brokenpub',))

    class FakeBridge:
        total_requests = 0

        def detect(self, urls, *, company=''):
            out = []
            for u in urls:
                if 'pageuppeople' in u:
                    out.append(AtsMatch(u, 'pageup', True, None))
                elif 'greenhouse' in u:
                    out.append(AtsMatch(u, 'greenhouse', True, None))
                elif 'lever.co' in u:
                    out.append(AtsMatch(u, 'lever', True, None))
                elif 'dayforcehcm' in u:
                    out.append(AtsMatch(u, None, False, 'dayforce'))
            return out

        def fetch(self, url, *, company, provider_id, timeout_s=20.0):
            self.total_requests += 1
            if provider_id == 'pageup':
                return AtsFetchResult(True, 'pageup', [
                    AtsJob('Room Attendant / Housekeeping', 'https://careers.pageuppeople.com/999/cw/en/job/1',
                           'Airport Hotel', 'Mascot NSW 2020', '<p>Clean hotel guest rooms.</p>', None),
                    AtsJob('Waiter', 'https://careers.pageuppeople.com/999/cw/en/job/2',
                           'Airport Hotel', 'Parramatta NSW', None, None),
                    AtsJob('Commercial Cleaner', 'https://careers.pageuppeople.com/999/cw/en/job/3',
                           'Airport Hotel', '', None, None),
                ], None, 1)
            if provider_id == 'greenhouse':
                return AtsFetchResult(True, 'greenhouse', [], None, 1)
            raise RuntimeError('Simulated adapter crash')

    report = run_track1_enrichment(
        [hotel, cafe, bistro, broken],
        inspect=fake_inspect,
        bridge=FakeBridge(),
        catalog=catalog,
        max_employers=60,
        priority='hospitality',
        dry_run=False,
        fetch_detail=lambda _u: '<p>Greater Sydney region</p>',
    )
    assert report['verified_jobs_from_ats'] == 1
    assert report['ats_jobs_dropped_outside_suburbs'] == 1
    assert report['ats_jobs_dropped_ambiguous_location'] == 1
    assert report['ats_detected_unsupported'] == {'dayforce': 1}
    assert report['errors'] == 1

    verified = catalog.list(verification_state='verified_job')
    assert len(verified) == 1
    job = verified[0]
    assert job['title'] == 'Room Attendant / Housekeeping'
    assert job['suburb'] == 'Mascot'
    assert job['source'] == 'PageUp'
    assert job['discovery_source'] == 'OpenStreetMap discovery'
    assert job['ats_provider'] == 'pageup'
    assert job['role_bucket'] == 'housekeeping'
    assert job['suburb_match_basis'] == 'explicit_job_location'
    assert job['last_verified_at'] is not None
    assert '[ats:' not in (job['description'] or '')
    assert all('[ats:' not in (ev.get('excerpt') or '') for ev in job['evidence'])

    leads = catalog.list(verification_state='employer_lead')
    assert len(leads) == 4


def test_track1_live_preserves_retail_store_rows_in_hub_db(tmp_path):
    from hub.discovery.priority import build_priority_queue
    from run_employer_pilot import run_track1_enrichment

    catalog = CatalogRepository(Database(tmp_path / 'hub.db'))
    retail = EmployerCandidate('https://optician.example/', 'OpenStreetMap discovery', 'Hurstville', 'retail store',
                               business_name='Vision Shop')
    cafe = EmployerCandidate('https://cafe.example/', 'OpenStreetMap discovery', 'Rockdale', 'cafe',
                             business_name='Rockdale Cafe')
    catalog.upsert_discovered_lead(retail, check_outcome='ok', observed_at=100.0, checked_at=100.0)
    catalog.upsert_discovered_lead(cafe, check_outcome='ok', observed_at=100.0, checked_at=100.0)

    queue = build_priority_queue([], catalog.list_employer_check_states(), max_employers=60, priority='hospitality')
    seen_urls = []
    report = run_track1_enrichment(
        queue.selected,
        queue_counts=queue.counts,
        inspect=lambda url: seen_urls.append(url) or SiteInspection('ok', [
            WebsiteFinding('employer_lead', url, 'Rockdale Cafe', None, None, 200.0),
        ], 1),
        catalog=catalog,
        max_employers=60,
        priority='hospitality',
        dry_run=False,
    )
    assert seen_urls == ['https://cafe.example/']
    assert report['retail_preserved_skipped'] == 1
    assert [row['source_url'] for row in catalog.list() if row['category'] == 'retail store'] == ['https://optician.example/']


def test_track1_offsite_redirect_not_marked_blocked_and_cross_suburb_ats_clears_osm_coords(tmp_path):
    from hub.discovery.ats_bridge import AtsFetchResult, AtsJob, AtsMatch
    from run_employer_pilot import run_track1_enrichment

    class _Bridge:
        def detect(self, urls, *, company=''):
            return [AtsMatch('https://careers.group.example/board', 'pageup', True, None)]

        def fetch(self, url, *, provider_id=None, company='', timeout_s=20.0):
            return AtsFetchResult(
                True,
                'pageup',
                [
                    AtsJob(
                        'Kitchen Hand',
                        'https://careers.group.example/job/55',
                        'Group Bistro',
                        'Rockdale NSW 2216',
                        'Work in Rockdale kitchen.',
                        None,
                    )
                ],
                None,
                1,
            )

    catalog = CatalogRepository(Database(tmp_path / 'hub.db'))
    cand = EmployerCandidate(
        'https://bistro.example/',
        'OpenStreetMap discovery',
        'Mascot',
        'restaurant',
        'node/99',
        'Group Bistro Mascot',
        -33.926,
        151.187,
    )
    report = run_track1_enrichment(
        [cand],
        inspect=lambda url: SiteInspection(
            'blocked',
            [],
            1,
            outbound_urls=('https://careers.group.example/board',),
        ),
        bridge=_Bridge(),
        catalog=catalog,
        max_employers=10,
        priority='hospitality',
        dry_run=False,
    )
    assert report['verified_jobs_from_ats'] == 1
    assert report['failed_checks_by_outcome'] == {}
    check_states = catalog.list_employer_check_states()
    assert all(row['check_outcome'] != 'blocked' for row in check_states)

    verified = catalog.list(verification_state='verified_job')
    assert len(verified) == 1
    assert verified[0]['suburb'] == 'Rockdale'
    assert verified[0]['lat'] is None and verified[0]['lng'] is None
