"""Persist source observations without promoting legacy claims into verified jobs."""
from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from hub.discovery.ats_filter import _match_pilot_suburb
from hub.discovery.expiry import (
    CONSECUTIVE_MISS_EXPIRY_THRESHOLD,
    STALE_AFTER_DAYS,
    is_explicit_expiry,
)
from hub.discovery.models import EmployerCandidate, SiteInspection
from hub.discovery.published import ListingCheck, PublishedListing, SourcePolicy
from hub.discovery.transit import CommuteResult
from hub.domain.community_extract import extract_community_post

from hub.storage.db import Database

_COMMUTE_MAX_AGE_SECONDS = 7 * 86400
_GENERIC_LOCALITY_TOKENS = {"sydney", "greater sydney", "sydney metro", "sydney cbd", "nsw", "new south wales", "australia"}
_EXPLICIT_NSW_SUBURB_RE = re.compile(r"(?:^|,\s*)([A-Za-z][A-Za-z\s'-]{1,40}),?\s+NSW(?:\s+\d{4})?\b", re.I)


def _extract_explicit_suburb_state(location: str | None) -> tuple[str | None, str | None, str | None]:
    raw = (location or "").strip()
    if not raw or raw.casefold() in _GENERIC_LOCALITY_TOKENS:
        return None, None, None
    pilot = _match_pilot_suburb(raw)
    if pilot is not None:
        return pilot[0], "NSW", "explicit_suburb"
    m = _EXPLICIT_NSW_SUBURB_RE.search(raw)
    if m:
        candidate = " ".join(m.group(1).split())
        if candidate.casefold() not in _GENERIC_LOCALITY_TOKENS:
            return candidate, "NSW", "explicit_suburb"
    return None, None, None


def _extract_ats_provider(source_id: str) -> str | None:
    if source_id.startswith("ats:"):
        parts = source_id.split(":")
        if len(parts) >= 2 and parts[1]:
            return parts[1]
    return None


