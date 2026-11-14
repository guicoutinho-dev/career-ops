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
