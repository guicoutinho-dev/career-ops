from datetime import datetime, timedelta, timezone
import sqlite3
from pathlib import Path

import pytest

from hub.discovery.published import ListingCheck, PublishedListing, SourcePolicy, is_recent
from hub.storage.catalog import CatalogRepository
from hub.storage.db import Database, migrate_published_catalog, restore_published_catalog_backup


NOW = datetime(2026, 11, 14, 12, tzinfo=timezone.utc)
POLICY = SourcePolicy('greenhouse', True, 'Greenhouse', 25)


def listing(url='https://job-boards.greenhouse.io/cafe/jobs/101', *, location='Rockdale NSW',
            external_id='101', published_at=NOW):
    return PublishedListing(
        source_id='greenhouse', external_id=external_id, source_url=url,
        title='Barista', company='Cafe Aurora', location=location,
        published_at=published_at, employment_type=None, description=None,
        lat=None, lng=None,
    )


def check(url='https://boards-api.greenhouse.io/v1/boards/cafe/jobs?content=true',
          *, outcome='ok', checked_at=None):
    return ListingCheck(outcome, checked_at or NOW.timestamp(), url, 'Barista — Rockdale NSW')


def repo(tmp_path):
    path = tmp_path / 'hub.db'
    db = Database(path)
    migrate_published_catalog(path)
    return CatalogRepository(db)


def test_published_listing_has_specific_evidence_and_unknown_fields(tmp_path):
    catalog = repo(tmp_path)
    opportunity_id = catalog.upsert_published_listing(listing(), check(), POLICY)
    job = catalog.list(verification_state='verified_job')[0]
    assert job['id'] == opportunity_id
    assert job['source_url'] == listing().source_url
    assert job['title'] == 'Barista' and job['company'] == 'Cafe Aurora'
    assert job['published_at'] == NOW.isoformat()
    assert job['last_verified_at'] == NOW.isoformat()
    assert job['pay_display'] is None and job['requirements'] is None
    assert job['experience_required'] is None and job['visa_status'] == 'unknown'
    assert any(e['source_type'] == 'job_listing' and e['supports_job'] and e['checked_at']
               for e in job['evidence'])


def test_source_identity_keeps_local_id_when_url_changes_and_preserves_evidence(tmp_path):
    catalog = repo(tmp_path)
    first = catalog.upsert_published_listing(listing(), check(), POLICY)
    second_url = 'https://job-boards.greenhouse.io/cafe/jobs/101-new'
    second = catalog.upsert_published_listing(listing(second_url), check(), POLICY)
    assert first == second
    assert catalog.list()[0]['source_url'] == second_url
    with catalog._db.connect() as conn:
        urls = {row[0] for row in conn.execute(
            'SELECT source_url FROM opportunity_evidence WHERE opportunity_id=?', (first,))}
    assert listing().source_url in urls and second_url in urls


def test_identical_company_and_title_at_distinct_locations_are_not_merged(tmp_path):
    catalog = repo(tmp_path)
    one = catalog.upsert_published_listing(listing(), check(), POLICY)
    two = catalog.upsert_published_listing(
        listing('https://job-boards.greenhouse.io/cafe/jobs/202', location='Mascot NSW', external_id='202'),
        check(), POLICY)
    assert one != two and len(catalog.list()) == 2


def test_publication_policy_and_failed_check_cannot_promote_or_close(tmp_path):
    catalog = repo(tmp_path)
    with pytest.raises(PermissionError):
        catalog.upsert_published_listing(listing(), check(), SourcePolicy('greenhouse', False, 'Greenhouse', 25))
    assert catalog.list() == []
    opportunity_id = catalog.upsert_published_listing(listing(), check(), POLICY)
    before = catalog.list()[0]
    assert catalog.upsert_published_listing(listing(), check(outcome='timeout'), POLICY) == opportunity_id
    after = catalog.list()[0]
    assert after['verification_state'] == 'verified_job'
    assert after['last_verified_at'] == before['last_verified_at']


def test_recent_requires_source_date_and_fresh_successful_check():
    assert is_recent(listing(), check(), NOW) is True
    assert is_recent(listing(published_at=None), check(), NOW) is False
    assert is_recent(listing(published_at=NOW - timedelta(days=31)), check(), NOW) is False
    assert is_recent(listing(), check(checked_at=(NOW - timedelta(hours=49)).timestamp()), NOW) is False
    assert is_recent(listing(), check(outcome='timeout'), NOW) is False


