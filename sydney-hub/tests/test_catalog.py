import json
from pathlib import Path

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
