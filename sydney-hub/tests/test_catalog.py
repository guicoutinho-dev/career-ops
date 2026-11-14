import json
from pathlib import Path

import pytest

from hub.storage.catalog import CatalogRepository
from hub.storage.db import Database
from hub.discovery.models import EmployerCandidate, SiteInspection, WebsiteFinding


def _candidate():
    return EmployerCandidate('https://cafe.example/', 'OpenStreetMap discovery', 'Rockdale', 'cafe', 'node/1')


def _inspection(*findings):
    return SiteInspection('ok', list(findings), 2)


def _finding(state, url, title=None):
    return WebsiteFinding(state, url, 'Cafe Aurora', title, 'Barista. Apply now.' if title else None, 123)


def test_site_lead_and_listing_upsert(tmp_path):
    catalog = CatalogRepository(Database(tmp_path / 'hub.db'))
    new_ids, existing_ids = catalog.upsert_site_inspection(_candidate(), _inspection(
        _finding('employer_lead', 'https://cafe.example/'),
        _finding('verified_job', 'https://cafe.example/careers/barista', 'Barista'),
    ))
    assert len(new_ids) == 2 and existing_ids == []
    by_state = {row['verification_state']: row for row in catalog.list()}
    assert by_state['employer_lead']['title'] is None
    job = by_state['verified_job']
    assert job['title'] == 'Barista' and job['source_url'] == 'https://cafe.example/careers/barista'
    assert job['company'] == 'Cafe Aurora' and job['visa_status'] == 'unknown'
    assert job['pay_display'] is None and job['requirements'] is None and job['published_at'] is None
    assert any(e['source_type'] == 'job_listing' and e['supports_job'] for e in job['evidence'])


def test_hiring_mention_stays_unverified(tmp_path):
    catalog = CatalogRepository(Database(tmp_path / 'hub.db'))
    catalog.upsert_site_inspection(_candidate(), _inspection(
        _finding('unverified_job_post', 'https://cafe.example/careers', 'Barista'),
    ))
    assert [row['verification_state'] for row in catalog.list()] == ['unverified_job_post']
    assert catalog.list(verification_state='verified_job') == []


def test_repeat_and_legacy_url_keep_id(tmp_path):
    catalog = CatalogRepository(Database(tmp_path / 'hub.db'))
    seed = tmp_path / 'jobs.json'
    seed.write_text(json.dumps([{'id': 'legacy-42', 'title': 'Barista', 'company': 'Cafe Aurora',
                                 'source': 'Legacy', 'url': 'https://cafe.example/careers/barista'}]))
    catalog.import_legacy_seed(seed)
    inspected = _inspection(_finding('verified_job', 'https://cafe.example/careers/barista', 'Barista'))
    assert catalog.upsert_site_inspection(_candidate(), inspected) == ([], ['legacy-42'])
    assert catalog.upsert_site_inspection(_candidate(), inspected) == ([], ['legacy-42'])
    row = catalog.list()[0]
    assert row['id'] == 'legacy-42' and row['verification_state'] == 'verified_job'
    assert len([e for e in row['evidence'] if e['source_type'] == 'job_listing']) == 1


def test_timeout_preserves_status(tmp_path):
    catalog = CatalogRepository(Database(tmp_path / 'hub.db'))
    inspected = _inspection(_finding('verified_job', 'https://cafe.example/careers/barista', 'Barista'))
    catalog.upsert_site_inspection(_candidate(), inspected)
    before = catalog.list()
    assert catalog.upsert_site_inspection(_candidate(), SiteInspection('timeout', [], 1)) == ([], [])
    assert catalog.list() == before