def _http_url(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    parsed = urlparse(value.strip())
    return value.strip() if parsed.scheme in ('http', 'https') and parsed.netloc else None


def _url_key(url: str) -> str:
    parsed = urlparse(url)
    scheme = 'https' if parsed.scheme.lower() in ('http', 'https') else parsed.scheme.lower()
    netloc = parsed.netloc.lower()
    if netloc.startswith('www.'):
        netloc = netloc[4:]
    return parsed._replace(scheme=scheme, netloc=netloc, path=parsed.path.rstrip('/') or '/', fragment='').geturl()


class CatalogRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def record_commute_observation(
        self, opportunity_id: str, origin_label: str, departure_window: str,
        departure_at: datetime, result: CommuteResult,
    ) -> bool:
        if result.outcome != 'ok' or result.minutes is None:
            return False
        if (departure_window not in {'tuesday_0900', 'friday_1800'} or not origin_label.strip()
                or departure_at.tzinfo is None or result.minutes < 0
                or result.source != 'https://api.transport.nsw.gov.au/v1/tp/trip'):
            raise ValueError('Invalid timed commute evidence')
        local_departure = departure_at.astimezone(ZoneInfo('Australia/Sydney'))
        if (local_departure.weekday(), local_departure.hour, local_departure.minute) != {
            'tuesday_0900': (1, 9, 0), 'friday_1800': (4, 18, 0),
        }[departure_window]:
            raise ValueError('Departure does not match commute window')
        with self._db.connect() as conn:
            if conn.execute('PRAGMA user_version').fetchone()[0] < 5:
                raise RuntimeError('Published catalog migration is required before writing commutes')
            row = conn.execute('SELECT lat,lng FROM opportunities WHERE id=?', (opportunity_id,)).fetchone()
            if row is None or row['lat'] is None or row['lng'] is None:
                raise ValueError('Exact opportunity destination is required')
            conn.execute(
                'INSERT INTO commute_observations(opportunity_id,origin_label,departure_window,departure_at,'
                'transit_minutes,checked_at,source_url) VALUES (?,?,?,?,?,?,?) '
                'ON CONFLICT(opportunity_id,origin_label,departure_window) DO UPDATE SET '
                'departure_at=excluded.departure_at,transit_minutes=excluded.transit_minutes,'
                'checked_at=excluded.checked_at,source_url=excluded.source_url '
                'WHERE excluded.checked_at>=commute_observations.checked_at',
                (opportunity_id, origin_label, departure_window, departure_at.isoformat(),
                 result.minutes, result.checked_at, result.source),
            )
        return True

    def upsert_published_listing(
        self, listing: PublishedListing, check: ListingCheck, policy: SourcePolicy,
        role_bucket: str | None = None,
    ) -> str:
        if listing.source_id.startswith('adzuna') or policy.source_id.startswith('adzuna'):
            raise PermissionError(f'Adzuna catalog insertion is forbidden without commercial license: {listing.source_id}')
        if not policy.publish_allowed:
            raise PermissionError(f'Publishing is not allowed for {policy.source_id}')
        if policy.source_id != listing.source_id:
            raise ValueError('Source policy does not match listing')
        url = _http_url(listing.source_url)
        evidence_url = _http_url(check.evidence_url)
        title, company = listing.title.strip(), listing.company.strip()
        if not url or not evidence_url or not title or not company:
            raise ValueError('Published listing requires title, company, URL, and evidence URL')
        key = _url_key(url)
        external_id = (listing.external_id or key).strip()
        verified = check.outcome == 'ok'
        verified_at = datetime.fromtimestamp(check.checked_at, timezone.utc).isoformat() if verified else None
        published_at = listing.published_at.astimezone(timezone.utc).isoformat() if listing.published_at else None
        suburb, state, suburb_match_basis = _extract_explicit_suburb_state(listing.location)
        ats_provider = _extract_ats_provider(listing.source_id)
        with self._db.connect() as conn:
            if conn.execute('PRAGMA user_version').fetchone()[0] < 5:
                raise RuntimeError('Published catalog migration is required before writing listings')
            row = conn.execute(
                'SELECT opportunity_id FROM published_source_keys WHERE source_id=? AND external_id=?',
                (listing.source_id, external_id),
            ).fetchone()
            opportunity_id = row['opportunity_id'] if row else self._known_urls(conn).get(key)
            if opportunity_id and not verified:
                conn.execute(
                    'INSERT INTO opportunity_evidence('
                    'opportunity_id,source_url,source_type,observed_at,checked_at,excerpt,check_outcome,supports_job'
                    ') VALUES (?,?,?,?,?,?,?,0) ON CONFLICT(opportunity_id,source_url,source_type) DO UPDATE SET '
                    'observed_at=excluded.observed_at,check_outcome=excluded.check_outcome,excerpt=excluded.excerpt',
                    (opportunity_id, evidence_url, 'source_feed', check.checked_at, None, check.excerpt, check.outcome),
                )
                return opportunity_id

            employer_id = hashlib.sha256(('published:' + company.casefold()).encode()).hexdigest()[:20]
            conn.execute('INSERT OR IGNORE INTO employers(id,name) VALUES (?,?)', (employer_id, company))
            if opportunity_id is None:
                opportunity_id = 'published_' + hashlib.sha256(
                    f'{listing.source_id}:{external_id}'.encode()
                ).hexdigest()[:20]
                conn.execute(
                    'INSERT INTO opportunities('
                    'id,employer_id,title,location,suburb,state,source,source_url,verification_state,first_seen_at,'
                    'published_at,last_verified_at,description,employment_type,lat,lng,discovery_source,ats_provider,role_bucket,suburb_match_basis'
                    ') VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (opportunity_id, employer_id, title, listing.location, suburb, state, policy.attribution or listing.source_id,
                     url, 'verified_job' if verified else 'unverified_job_post', check.checked_at,
                     published_at, verified_at, listing.description, listing.employment_type,
                     listing.lat if listing.lng is not None else None,
                     listing.lng if listing.lat is not None else None, 'Published Jobs', ats_provider, role_bucket, suburb_match_basis),
                )
            elif verified:
                previous = conn.execute('SELECT lat,lng,location FROM opportunities WHERE id=?', (opportunity_id,)).fetchone()
                if previous and ((listing.location is not None and listing.location != previous['location'])
                                 or (listing.lat is not None and listing.lng is not None
                                     and (previous['lat'], previous['lng']) != (listing.lat, listing.lng))):
                    conn.execute('DELETE FROM commute_observations WHERE opportunity_id=?', (opportunity_id,))
                conn.execute(
                    'UPDATE opportunities SET employer_id=?,title=?,location=COALESCE(?,location),'
                    'suburb=COALESCE(?,suburb),state=COALESCE(?,state),'
                    'source=?,source_url=?,verification_state=?,published_at=COALESCE(?,published_at),'
                    'last_verified_at=?,description=COALESCE(?,description),'
                    'employment_type=COALESCE(?,employment_type),lat=COALESCE(?,lat),lng=COALESCE(?,lng),'
                    'discovery_source=COALESCE(discovery_source,?),ats_provider=COALESCE(?,ats_provider),'
                    'role_bucket=COALESCE(?,role_bucket),suburb_match_basis=COALESCE(?,suburb_match_basis) WHERE id=?',
                    (employer_id, title, listing.location, suburb, state, policy.attribution or listing.source_id, url,
                     'verified_job', published_at, verified_at, listing.description, listing.employment_type,
                     listing.lat, listing.lng, 'Published Jobs', ats_provider, role_bucket, suburb_match_basis, opportunity_id),
                )
            conn.execute(
                'INSERT INTO published_source_keys(source_id,external_id,opportunity_id) VALUES (?,?,?) '
                'ON CONFLICT(source_id,external_id) DO UPDATE SET opportunity_id=excluded.opportunity_id',
                (listing.source_id, external_id, opportunity_id),
            )
            conn.execute(
                'INSERT INTO opportunity_evidence('
                'opportunity_id,source_url,source_type,observed_at,checked_at,excerpt,check_outcome,supports_job'
                ') VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(opportunity_id,source_url,source_type) DO UPDATE SET '
                'observed_at=excluded.observed_at,checked_at=excluded.checked_at,excerpt=excluded.excerpt,'
                'check_outcome=excluded.check_outcome,supports_job=excluded.supports_job',
                (opportunity_id, url, 'job_listing', check.checked_at, check.checked_at if verified else None,
                 check.excerpt, check.outcome, int(verified)),
            )
            if evidence_url != url:
                conn.execute(
                    'INSERT INTO opportunity_evidence('
                    'opportunity_id,source_url,source_type,observed_at,checked_at,excerpt,check_outcome,supports_job'
                    ') VALUES (?,?,?,?,?,?,?,0) ON CONFLICT(opportunity_id,source_url,source_type) DO UPDATE SET '
                    'observed_at=excluded.observed_at,checked_at=excluded.checked_at,check_outcome=excluded.check_outcome',
                    (opportunity_id, evidence_url, 'source_feed', check.checked_at,
                     check.checked_at if verified else None, None, check.outcome),
                )
            return opportunity_id

    @staticmethod
    def _known_urls(conn) -> dict[str, str]:
        return {_url_key(row['source_url']): row['id'] for row in conn.execute(
            'SELECT id,source_url FROM opportunities ORDER BY first_seen_at DESC'
        )}

    @staticmethod
    def _candidate_employer(conn, candidate: EmployerCandidate, name: str) -> str:
        website = _http_url(candidate.website_url) or candidate.website_url or ''
        employer_key = _url_key(website) if website else f'name:{name.casefold()}'
        employer_id = hashlib.sha256(employer_key.encode()).hexdigest()[:20]
        conn.execute('INSERT OR IGNORE INTO employers(id,name,website) VALUES (?,?,?)',
                     (employer_id, name, _http_url(candidate.website_url)))
        return employer_id

    @staticmethod
    def _candidate_geo(candidate: EmployerCandidate) -> tuple[str | None, str | None, str | None, float | None, float | None]:
        suburb = (candidate.suburb or '').strip() or None
        state = 'NSW' if suburb else None
        category = (candidate.category or '').strip() or None
        lat = float(candidate.lat) if candidate.lat is not None and candidate.lng is not None else None
        lng = float(candidate.lng) if candidate.lat is not None and candidate.lng is not None else None
        return suburb, state, category, lat, lng

    def upsert_site_inspection(self, candidate: EmployerCandidate, inspection: SiteInspection) -> tuple[list[str], list[str]]:
        if inspection.outcome != 'ok' or not inspection.findings:
            return [], []
        new_ids: list[str] = []
        existing_ids: list[str] = []
        suburb, state, category, lat, lng = self._candidate_geo(candidate)
        with self._db.connect() as conn:
            known = self._known_urls(conn)
            for finding in inspection.findings:
                url = _http_url(finding.source_url)
                name = (finding.employer_name or candidate.business_name or '').strip()
                if not url or not name:
                    continue
                key = _url_key(url)
                opportunity_id = known.get(key)
                employer_id = self._candidate_employer(conn, candidate, name)
                title = finding.title if finding.verification_state != 'employer_lead' else None
                discovery_source = getattr(finding, 'discovery_source', None) or candidate.discovery_source
                source = getattr(finding, 'listing_source', None) or candidate.discovery_source
                ats_provider = getattr(finding, 'ats_provider', None)
                role_bucket = getattr(finding, 'role_bucket', None)
                suburb_match_basis = getattr(finding, 'suburb_match_basis', None)
                verified_iso = (
                    datetime.fromtimestamp(finding.checked_at, timezone.utc).isoformat()
                    if finding.verification_state == 'verified_job' and finding.checked_at is not None
                    else None
                )
                if opportunity_id is None:
                    opportunity_id = 'site_' + hashlib.sha256(key.encode()).hexdigest()[:20]
                    conn.execute(
                        'INSERT INTO opportunities(id,employer_id,title,suburb,state,source,source_url,verification_state,'
                        'first_seen_at,last_verified_at,description,category,lat,lng,discovery_source,ats_provider,role_bucket,suburb_match_basis) '
                        'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                        (opportunity_id, employer_id, title, suburb, state, source, url,
                         finding.verification_state, finding.checked_at, verified_iso, finding.excerpt, category, lat, lng,
                         discovery_source, ats_provider, role_bucket, suburb_match_basis),
                    )
                    known[key] = opportunity_id
                    new_ids.append(opportunity_id)
                else:
                    if opportunity_id not in new_ids and opportunity_id not in existing_ids:
                        existing_ids.append(opportunity_id)
                    conn.execute(
                        'UPDATE opportunities SET employer_id=?,title=?,suburb=COALESCE(suburb,?),state=COALESCE(state,?),'
                        'source=?,source_url=?,verification_state=?,last_verified_at=COALESCE(?,last_verified_at),'
                        'description=?,category=COALESCE(category,?),lat=COALESCE(lat,?),lng=COALESCE(lng,?),'
                        'discovery_source=COALESCE(discovery_source,?),ats_provider=COALESCE(?,ats_provider),'
                        'role_bucket=COALESCE(?,role_bucket),suburb_match_basis=COALESCE(?,suburb_match_basis) '
                        "WHERE id=? AND verification_state!='verified_job'",
                        (employer_id, title, suburb, state, source, url,
                         finding.verification_state, verified_iso, finding.excerpt, category, lat, lng,
                         discovery_source, ats_provider, role_bucket, suburb_match_basis, opportunity_id),
                    )
                    if verified_iso is not None:
                        conn.execute(
                            "UPDATE opportunities SET last_verified_at=? WHERE id=? AND verification_state='verified_job'",
                            (verified_iso, opportunity_id),
                        )
                source_type = 'job_listing' if finding.verification_state == 'verified_job' else 'employer_website'
                supports_job = int(finding.verification_state == 'verified_job')
                conn.execute(
                    'INSERT INTO opportunity_evidence(opportunity_id,source_url,source_type,observed_at,checked_at,excerpt,check_outcome,supports_job) '
                    'VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(opportunity_id,source_url,source_type) DO UPDATE SET '
                    'checked_at=excluded.checked_at,excerpt=excluded.excerpt,check_outcome=excluded.check_outcome,supports_job=excluded.supports_job',
                    (opportunity_id, url, source_type, finding.checked_at, finding.checked_at, finding.excerpt, inspection.outcome, supports_job),
                )
        return new_ids, existing_ids

    def upsert_discovered_lead(self, candidate: EmployerCandidate, *, check_outcome: str,
                               observed_at: float, checked_at: float | None = None) -> tuple[list[str], list[str]]:
        url = _http_url(candidate.website_url)
        name = (candidate.business_name or '').strip()
        if not url or not name:
            return [], []
        suburb, state, category, lat, lng = self._candidate_geo(candidate)
        key = _url_key(url)
        with self._db.connect() as conn:
            known = self._known_urls(conn)
            opportunity_id = known.get(key)
            employer_id = self._candidate_employer(conn, candidate, name)
            if opportunity_id is None:
                opportunity_id = 'site_' + hashlib.sha256(key.encode()).hexdigest()[:20]
                conn.execute(
                    'INSERT INTO opportunities(id,employer_id,title,suburb,state,source,source_url,verification_state,'
                    'first_seen_at,description,category,lat,lng,discovery_source) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (opportunity_id, employer_id, None, suburb, state, candidate.discovery_source, url,
                     'employer_lead', observed_at, None, category, lat, lng, candidate.discovery_source),
                )
                result = ([opportunity_id], [])
            else:
                conn.execute(
                    'UPDATE opportunities SET employer_id=COALESCE(employer_id,?),suburb=COALESCE(suburb,?),'
                    'state=COALESCE(state,?),category=COALESCE(category,?),lat=COALESCE(lat,?),lng=COALESCE(lng,?),'
                    'discovery_source=COALESCE(discovery_source,?) '
                    "WHERE id=? AND verification_state='employer_lead'",
                    (employer_id, suburb, state, category, lat, lng, candidate.discovery_source, opportunity_id),
                )
                result = ([], [opportunity_id])
            conn.execute(
                'INSERT INTO opportunity_evidence(opportunity_id,source_url,source_type,observed_at,checked_at,excerpt,check_outcome,supports_job) '
                'VALUES (?,?,?,?,?,?,?,0) ON CONFLICT(opportunity_id,source_url,source_type) DO UPDATE SET '
                'observed_at=excluded.observed_at,checked_at=COALESCE(excluded.checked_at,opportunity_evidence.checked_at),'
                'check_outcome=excluded.check_outcome',
                (opportunity_id, url, 'employer_website', observed_at, checked_at, None, check_outcome),
            )
            return result

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
            opp_cols = {row[1] for row in conn.execute('PRAGMA table_info(opportunities)')}
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
                fallback_suburb = details.get('suburb') if isinstance(details, dict) and isinstance(details.get('suburb'), str) else None
                ext = None if lead else extract_community_post(row['body'], created_ts=row['created_ts'])
                suburb = (ext.suburb if ext and ext.suburb else fallback_suburb)
                location = ext.location if ext else None
                title = ext.title if ext else None
                pay_display = ext.pay_display if ext else None
                pay_hourly_min = ext.pay_hourly_min if ext else None
                employment_type = ext.employment_type if ext else None
                requirements = json.dumps(ext.requirements) if ext and ext.requirements else None
                visa_status = 'whv_mentioned' if ext and ext.whv_eligible else 'unknown'
                role_bucket = ext.role_bucket if ext else None
                published_at = None if lead else datetime.fromtimestamp(row['created_ts'], timezone.utc).isoformat()
                evidence_excerpt = None if lead else json.dumps({
                    'excerpts': ext.excerpts if ext else {},
                    'contact': ext.contact if ext else None,
                    'contact_excerpt': ext.contact_excerpt if ext else None,
                    'suspicious_signals': ext.suspicious_signals if ext else [],
                }, ensure_ascii=False)
                cur = conn.execute(
                    'INSERT OR IGNORE INTO opportunities('
                    'id,employer_id,title,location,suburb,source,source_url,source_group_name,source_group_url,'
                    'verification_state,first_seen_at,published_at,description,employment_type,requirements,'
                    'pay_display,pay_hourly_min,visa_status'
                    ') VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (row['post_id'], employer_id, title, location, suburb,
                     'Local business discovery' if lead else 'Facebook Group', url,
                     None if lead else row['group_name'], None if lead else row['group_url'],
                     'employer_lead' if lead else 'unverified_job_post', row['first_seen_at'],
                     published_at, None if lead else row['body'],
                     employment_type, requirements, pay_display, pay_hourly_min, visa_status),
                )
                if cur.rowcount:
                    added += 1
                update_cols = (
                    'source=?,source_url=?,source_group_name=?,source_group_url=?,'
                    'first_seen_at=?,published_at=?,description=?,verification_state=?,'
                    'title=?,location=?,suburb=COALESCE(?,suburb),employment_type=?,'
                    'requirements=?,pay_display=?,pay_hourly_min=?,visa_status=?,'
                    'employer_id=CASE WHEN ? THEN COALESCE(employer_id,?) ELSE NULL END'
                )
                update_params = [
                    'Local business discovery' if lead else 'Facebook Group', url,
                    None if lead else row['group_name'], None if lead else row['group_url'],
                    row['first_seen_at'], published_at, None if lead else row['body'],
                    'employer_lead' if lead else 'unverified_job_post',
                    title, location, suburb, employment_type,
                    requirements, pay_display, pay_hourly_min, visa_status,
                    int(lead), employer_id,
                ]
                if 'role_bucket' in opp_cols:
                    update_cols += ',role_bucket=?'
                    update_params.append(role_bucket)
                if 'discovery_source' in opp_cols:
                    update_cols += ',discovery_source=COALESCE(discovery_source,?)'
                    update_params.append('Local business discovery' if lead else 'Facebook Group')
                update_params.append(row['post_id'])
                conn.execute(
                    f"UPDATE opportunities SET {update_cols} WHERE id=? AND verification_state!='verified_job'",
                    update_params,
                )
                conn.execute(
                    'INSERT INTO opportunity_evidence(opportunity_id,source_url,source_type,observed_at,excerpt,supports_job) '
                    'VALUES (?,?,?,?,?,0) ON CONFLICT(opportunity_id,source_url,source_type) DO UPDATE SET '
                    'excerpt=COALESCE(excluded.excerpt, opportunity_evidence.excerpt)',
                    (row['post_id'], url, 'local_discovery' if lead else 'facebook_post', row['first_seen_at'], evidence_excerpt),
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

    @staticmethod
    def _build_filters(
        *, role: str | None = None, suburb: str | None = None, location: str | None = None,
        verification_state: str | None = None, source: str | None = None,
        employment_type: str | None = None, experience: str | None = None,
        requirement: str | None = None, origin_suburb: str | None = None,
        max_commute_minutes: int | None = None, commute_window: str | None = None,
        role_bucket: str | None = None, recent: bool | None = None,
        commute_unknown: bool | None = None,
        pay_min: float | None = None, pay_max: float | None = None,
        include_unknown_pay: bool = False,
        include_unknown_employment_type: bool = False,
        include_unknown_requirement: bool = False,
        category: str | None = None, ats_provider: str | None = None,
        recent_days: int | None = None,
        lifecycle_state: str | None = None,
        include_expired: bool = True,
        has_published_schema: bool = True,
        opp_columns: set[str] | None = None,
    ) -> tuple[list[str], list]:
        if pay_min is not None and pay_max is not None and pay_min > pay_max:
            raise ValueError('pay_min must be <= pay_max')
        where, params = [], []
        for column, value in (('o.suburb', suburb), ('o.verification_state', verification_state),
                              ('o.source', source), ('o.experience_required', experience),
                              ('o.role_bucket', role_bucket)):
            if value:
                where.append(f'{column} = ?')
                params.append(value)
        has_lifecycle = opp_columns is None or 'lifecycle_state' in opp_columns
        if lifecycle_state:
            if has_lifecycle:
                where.append("COALESCE(o.lifecycle_state, 'active') = ?")
                params.append(lifecycle_state)
            elif lifecycle_state != 'active':
                where.append('0')
        elif not include_expired and has_lifecycle:
            where.append("COALESCE(o.lifecycle_state, 'active') != 'expired'")
        if employment_type:
            if include_unknown_employment_type:
                where.append("(o.employment_type = ? OR o.employment_type IS NULL OR TRIM(o.employment_type) = '')")
            else:
                where.append('o.employment_type = ?')
            params.append(employment_type)
        if category:
            where.append('LOWER(o.category) = LOWER(?)')
            params.append(category)
        if ats_provider:
            if opp_columns is None or 'ats_provider' in opp_columns:
                where.append('LOWER(o.ats_provider) = LOWER(?)')
                params.append(ats_provider)
            else:
                where.append('0')
        if pay_min is not None or pay_max is not None:
            pay_clauses = []
            if pay_min is not None:
                pay_clauses.append('o.pay_hourly_min >= ?')
                params.append(pay_min)
            if pay_max is not None:
                pay_clauses.append('o.pay_hourly_min <= ?')
                params.append(pay_max)
            known_pay_sql = ' AND '.join(pay_clauses)
            if include_unknown_pay:
                where.append(f'(o.pay_hourly_min IS NULL OR ({known_pay_sql}))')
            else:
                where.append(f'(o.pay_hourly_min IS NOT NULL AND {known_pay_sql})')
        if verification_state == 'verified_job':
            where.append("EXISTS (SELECT 1 FROM opportunity_evidence v WHERE v.opportunity_id=o.id "
                         "AND v.source_type='job_listing' AND v.supports_job=1 AND v.checked_at IS NOT NULL)")
        if role:
            where.append("o.title LIKE ? ESCAPE '\\'")
            params.append('%' + role.replace('%', '\\%').replace('_', '\\_') + '%')
        if requirement:
            term = '%' + requirement.replace('%', '\\%').replace('_', '\\_') + '%'
            if include_unknown_requirement:
                where.append("(o.requirements LIKE ? ESCAPE '\\' OR o.requirements IS NULL OR TRIM(o.requirements) = '')")
            else:
                where.append("o.requirements LIKE ? ESCAPE '\\'")
            params.append(term)
        if location:
            where.append("(o.location LIKE ? ESCAPE '\\' OR o.suburb LIKE ? ESCAPE '\\')")
            term = '%' + location.replace('%', '\\%').replace('_', '\\_') + '%'
            params.extend((term, term))
        if recent_days is not None:
            now = datetime.now(timezone.utc)
            where.append('o.published_at IS NOT NULL AND julianday(o.published_at)>=julianday(?) AND julianday(o.published_at)<=julianday(?)')
            params.extend(((now - timedelta(days=recent_days)).isoformat(), now.isoformat()))
        if recent is not None:
            now = datetime.now(timezone.utc)
            now_iso = now.isoformat()
            recent_sql = (
                "o.verification_state='verified_job' "
                "AND julianday(o.published_at)>=julianday(?) AND julianday(o.published_at)<=julianday(?) "
                "AND julianday(o.last_verified_at)>=julianday(?) AND julianday(o.last_verified_at)<=julianday(?) "
                "AND EXISTS (SELECT 1 FROM opportunity_evidence v WHERE v.opportunity_id=o.id "
                "AND v.source_type='job_listing' AND v.supports_job=1 AND v.checked_at IS NOT NULL)"
            )
            where.append(f'COALESCE(({recent_sql}),0)=?')
            params.extend((
                (now - timedelta(days=30)).isoformat(), now_iso,
                (now - timedelta(hours=48)).isoformat(), now_iso,
                int(recent),
            ))
        if max_commute_minutes is not None:
            if not origin_suburb:
                raise ValueError('origin_suburb is required for a commute filter')
            if commute_window:
                if has_published_schema:
                    where.append('EXISTS (SELECT 1 FROM commute_observations c WHERE c.opportunity_id=o.id '
                                 'AND c.origin_label=? AND c.departure_window=? AND c.transit_minutes<=? '
                                 'AND c.checked_at>=?)')
                    params.extend((origin_suburb, commute_window, max_commute_minutes,
                                   time.time() - _COMMUTE_MAX_AGE_SECONDS))
                else:
                    where.append('0')
            else:
                where.append('EXISTS (SELECT 1 FROM opportunity_commutes c WHERE c.opportunity_id=o.id '
                             'AND c.origin_suburb=? AND c.transit_minutes<=?)')
                params.extend((origin_suburb, max_commute_minutes))
        if commute_unknown is not None:
            if not origin_suburb or not commute_window:
                raise ValueError('origin_suburb and commute_window are required for commute_unknown')
            if has_published_schema:
                exists = ('EXISTS (SELECT 1 FROM commute_observations c WHERE c.opportunity_id=o.id '
                          'AND c.origin_label=? AND c.departure_window=? AND c.checked_at>=?)')
                where.append('NOT ' + exists if commute_unknown else exists)
                params.extend((origin_suburb, commute_window, time.time() - _COMMUTE_MAX_AGE_SECONDS))
            else:
                where.append('1' if commute_unknown else '0')
        return where, params

    def _prepare_filters(self, conn, **filters) -> tuple[bool, set[str], list[str], list]:
        has_published_schema = conn.execute('PRAGMA user_version').fetchone()[0] >= 5
        opp_columns = {row[1] for row in conn.execute('PRAGMA table_info(opportunities)')}
        where, params = self._build_filters(
            **filters, has_published_schema=has_published_schema, opp_columns=opp_columns,
        )
        return has_published_schema, opp_columns, where, params

    def count(self, *, role: str | None = None, suburb: str | None = None, location: str | None = None,
              verification_state: str | None = None, source: str | None = None,
              employment_type: str | None = None, experience: str | None = None,
              requirement: str | None = None, origin_suburb: str | None = None,
              max_commute_minutes: int | None = None, commute_window: str | None = None,
              role_bucket: str | None = None, recent: bool | None = None,
              commute_unknown: bool | None = None,
              pay_min: float | None = None, pay_max: float | None = None,
              include_unknown_pay: bool = False,
              include_unknown_employment_type: bool = False,
              include_unknown_requirement: bool = False,
              category: str | None = None, ats_provider: str | None = None,
              recent_days: int | None = None,
              lifecycle_state: str | None = None,
              include_expired: bool = True) -> int:
        with self._db.connect() as conn:
            _, _, where, params = self._prepare_filters(
                conn,
                role=role, suburb=suburb, location=location, verification_state=verification_state,
                source=source, employment_type=employment_type, experience=experience,
                requirement=requirement, origin_suburb=origin_suburb,
                max_commute_minutes=max_commute_minutes, commute_window=commute_window,
                role_bucket=role_bucket, recent=recent, commute_unknown=commute_unknown,
                pay_min=pay_min, pay_max=pay_max, include_unknown_pay=include_unknown_pay,
                include_unknown_employment_type=include_unknown_employment_type,
                include_unknown_requirement=include_unknown_requirement,
                category=category, ats_provider=ats_provider, recent_days=recent_days,
                lifecycle_state=lifecycle_state, include_expired=include_expired,
            )
            sql = 'SELECT COUNT(*) FROM opportunities o '
            if where:
                sql += 'WHERE ' + ' AND '.join(where)
            return int(conn.execute(sql, params).fetchone()[0])

    def list(self, *, role: str | None = None, suburb: str | None = None, location: str | None = None,
             verification_state: str | None = None, source: str | None = None,
             employment_type: str | None = None, experience: str | None = None,
             requirement: str | None = None, origin_suburb: str | None = None,
             max_commute_minutes: int | None = None, commute_window: str | None = None,
             role_bucket: str | None = None, recent: bool | None = None,
             commute_unknown: bool | None = None,
             pay_min: float | None = None, pay_max: float | None = None,
             include_unknown_pay: bool = False,
             include_unknown_employment_type: bool = False,
             include_unknown_requirement: bool = False,
             category: str | None = None, ats_provider: str | None = None,
             recent_days: int | None = None,
             lifecycle_state: str | None = None,
             include_expired: bool = True,
             limit: int = 500, offset: int = 0) -> list[dict]:
        with self._db.connect() as conn:
            has_published_schema, _, where, params = self._prepare_filters(
                conn,
                role=role, suburb=suburb, location=location, verification_state=verification_state,
                source=source, employment_type=employment_type, experience=experience,
                requirement=requirement, origin_suburb=origin_suburb,
                max_commute_minutes=max_commute_minutes, commute_window=commute_window,
                role_bucket=role_bucket, recent=recent, commute_unknown=commute_unknown,
                pay_min=pay_min, pay_max=pay_max, include_unknown_pay=include_unknown_pay,
                include_unknown_employment_type=include_unknown_employment_type,
                include_unknown_requirement=include_unknown_requirement,
                category=category, ats_provider=ats_provider, recent_days=recent_days,
                lifecycle_state=lifecycle_state, include_expired=include_expired,
            )
            sql = ('SELECT o.*, e.name AS company, e.website AS employer_website FROM opportunities o '
                   'LEFT JOIN employers e ON e.id=o.employer_id ')
            if where:
                sql += 'WHERE ' + ' AND '.join(where) + ' '
            sql += 'ORDER BY o.first_seen_at DESC, o.id ASC LIMIT ? OFFSET ?'
            rows = conn.execute(sql, [*params, limit, offset]).fetchall()
            items = []
            for row in rows:
                item = dict(row)
                item['url'] = item['source_url']
                item['evidence'] = [{**dict(e), 'supports_job': bool(e['supports_job'])} for e in conn.execute(
                    'SELECT source_url,source_type,observed_at,checked_at,excerpt,check_outcome,supports_job '
                    'FROM opportunity_evidence WHERE opportunity_id=?',
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
                try:
                    posted = datetime.fromisoformat(item['published_at']) if item['published_at'] else None
                    checked = datetime.fromisoformat(item['last_verified_at']) if item['last_verified_at'] else None
                    now = datetime.now(timezone.utc)
                    item['recent'] = bool(posted and checked and posted.tzinfo and checked.tzinfo
                                          and now - timedelta(days=30) <= posted <= now
                                          and now - timedelta(hours=48) <= checked <= now)
                except ValueError:
                    item['recent'] = False
                observations = list(conn.execute(
                    'SELECT departure_window,transit_minutes,departure_at,checked_at,source_url '
                    'FROM commute_observations WHERE opportunity_id=? AND origin_label=?',
                    (item['id'], origin_suburb or 'Rockdale Station'),
                )) if has_published_schema else []
                item['commute_observations'] = [dict(obs) for obs in observations]
                item['commute_windows'] = {
                    obs['departure_window']: obs['transit_minutes'] for obs in observations
                    if obs['checked_at'] >= time.time() - _COMMUTE_MAX_AGE_SECONDS
                }
                item['commute_unknown'] = (commute_window not in item['commute_windows']) if commute_window else not item['commute_windows']
                item['lifecycle_state'] = item.get('lifecycle_state') or 'active'
                item.setdefault('stale_since', None)
                item.setdefault('expired_at', None)
                item['is_stale'] = item['lifecycle_state'] == 'stale'
                items.append(item)
            return items

    def summary(self, *, role: str | None = None, suburb: str | None = None, location: str | None = None,
                verification_state: str | None = None, source: str | None = None,
                employment_type: str | None = None, experience: str | None = None,
                requirement: str | None = None, origin_suburb: str | None = None,
                max_commute_minutes: int | None = None, commute_window: str | None = None,
                role_bucket: str | None = None, recent: bool | None = None,
                commute_unknown: bool | None = None,
                pay_min: float | None = None, pay_max: float | None = None,
                include_unknown_pay: bool = False,
                include_unknown_employment_type: bool = False,
                include_unknown_requirement: bool = False,
                category: str | None = None, ats_provider: str | None = None,
                recent_days: int | None = None,
                lifecycle_state: str | None = None,
                include_expired: bool = True) -> dict:
        with self._db.connect() as conn:
            _, opp_columns, where, params = self._prepare_filters(
                conn,
                role=role, suburb=suburb, location=location, verification_state=verification_state,
                source=source, employment_type=employment_type, experience=experience,
                requirement=requirement, origin_suburb=origin_suburb,
                max_commute_minutes=max_commute_minutes, commute_window=commute_window,
                role_bucket=role_bucket, recent=recent, commute_unknown=commute_unknown,
                pay_min=pay_min, pay_max=pay_max, include_unknown_pay=include_unknown_pay,
                include_unknown_employment_type=include_unknown_employment_type,
                include_unknown_requirement=include_unknown_requirement,
                category=category, ats_provider=ats_provider, recent_days=recent_days,
                lifecycle_state=lifecycle_state, include_expired=include_expired,
            )
            where_sql = ('WHERE ' + ' AND '.join(where) + ' ') if where else ''
            counts_by_state = {'verified_job': 0, 'unverified_job_post': 0, 'employer_lead': 0}
            state_sql = (
                "SELECT CASE WHEN o.verification_state='verified_job' AND NOT EXISTS ("
                "SELECT 1 FROM opportunity_evidence v WHERE v.opportunity_id=o.id "
                "AND v.source_type='job_listing' AND v.supports_job=1 AND v.checked_at IS NOT NULL"
                ") THEN 'unverified_job_post' ELSE o.verification_state END AS effective_state, "
                "COUNT(*) AS n FROM opportunities o " + where_sql + "GROUP BY effective_state"
            )
            for row in conn.execute(state_sql, params):
                key = row['effective_state']
                if key in counts_by_state:
                    counts_by_state[key] = int(row['n'])

            def _distinct_facet(column: str) -> list[str]:
                if column not in opp_columns:
                    return []
                facet_where = [*where, f"o.{column} IS NOT NULL", f"TRIM(o.{column}) != ''"]
                rows = conn.execute(
                    f"SELECT DISTINCT TRIM(o.{column}) AS val FROM opportunities o "
                    f"WHERE {' AND '.join(facet_where)} ORDER BY val ASC",
                    params,
                ).fetchall()
                return [str(r['val']) for r in rows if r['val'] is not None]

            lifecycle_counts = {'active': 0, 'stale': 0, 'expired': 0}
            if 'lifecycle_state' in opp_columns:
                lc_sql = (
                    "SELECT COALESCE(o.lifecycle_state, 'active') AS lc, COUNT(*) AS n "
                    "FROM opportunities o " + where_sql + "GROUP BY lc"
                )
                for row in conn.execute(lc_sql, params):
                    key = str(row['lc'])
                    if key in lifecycle_counts:
                        lifecycle_counts[key] = int(row['n'])
            else:
                lifecycle_counts['active'] = sum(counts_by_state.values())

            return {
                'total': sum(counts_by_state.values()),
                'counts_by_state': counts_by_state,
                'lifecycle_counts': lifecycle_counts,
                'facets': {
                    'suburbs': _distinct_facet('suburb'),
                    'ats_providers': _distinct_facet('ats_provider'),
                    'employment_types': _distinct_facet('employment_type'),
                    'categories': _distinct_facet('category'),
                    'role_buckets': _distinct_facet('role_bucket'),
                    'sources': _distinct_facet('source'),
                },
            }

    def list_employer_check_states(self) -> list[dict]:
        """Return OSM-discovered employer leads with their latest website inspection outcome."""
        sql = (
            "SELECT o.id, o.source_url, e.name AS company, o.suburb, o.category, o.lat, o.lng, "
            "o.verification_state, ev.checked_at, ev.excerpt, ev.check_outcome "
            "FROM opportunities o "
            "LEFT JOIN employers e ON e.id = o.employer_id "
            "LEFT JOIN opportunity_evidence ev ON ev.id = ("
            "  SELECT ev2.id FROM opportunity_evidence ev2 "
            "  WHERE ev2.opportunity_id = o.id AND ev2.source_type = 'employer_website' "
            "  ORDER BY ev2.observed_at DESC, ev2.id DESC LIMIT 1"
            ") "
            "WHERE o.discovery_source = 'OpenStreetMap discovery' OR o.source = 'OpenStreetMap discovery' "
            "ORDER BY o.first_seen_at DESC, o.id"
        )
        with self._db.connect() as conn:
            return [dict(row) for row in conn.execute(sql).fetchall()]

    def get(self, opportunity_id: str) -> dict | None:
        items = self.list(limit=1000)
        for item in items:
            if item['id'] == opportunity_id:
                return item
        return None

    def get_last_verified_map(self) -> dict[str, str | None]:
        with self._db.connect() as conn:
            rows = conn.execute("SELECT source_url, last_verified_at FROM opportunities").fetchall()
            return {str(r['source_url']).strip().lower(): r['last_verified_at'] for r in rows if r['source_url']}

    def apply_listing_check_lifecycle(
        self,
        opportunity_id: str,
        check: ListingCheck,
        *,
        now_iso: str | None = None,
    ) -> str:
        """Update lifecycle state conservatively: explicit closed/404/410 expires; transient errors never expire."""
        ts_iso = now_iso or datetime.fromtimestamp(check.checked_at, timezone.utc).isoformat()
        with self._db.connect() as conn:
            opp_cols = {row[1] for row in conn.execute('PRAGMA table_info(opportunities)')}
            if 'lifecycle_state' not in opp_cols:
                return 'active'
            row = conn.execute(
                'SELECT lifecycle_state FROM opportunities WHERE id=?',
                (opportunity_id,),
            ).fetchone()
            if row is None:
                return 'active'
            if check.outcome == 'ok':
                conn.execute(
                    "UPDATE opportunities SET lifecycle_state='active', stale_since=NULL, "
                    "expired_at=NULL, consecutive_misses=0 WHERE id=?",
                    (opportunity_id,),
                )
                return 'active'
            if is_explicit_expiry(check.outcome):
                conn.execute(
                    "UPDATE opportunities SET lifecycle_state='expired', expired_at=COALESCE(expired_at, ?) WHERE id=?",
                    (ts_iso, opportunity_id),
                )
                return 'expired'
            return str(row['lifecycle_state'] or 'active')

    def apply_board_reconciliation(
        self,
        source_id: str,
        seen_external_ids: set[str],
        *,
        fetch_succeeded: bool,
        now_iso: str | None = None,
    ) -> dict[str, int]:
        """Mark listings expired only after consecutive successful fetches omit them."""
        if not fetch_succeeded:
            return {'missed': 0, 'expired': 0}
        ts_iso = now_iso or datetime.now(timezone.utc).isoformat()
        missed = 0
        expired = 0
        with self._db.connect() as conn:
            if conn.execute('PRAGMA user_version').fetchone()[0] < 6:
                return {'missed': 0, 'expired': 0}
            rows = conn.execute(
                "SELECT p.external_id, p.opportunity_id, o.lifecycle_state, o.consecutive_misses "
                "FROM published_source_keys p JOIN opportunities o ON o.id=p.opportunity_id "
                "WHERE p.source_id=? AND o.lifecycle_state != 'expired'",
                (source_id,),
            ).fetchall()
            for row in rows:
                ext_id = str(row['external_id'])
                opp_id = str(row['opportunity_id'])
                if ext_id in seen_external_ids:
                    if int(row['consecutive_misses'] or 0) > 0:
                        conn.execute(
                            'UPDATE opportunities SET consecutive_misses=0 WHERE id=?',
                            (opp_id,),
                        )
                    continue
                missed += 1
                new_misses = int(row['consecutive_misses'] or 0) + 1
                if new_misses >= CONSECUTIVE_MISS_EXPIRY_THRESHOLD:
                    expired += 1
                    conn.execute(
                        "UPDATE opportunities SET consecutive_misses=?, lifecycle_state='expired', "
                        "expired_at=COALESCE(expired_at, ?) WHERE id=?",
                        (new_misses, ts_iso, opp_id),
                    )
                else:
                    conn.execute(
                        'UPDATE opportunities SET consecutive_misses=? WHERE id=?',
                        (new_misses, opp_id),
                    )
        return {'missed': missed, 'expired': expired}

    def mark_stale_opportunities(
        self,
        *,
        stale_after_days: int = STALE_AFTER_DAYS,
        now: datetime | None = None,
    ) -> int:
        """Mark verified active opportunities as stale when last_verified_at exceeds threshold."""
        current = now or datetime.now(timezone.utc)
        cutoff = (current - timedelta(days=max(1, stale_after_days))).isoformat()
        now_iso = current.isoformat()
        with self._db.connect() as conn:
            opp_cols = {row[1] for row in conn.execute('PRAGMA table_info(opportunities)')}
            if 'lifecycle_state' not in opp_cols:
                return 0
            cur = conn.execute(
                "UPDATE opportunities SET lifecycle_state='stale', stale_since=COALESCE(stale_since, ?) "
                "WHERE verification_state='verified_job' AND lifecycle_state='active' "
                "AND last_verified_at IS NOT NULL AND julianday(last_verified_at) < julianday(?)",
                (now_iso, cutoff),
            )
            return int(cur.rowcount or 0)
