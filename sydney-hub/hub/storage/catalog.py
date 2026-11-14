"""Persist source observations without promoting legacy claims into verified jobs."""
from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from hub.discovery.models import EmployerCandidate, SiteInspection

from hub.storage.db import Database


def _http_url(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    parsed = urlparse(value.strip())
    return value.strip() if parsed.scheme in ('http', 'https') and parsed.netloc else None


def _url_key(url: str) -> str:
    parsed = urlparse(url)
    return parsed._replace(scheme=parsed.scheme.lower(), netloc=parsed.netloc.lower(),
                           path=parsed.path.rstrip('/') or '/', fragment='').geturl()


class CatalogRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def upsert_site_inspection(self, candidate: EmployerCandidate, inspection: SiteInspection) -> tuple[list[str], list[str]]:
        if inspection.outcome != 'ok' or not inspection.findings:
            return [], []
        new_ids: list[str] = []
        existing_ids: list[str] = []
        with self._db.connect() as conn:
            known = {_url_key(row['source_url']): row['id'] for row in conn.execute(
                'SELECT id,source_url FROM opportunities ORDER BY first_seen_at DESC'
            )}
            for finding in inspection.findings:
                url = _http_url(finding.source_url)
                name = (finding.employer_name or '').strip()
                if not url or not name:
                    continue
                key = _url_key(url)
                opportunity_id = known.get(key)
                employer_id = hashlib.sha256(_url_key(candidate.website_url).encode()).hexdigest()[:20]
                conn.execute('INSERT OR IGNORE INTO employers(id,name,website) VALUES (?,?,?)',
                             (employer_id, name, candidate.website_url))
                if opportunity_id is None:
                    opportunity_id = 'site_' + hashlib.sha256(key.encode()).hexdigest()[:20]
                    conn.execute(
                        'INSERT INTO opportunities(id,employer_id,title,source,source_url,verification_state,first_seen_at,description) '
                        'VALUES (?,?,?,?,?,?,?,?)',
                        (opportunity_id, employer_id, finding.title if finding.verification_state != 'employer_lead' else None,
                         candidate.discovery_source, url, finding.verification_state, finding.checked_at, finding.excerpt),
                    )
                    known[key] = opportunity_id
                    new_ids.append(opportunity_id)
                else:
                    existing_ids.append(opportunity_id)
                    conn.execute(
                        'UPDATE opportunities SET employer_id=?,title=?,source=?,source_url=?,verification_state=?,description=? '
                        "WHERE id=? AND verification_state!='verified_job'",
                        (employer_id, finding.title if finding.verification_state != 'employer_lead' else None,
                         candidate.discovery_source, url, finding.verification_state, finding.excerpt, opportunity_id),
                    )
                source_type = 'job_listing' if finding.verification_state == 'verified_job' else 'employer_website'
                supports_job = int(finding.verification_state == 'verified_job')
                conn.execute(
                    'INSERT INTO opportunity_evidence(opportunity_id,source_url,source_type,observed_at,checked_at,excerpt,supports_job) '
                    'VALUES (?,?,?,?,?,?,?) ON CONFLICT(opportunity_id,source_url,source_type) DO UPDATE SET '
                    'checked_at=excluded.checked_at,excerpt=excluded.excerpt,supports_job=excluded.supports_job',
                    (opportunity_id, url, source_type, finding.checked_at, finding.checked_at, finding.excerpt, supports_job),
                )
        return new_ids, existing_ids

    def import_legacy_seed(self, path: Path) -> int:
        if not path.is_file():
            return 0
        records = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(records, list):
            raise ValueError('Legacy jobs seed must be a list')
        added = 0
        with self._db.connect() as conn:
            for job in records:
                if not isinstance(job, dict) or not isinstance(job.get('id'), str):
                    continue
                url = _http_url(job.get('url'))
                if not url:
                    continue
                path_part = urlparse(url).path.rstrip('/').lower()
                lead = job.get('source') == 'Direct Employer' or path_part.rsplit('/', 1)[-1] in (
                    '', 'jobs', 'careers', 'career', 'vacancies', 'employment', 'work-with-us',
                )
                name = job.get('company') if isinstance(job.get('company'), str) else None
                employer_id = self._employer(conn, name)
                cur = conn.execute(
                    'INSERT OR IGNORE INTO opportunities(id,employer_id,title,location,suburb,state,source,source_url,verification_state,first_seen_at) '
                    'VALUES (?,?,?,?,?,?,?,?,?,?)',
                    (job['id'], employer_id, None if lead else job.get('title'), job.get('location'),
                     job.get('suburb'), job.get('state'), job.get('source') or 'Legacy catalog', url,
                     'employer_lead' if lead else 'unverified_job_post', time.time()),
                )
                if cur.rowcount:
                    added += 1
                    conn.execute(
                        'INSERT OR IGNORE INTO opportunity_evidence(opportunity_id,source_url,source_type,observed_at,supports_job) '
                        'VALUES (?,?,?,?,0)', (job['id'], url, 'legacy_catalog', time.time()),
                    )
                if lead:
                    conn.execute(
                        "UPDATE opportunities SET verification_state='employer_lead',title=NULL "
                        "WHERE id=? AND verification_state='unverified_job_post' AND source NOT IN ('Facebook Group','Local business discovery')",
                        (job['id'],),
                    )
        return added

    def sync_posts(self) -> int:
        added = 0
        with self._db.connect() as conn:
            rows = conn.execute("SELECT * FROM posts WHERE kind='job'").fetchall()
            for row in rows:
                url = _http_url(row['permalink'])
                if not url:
                    continue
                lead = row['post_id'].startswith('job_local_') or (row['group_name'] or '').startswith('Direct Hunt:')
                employer_id = self._employer(conn, (row['group_name'] or '').removeprefix('Direct Hunt: ').strip()) if lead else None
                try:
                    details = json.loads(row['classification'])
                except (TypeError, ValueError):
                    details = {}
                suburb = details.get('suburb') if isinstance(details, dict) and isinstance(details.get('suburb'), str) else None
                cur = conn.execute(
                    'INSERT OR IGNORE INTO opportunities(id,employer_id,title,suburb,source,source_url,source_group_name,source_group_url,verification_state,first_seen_at,published_at,description) '
                    'VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                    (row['post_id'], employer_id, None, suburb, 'Local business discovery' if lead else 'Facebook Group', url,
                     None if lead else row['group_name'], None if lead else row['group_url'],
                     'employer_lead' if lead else 'unverified_job_post', row['first_seen_at'],
                     None if lead else datetime.fromtimestamp(row['created_ts'], timezone.utc).isoformat(),
                     None if lead else row['body']),
                )
                if cur.rowcount:
                    added += 1
                conn.execute(
                    'UPDATE opportunities SET source=?,source_url=?,source_group_name=?,source_group_url=?, '
                    'first_seen_at=?,published_at=?,description=?,verification_state=?,title=NULL, '
                    'suburb=COALESCE(suburb,?),employer_id=CASE WHEN ? THEN COALESCE(employer_id,?) ELSE NULL END '
                    "WHERE id=? AND verification_state!='verified_job'",
                    ('Local business discovery' if lead else 'Facebook Group', url,
                     None if lead else row['group_name'], None if lead else row['group_url'],
                     row['first_seen_at'], None if lead else datetime.fromtimestamp(row['created_ts'], timezone.utc).isoformat(),
                     None if lead else row['body'], 'employer_lead' if lead else 'unverified_job_post',
                     suburb, int(lead), employer_id, row['post_id']),
                )
                conn.execute(
                    'INSERT OR IGNORE INTO opportunity_evidence(opportunity_id,source_url,source_type,observed_at,supports_job) '
                    'VALUES (?,?,?,?,0)', (row['post_id'], url, 'local_discovery' if lead else 'facebook_post', row['first_seen_at']),
                )
        return added

    @staticmethod
    def _employer(conn, name: str | None) -> str | None:
        if not name or not name.strip():
            return None
        name = name.strip()
        employer_id = hashlib.sha256(name.casefold().encode()).hexdigest()[:20]
        conn.execute('INSERT OR IGNORE INTO employers(id,name) VALUES (?,?)', (employer_id, name))
        return employer_id

    def list(self, *, role: str | None = None, suburb: str | None = None, location: str | None = None,
             verification_state: str | None = None, source: str | None = None,
             employment_type: str | None = None, experience: str | None = None,
             requirement: str | None = None, origin_suburb: str | None = None,
             max_commute_minutes: int | None = None, limit: int = 500) -> list[dict]:
        where, params = [], []
        for column, value in (('o.suburb', suburb), ('o.verification_state', verification_state),
                              ('o.source', source), ('o.employment_type', employment_type),
                              ('o.experience_required', experience)):
            if value:
                where.append(f'{column} = ?')
                params.append(value)
        if verification_state == 'verified_job':
            where.append("EXISTS (SELECT 1 FROM opportunity_evidence v WHERE v.opportunity_id=o.id "
                         "AND v.source_type='job_listing' AND v.supports_job=1 AND v.checked_at IS NOT NULL)")
        if role:
            where.append("o.title LIKE ? ESCAPE '\\'")
            params.append('%' + role.replace('%', '\\%').replace('_', '\\_') + '%')
        if requirement:
            where.append("o.requirements LIKE ? ESCAPE '\\'")
            params.append('%' + requirement.replace('%', '\\%').replace('_', '\\_') + '%')
        if location:
            where.append("(o.location LIKE ? ESCAPE '\\' OR o.suburb LIKE ? ESCAPE '\\')")
            term = '%' + location.replace('%', '\\%').replace('_', '\\_') + '%'
            params.extend((term, term))
        if max_commute_minutes is not None:
            if not origin_suburb:
                raise ValueError('origin_suburb is required for a commute filter')
            where.append('EXISTS (SELECT 1 FROM opportunity_commutes c WHERE c.opportunity_id=o.id '
                         'AND c.origin_suburb=? AND c.transit_minutes<=?)')
            params.extend((origin_suburb, max_commute_minutes))
        sql = ('SELECT o.*, e.name AS company, e.website AS employer_website FROM opportunities o '
               'LEFT JOIN employers e ON e.id=o.employer_id ')
        if where:
            sql += 'WHERE ' + ' AND '.join(where) + ' '
        sql += 'ORDER BY o.first_seen_at DESC, o.id LIMIT ?'
        with self._db.connect() as conn:
            rows = conn.execute(sql, [*params, limit]).fetchall()
            items = []
            for row in rows:
                item = dict(row)
                item['url'] = item['source_url']
                item['evidence'] = [{**dict(e), 'supports_job': bool(e['supports_job'])} for e in conn.execute(
                    'SELECT source_url,source_type,observed_at,checked_at,supports_job FROM opportunity_evidence WHERE opportunity_id=?',
                    (item['id'],),
                )]
                if item['verification_state'] == 'verified_job':
                    checked = [e['checked_at'] for e in item['evidence']
                               if e['supports_job'] and e['source_type'] == 'job_listing' and e['checked_at'] is not None]
                    if checked:
                        item['last_verified_at'] = datetime.fromtimestamp(max(checked), timezone.utc).isoformat()
                    else:
                        item['verification_state'] = 'unverified_job_post'
                        item['last_verified_at'] = None
                items.append(item)
            return items