def test_discovered_metadata_persists_only_when_present(tmp_path):
    catalog = CatalogRepository(Database(tmp_path / 'hub.db'))
    with_coords = EmployerCandidate(
        'https://cafe.example/', 'OpenStreetMap discovery', 'Rockdale', 'cafe', 'node/1',
        business_name='Cafe Aurora', lat=-33.9542, lng=151.1403,
    )
    without_coords = EmployerCandidate(
        'https://nocords.example/', 'OpenStreetMap discovery', 'Mascot', 'bar', 'node/2',
    )
    catalog.upsert_site_inspection(with_coords, _inspection(WebsiteFinding(
        'employer_lead', 'https://cafe.example/', None, None, None, 123,
    )))
    catalog.upsert_site_inspection(without_coords, _inspection(WebsiteFinding(
        'employer_lead', 'https://nocords.example/', 'Bar No Coords', None, None, 124,
    )))
    by_url = {row['source_url']: row for row in catalog.list()}
    first = by_url['https://cafe.example/']
    assert first['company'] == 'Cafe Aurora'
    assert first['suburb'] == 'Rockdale' and first['category'] == 'cafe'
    assert first['lat'] == -33.9542 and first['lng'] == 151.1403
    second = by_url['https://nocords.example/']
    assert second['company'] == 'Bar No Coords'
    assert second['suburb'] == 'Mascot' and second['category'] == 'bar'
    assert second['lat'] is None and second['lng'] is None


def test_upsert_discovered_lead_preserves_verified_job_and_records_unverified_lead(tmp_path):
    catalog = CatalogRepository(Database(tmp_path / 'hub.db'))
    candidate = EmployerCandidate(
        'https://hotel.example/', 'OpenStreetMap discovery', 'Kogarah', 'hotel', 'node/9',
        business_name='Kogarah Inn', lat=-33.968, lng=151.135,
    )
    new_ids, existing_ids = catalog.upsert_discovered_lead(candidate, check_outcome='blocked', observed_at=200.0)
    assert len(new_ids) == 1 and existing_ids == []
    lead = catalog.list()[0]
    assert lead['verification_state'] == 'employer_lead'
    assert lead['company'] == 'Kogarah Inn' and lead['category'] == 'hotel'
    assert lead['lat'] == -33.968 and lead['lng'] == 151.135
    assert lead['evidence'][0]['checked_at'] is None and not lead['evidence'][0]['supports_job']

    # Repeat call keeps ID and avoids creating duplicates.
    assert catalog.upsert_discovered_lead(candidate, check_outcome='timeout', observed_at=201.0) == ([], new_ids)

    # Upgrade to verified_job, then verify a later failed check cannot overwrite or downgrade it.
    catalog.upsert_site_inspection(candidate, _inspection(
        WebsiteFinding('verified_job', 'https://hotel.example/', 'Kogarah Inn', 'Housekeeping', 'Apply now', 202.0),
    ))
    assert catalog.upsert_discovered_lead(candidate, check_outcome='timeout', observed_at=203.0) == ([], new_ids)
    verified = catalog.list(verification_state='verified_job')
    assert len(verified) == 1 and verified[0]['id'] == new_ids[0]


def test_legacy_seed_preserves_id_but_drops_unverified_claims(tmp_path):
    catalog = CatalogRepository(Database(tmp_path / 'hub.db'))
    seed = [{
        'id': 'old-1', 'title': 'Barista', 'company': 'Cafe A', 'source': 'Direct Employer',
        'url': 'https://cafe.example/careers', 'suburb': 'Rockdale', 'state': 'NSW',
        'pay_display': '$35/h', 'requirements': ['RSA'], 'whv_eligible': True,
        'employment_type': 'Casual', 'posted_date': '2026-11-12',
        'description': 'Confirmed open vacancy',
    }]
    path = tmp_path / 'jobs.json'
    path.write_text(json.dumps(seed), encoding='utf-8')
    assert catalog.import_legacy_seed(path) == 1
    assert catalog.import_legacy_seed(path) == 0
    item = catalog.list()[0]
    assert item['id'] == 'old-1'
    assert item['verification_state'] == 'employer_lead'
    assert item['title'] is None
    assert item['pay_display'] is None and item['requirements'] is None
    assert item['visa_status'] == 'unknown' and item['published_at'] is None
    assert item['employment_type'] is None and item['description'] is None
    assert item['evidence'][0]['supports_job'] is False