def test_explicit_v4_to_v5_migration_has_independent_verified_rollback(tmp_path):
    path = tmp_path / 'hub.db'
    db = Database(path)
    with db.connect() as conn:
        conn.execute("INSERT INTO employers(id,name) VALUES ('keep','Keep Cafe')")
    backup = migrate_published_catalog(path)
    assert backup.is_file()
    with sqlite3.connect(path) as conn:
        assert conn.execute('PRAGMA user_version').fetchone()[0] == 5
        assert {'published_source_keys', 'commute_observations'}.issubset(
            {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")})
        conn.execute("INSERT INTO employers(id,name) VALUES ('new','New Cafe')")
    safety = restore_published_catalog_backup(path, force=True)
    assert safety is not None and safety.is_file()
    with sqlite3.connect(path) as conn:
        assert conn.execute('PRAGMA user_version').fetchone()[0] == 4
        assert [r[0] for r in conn.execute('SELECT id FROM employers')] == ['keep']
        assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    with sqlite3.connect(path) as restored, sqlite3.connect(backup) as original:
        assert list(restored.iterdump()) == list(original.iterdump())


def test_manifest_write_failure_restores_v4_before_raising(tmp_path, monkeypatch):
    path = tmp_path / 'hub.db'
    Database(path)
    original_replace = Path.replace

    def fail_manifest_replace(self, target):
        if self.name.endswith('.published-v5-rollback.json.tmp'):
            raise OSError('simulated manifest failure')
        return original_replace(self, target)

    monkeypatch.setattr(Path, 'replace', fail_manifest_replace)
    with pytest.raises(OSError, match='simulated manifest failure'):
        migrate_published_catalog(path)
    with sqlite3.connect(path) as conn:
        assert conn.execute('PRAGMA user_version').fetchone()[0] == 4
        assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'


def test_recent_sql_filter_matches_in_memory_upper_bound_and_evidence(tmp_path):
    catalog = repo(tmp_path)
    future = datetime.now(timezone.utc) + timedelta(days=2)
    catalog.upsert_published_listing(listing(published_at=future, external_id='future'), check(), POLICY)
    no_ev = catalog.upsert_published_listing(
        listing('https://job-boards.greenhouse.io/cafe/jobs/noev', published_at=datetime.now(timezone.utc), external_id='noev'),
        check(), POLICY,
    )
    with catalog._db.connect() as conn:
        conn.execute("DELETE FROM opportunity_evidence WHERE opportunity_id=? AND source_type='job_listing'", (no_ev,))
    assert catalog.count(recent=True) == 0
    assert catalog.list(recent=True) == []
    assert all(not item['recent'] for item in catalog.list())


def test_published_upsert_rejects_adzuna_and_preserves_explicit_suburb_and_verbatim_url(tmp_path):
    catalog = repo(tmp_path)

    with pytest.raises(PermissionError):
        catalog.upsert_published_listing(
            PublishedListing(
                source_id='adzuna_au',
                external_id='az1',
                source_url='https://www.adzuna.com.au/details/1',
                title='Barista',
                company='Cafe',
                location='Rockdale NSW',
                published_at=NOW,
                employment_type=None,
                description=None,
                lat=None,
                lng=None,
            ),
            check('https://api.adzuna.com/1'),
            SourcePolicy('adzuna_au', True, 'Adzuna', 10),
        )

    # Explicit suburb & ats_provider extraction; generic 'Sydney NSW' keeps suburb=None.
    opp_rockdale = catalog.upsert_published_listing(
        PublishedListing(
            source_id='ats:greenhouse:acmecafe',
            external_id='101',
            source_url='https://job-boards.greenhouse.io/acmecafe/jobs/101',
            title='Barista',
            company='Acme Cafe',
            location='Rockdale NSW',
            published_at=NOW,
            employment_type='Casual',
            description=None,
            lat=None,
            lng=None,
        ),
        check('https://boards-api.greenhouse.io/v1/boards/acmecafe/jobs/101'),
        SourcePolicy('ats:greenhouse:acmecafe', True, 'Greenhouse', 25),
    )
    opp_generic = catalog.upsert_published_listing(
        PublishedListing(
            source_id='workable',
            external_id='W1',
            source_url='https://apply.workable.com/j/W1',
            title='Kitchen Hand',
            company='Harbour Diner',
            location='Sydney NSW',
            published_at=NOW,
            employment_type=None,
            description=None,
            lat=None,
            lng=None,
        ),
        check('https://www.workable.com/api/v2/accounts/harbour/jobs/W1'),
        SourcePolicy('workable', True, 'Jobs by Workable', 1),
    )

    items = {row['id']: row for row in catalog.list()}
    assert items[opp_rockdale]['suburb'] == 'Rockdale'
    assert items[opp_rockdale]['state'] == 'NSW'
    assert items[opp_rockdale]['ats_provider'] == 'greenhouse'

    assert items[opp_generic]['suburb'] is None
    assert items[opp_generic]['source_url'] == 'https://apply.workable.com/j/W1'
    assert any(
        e['source_type'] == 'source_feed'
        and e['source_url'] == 'https://www.workable.com/api/v2/accounts/harbour/jobs/W1'
        for e in items[opp_generic]['evidence']
    )

    # Failed recheck preserves verified_job and records failed check_outcome on source_feed.
    before_verified = items[opp_generic]['last_verified_at']
    catalog.upsert_published_listing(
        PublishedListing(
            source_id='workable',
            external_id='W1',
            source_url='https://apply.workable.com/j/W1',
            title='Kitchen Hand',
            company='Harbour Diner',
            location='Sydney NSW',
            published_at=NOW,
            employment_type=None,
            description=None,
            lat=None,
            lng=None,
        ),
        check('https://www.workable.com/api/v2/accounts/harbour/jobs/W1', outcome='timeout'),
        SourcePolicy('workable', True, 'Jobs by Workable', 1),
    )
    after_row = {row['id']: row for row in catalog.list()}[opp_generic]
    assert after_row['verification_state'] == 'verified_job'
    assert after_row['last_verified_at'] == before_verified
    assert any(
        e['source_type'] == 'source_feed' and e['check_outcome'] == 'timeout'
        for e in after_row['evidence']
    )