def test_generic_agency_jobs_page_is_an_employer_lead(tmp_path):
    catalog = CatalogRepository(Database(tmp_path / 'hub.db'))
    path = tmp_path / 'jobs.json'
    path.write_text(json.dumps([{'id':'agency','title':'Several roles combined','company':'Agency',
                                 'source':'Agency','url':'https://agency.example/jobs/'}]))
    catalog.import_legacy_seed(path)
    with catalog._db.connect() as conn:
        conn.execute("UPDATE opportunities SET verification_state='unverified_job_post',title='Several roles combined' WHERE id='agency'")
    catalog.import_legacy_seed(path)
    item = catalog.list()[0]
    assert item['verification_state'] == 'employer_lead'
    assert item['title'] is None


def test_posts_merge_with_legacy_id_and_keep_original_body(tmp_path):
    db = Database(tmp_path / 'hub.db')
    catalog = CatalogRepository(db)
    with db.connect() as conn:
        conn.execute("INSERT INTO posts(post_id,group_url,group_name,permalink,body,created_ts,first_seen_at,kind,classification) VALUES (?,?,?,?,?,?,?,?,?)", (
            'post-1', 'https://facebook.com/groups/a/', 'Hospitality Sydney',
            'https://facebook.com/groups/a/posts/1/', 'Cafe hiring barista in Kogarah', 100, 101,
            'job', '{"kind":"job","score":1}',
        ))
    path = tmp_path / 'jobs.json'
    path.write_text(json.dumps([{'id':'post-1','title':'Invented','source':'Facebook Group','url':'https://facebook.com/groups/a/posts/1/'}]))
    assert catalog.import_legacy_seed(path) == 1
    assert catalog.sync_posts() == 0
    assert catalog.sync_posts() == 0
    item = catalog.list()[0]
    assert item['verification_state'] == 'unverified_job_post'
    assert item['description'] == 'Cafe hiring barista in Kogarah'
    assert item['source_url'].endswith('/posts/1/')
    assert item['pay_display'] is None
    assert item['source_group_name'] == 'Hospitality Sydney'
    assert item['source_group_url'] == 'https://facebook.com/groups/a/'
    assert item['first_seen_at'] == 101
    assert {e['source_type'] for e in item['evidence']} == {'legacy_catalog', 'facebook_post'}


def test_local_hunt_post_is_only_an_employer_lead(tmp_path):
    db = Database(tmp_path / 'hub.db')
    catalog = CatalogRepository(db)
    with db.connect() as conn:
        conn.execute("INSERT INTO posts(post_id,group_url,group_name,permalink,body,created_ts,first_seen_at,kind,classification) VALUES (?,?,?,?,?,?,?,?,?)", (
            'job_local_cafe_barista', 'https://cafe.example/careers', 'Direct Hunt: Cafe Fixture',
            'https://cafe.example/careers', 'Invented vacancy and salary', 100, 101,
            'job', '{"kind":"job","score":0,"suburb":"Rockdale"}',
        ))
    catalog.sync_posts()
    item = catalog.list()[0]
    assert item['company'] == 'Cafe Fixture'
    assert item['suburb'] == 'Rockdale'
    assert item['verification_state'] == 'employer_lead'
    assert item['description'] is None and item['title'] is None


def test_filters_are_parameterized_and_unknowns_stay_unknown(tmp_path):
    catalog = CatalogRepository(Database(tmp_path / 'hub.db'))
    path = tmp_path / 'jobs.json'
    path.write_text(json.dumps([
        {'id':'one','title':'Barista','company':'Cafe','source':'Seek','url':'https://seek.example/job/1','suburb':'Rockdale'},
        {'id':'two','title':'Cleaner','company':'Hotel','source':'Direct Employer','url':'https://hotel.example','suburb':'Mascot'},
    ]))
    catalog.import_legacy_seed(path)
    assert [x['id'] for x in catalog.list(role='Barista', suburb='Rockdale')] == ['one']
    assert [x['id'] for x in catalog.list(verification_state='employer_lead')] == ['two']
    assert catalog.list(suburb="Rockdale' OR 1=1 --") == []
    assert catalog.list(employment_type='Casual') == []


def test_verified_label_requires_job_supporting_evidence(tmp_path):
    db = Database(tmp_path / 'hub.db')
    catalog = CatalogRepository(db)
    path = tmp_path / 'jobs.json'
    path.write_text(json.dumps([{'id':'one','title':'Barista','source':'Seek','url':'https://example.com/job/1'}]))
    catalog.import_legacy_seed(path)
    with db.connect() as conn:
        conn.execute("UPDATE opportunities SET verification_state='verified_job' WHERE id='one'")
    assert catalog.list()[0]['verification_state'] == 'unverified_job_post'
    assert catalog.list(verification_state='verified_job') == []
    with db.connect() as conn:
        conn.execute("UPDATE opportunity_evidence SET supports_job=1 WHERE opportunity_id='one'")
    assert catalog.list(verification_state='verified_job') == []
    with db.connect() as conn:
        conn.execute("INSERT INTO opportunity_evidence(opportunity_id,source_url,source_type,observed_at,checked_at,supports_job) VALUES (?,?,?,?,?,?)", (
            'one', 'https://example.com/job/1', 'job_listing', 10, 11, 1,
        ))
    verified = catalog.list(verification_state='verified_job')[0]
    assert verified['id'] == 'one'
    assert verified['last_verified_at'] == '1970-01-01T00:00:11+00:00'


def test_legacy_local_hunt_cannot_run_or_ingest(tmp_path):
    import subprocess
    import sys

    script = Path(__file__).resolve().parents[2] / 'scripts' / 'hunt_local_venues.py'
    result = subprocess.run([sys.executable, str(script), '--ingest'], cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode != 0
    assert 'invented vacancies' in result.stderr
    assert list(tmp_path.iterdir()) == []


def test_location_and_transit_filter_only_use_observed_commute(tmp_path):
    db = Database(tmp_path / 'hub.db')
    catalog = CatalogRepository(db)
    path = tmp_path / 'jobs.json'
    path.write_text(json.dumps([
        {'id':'one','title':'Barista','source':'Seek','url':'https://example.com/1','suburb':'Rockdale','location':'Rockdale, NSW'},
        {'id':'two','title':'Cleaner','source':'Seek','url':'https://example.com/2','suburb':'Mascot','location':'Mascot, NSW'},
    ]))
    catalog.import_legacy_seed(path)
    with db.connect() as conn:
        conn.execute("INSERT INTO opportunity_commutes(opportunity_id,origin_suburb,transit_minutes,checked_at,source_url) VALUES (?,?,?,?,?)", (
            'one', 'Kogarah', 18, 100, 'https://example.com/route/1',
        ))
    assert [x['id'] for x in catalog.list(location='Rockdale')] == ['one']
    assert [x['id'] for x in catalog.list(origin_suburb='Kogarah', max_commute_minutes=20)] == ['one']
    assert catalog.list(origin_suburb='Kogarah', max_commute_minutes=15) == []


def test_timed_commute_ignores_undated_legacy_values_and_unknown_routes(tmp_path):
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo

    from hub.discovery.transit import CommuteResult
    from hub.storage.db import migrate_published_catalog

    path = tmp_path / 'hub.db'
    db = Database(path)
    catalog = CatalogRepository(db)
    seed = tmp_path / 'jobs.json'
    seed.write_text(json.dumps([{'id': 'one', 'title': 'Barista', 'source': 'Seek',
                                'url': 'https://example.com/1', 'location': 'Rockdale NSW'}]))
    catalog.import_legacy_seed(seed)
    with db.connect() as conn:
        conn.execute("UPDATE opportunities SET lat=-33.923,lng=151.187 WHERE id='one'")
        conn.execute("INSERT INTO opportunity_commutes(opportunity_id,origin_suburb,transit_minutes,checked_at,source_url) VALUES (?,?,?,?,?)",
                     ('one', 'Rockdale Station', 18, 100, 'https://example.com/old'))
    migrate_published_catalog(path)
    assert catalog.list(origin_suburb='Rockdale Station', commute_window='tuesday_0900', max_commute_minutes=45) == []
    departure = datetime(2026, 11, 17, 9, 0, tzinfo=ZoneInfo('Australia/Sydney'))
    with pytest.raises(ValueError, match='window'):
        catalog.record_commute_observation(
            'one', 'Rockdale Station', 'friday_1800', departure,
            CommuteResult(40, 'https://api.transport.nsw.gov.au/v1/tp/trip', 1001, 'ok'))
    assert catalog.record_commute_observation(
        'one', 'Rockdale Station', 'tuesday_0900', departure,
        CommuteResult(None, 'https://api.transport.nsw.gov.au/v1/tp/trip', 1000, 'no_route')) is False
    current_check = datetime.now(timezone.utc).timestamp()
    assert catalog.record_commute_observation(
        'one', 'Rockdale Station', 'tuesday_0900', departure,
        CommuteResult(40, 'https://api.transport.nsw.gov.au/v1/tp/trip', current_check, 'ok')) is True
    assert [item['id'] for item in catalog.list(origin_suburb='Rockdale Station', commute_window='tuesday_0900', max_commute_minutes=45)] == ['one']
    assert catalog.list(origin_suburb='Rockdale Station', commute_window='friday_1800', max_commute_minutes=45) == []
    with db.connect() as conn:
        conn.execute("UPDATE commute_observations SET checked_at=1000 WHERE opportunity_id='one'")
    assert catalog.list(origin_suburb='Rockdale Station', commute_window='tuesday_0900', max_commute_minutes=45) == []
    assert [item['id'] for item in catalog.list(origin_suburb='Rockdale Station', commute_window='tuesday_0900', commute_unknown=True)] == ['one']


def test_http_to_https_www_redirect_reuses_lead_id_and_keeps_description_clean(tmp_path):
    from hub.discovery.models import EmployerCandidate, SiteInspection, WebsiteFinding

    repo = CatalogRepository(Database(tmp_path / "hub.db"))
    lead = EmployerCandidate("http://cafe.example/", "OpenStreetMap discovery", "Rockdale", "cafe", business_name="Cafe One")
    new_ids, _ = repo.upsert_discovered_lead(lead, check_outcome="timeout", observed_at=10.0)
    row = repo.list()[0]
    assert row["description"] is None
    assert row["discovery_source"] == "OpenStreetMap discovery"
    assert row["evidence"][0]["check_outcome"] == "timeout"

    inspection = SiteInspection("ok", [
        WebsiteFinding("verified_job", "https://www.cafe.example/", "Cafe One", "Barista", "Barista opening — Apply", 20.0),
    ], 1)
    upd_new, upd_existing = repo.upsert_site_inspection(lead, inspection)
    assert upd_new == [] and upd_existing == new_ids
    items = repo.list()
    assert len(items) == 1
    assert items[0]["discovery_source"] == "OpenStreetMap discovery"
    assert items[0]["last_verified_at"] is not None


def test_catalog_summary_and_v5_filters_preserve_v4_schema_compatibility(tmp_path):
    db_path = tmp_path / "hub_v4.db"
    db = Database(db_path)
    with db.connect() as conn:
        conn.execute("DROP TABLE IF EXISTS commute_observations")
        conn.execute("DROP TABLE IF EXISTS published_source_keys")
        conn.execute("PRAGMA user_version = 4")
        conn.execute("INSERT INTO employers(id, name) VALUES ('e1', 'Cafe V4')")
        conn.execute(
            "INSERT INTO opportunities(id, employer_id, title, suburb, source, source_url, verification_state, first_seen_at, pay_hourly_min) "
            "VALUES ('id-1', 'e1', 'Barista', 'Rockdale', 'Seek', 'https://v4.example/1', 'unverified_job_post', 50.0, NULL)"
        )
        conn.execute(
            "INSERT INTO opportunities(id, employer_id, title, suburb, source, source_url, verification_state, first_seen_at, pay_hourly_min) "
            "VALUES ('id-2', 'e1', 'Waiter', 'Mascot', 'Seek', 'https://v4.example/2', 'employer_lead', 40.0, 32.0)"
        )
    repo = CatalogRepository(db)
    assert [x["id"] for x in repo.list(pay_min=30.0)] == ["id-2"]
    assert [x["id"] for x in repo.list(pay_min=30.0, include_unknown_pay=True)] == ["id-1", "id-2"]
    summary = repo.summary()
    assert summary["total"] == 2
    assert summary["counts_by_state"] == {"verified_job": 0, "unverified_job_post": 1, "employer_lead": 1}
    assert summary["facets"]["suburbs"] == ["Mascot", "Rockdale"]
