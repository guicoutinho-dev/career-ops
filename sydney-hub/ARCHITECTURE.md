# SydneyStays local service

## Runtime

`app.py` composes a single local FastAPI process with SQLite and APScheduler. The existing Facebook routine runs serially through the Maestri scraper and classifier adapters. A failed group scrape is isolated to its run; it does not remove catalog records or mark an opportunity closed. The service binds to localhost and serves `web/index.html`, generated from `web/template.html` at startup.

Only `hub/storage/` opens SQLite. `hub/pipeline/routine.py` stores Facebook posts and synchronizes job posts into the opportunity catalog. `hub/api/app.py` reads that persisted catalog through `CatalogRepository`; the Jobs UI requests `/api/opportunities` and shows an explicit error when the request fails. No static jobs are embedded in the generated page.

## Catalog trust contract

- `posts` keeps original Facebook bodies, links, timestamps, and user status. The 11 incompatible legacy `approved` rows were repaired to `skipped`; their original AI and classification JSON remain in archival columns. A trigger rejects new `approved` rows from the retired local-hunt importer.
- `employers` stores identified business names. `opportunities` stores a stable ID, optional employer, source URL, optional Facebook group, optional role/location/terms, and one of `verified_job`, `unverified_job_post`, or `employer_lead`.
- `opportunity_evidence` keeps provenance. A verified label requires `source_type='job_listing'`, `supports_job=1`, and a non-null `checked_at`. No historical import qualifies. Generic career and agency pages are employer leads. Facebook posts are unverified posts.
- `opportunity_commutes` is origin-specific and requires a measured transit time, check time, and source. No routes are stored yet; a commute filter therefore returns no opportunities rather than guessing.
- The ignored local `web/seed/jobs.json` is imported idempotently for ID continuity. Duplicate IDs collapse to one opportunity. Unsupported pay, requirements, visa eligibility, start date, and generated outreach are not imported. Post provenance wins when a seed ID also exists in `posts`.

`GET /api/opportunities` filters by role, suburb, location, source, verification, employment type, experience, requirement, and measured commute. Responses preserve original URLs and evidence. Unknown claims remain null or `unknown`. The Jobs UI keeps the existing card, map, drawer, favorites, notes, saved searches, and application state. Employer leads use a separate contact state; old `fav` values migrate to a favorites map without changing opportunity IDs. The Rockdale modal also reads the live catalog.

## Data safety and rollback

The initial catalog migration and the schema v1→v2 upgrade each create an integrity-checked local backup before changing an existing database. Local database, backups, generated HTML, personal seeds, and `config/settings.yml` are ignored by Git. `config/settings.example.yml` allows a clean checkout to start with empty local data.

Stop the service before rollback. Run `python sydney-hub/rollback_catalog.py --target-version 1` to restore the pre-v2 snapshot, or omit the flag to restore the original pre-catalog database. If new records exist, add `--force`; it takes and verifies a safety copy of the current database first. Restoration uses SQLite's exclusive locking mode and backup API, so a concurrent writer cannot slip between the safety check and restore. The command refuses active WAL sidecars.

The former `scripts/hunt_local_venues.py` importer is paused because it generated unverified job, salary, and visa claims. Its original code is retained only in an ignored local backup for the later employer discovery pilot. This delivery adds no collector or dependency.

## Verification

`python -m pytest -q sydney-hub/tests` covers storage migration/rollback, catalog trust and provenance, FastAPI filters, and the existing pipeline. `node sydney-hub/tests/test_jobs_ui.mjs` runs Playwright against `python sydney-hub/tests/serve_ui_fixture.py` and checks desktop/mobile, saved state, evidence labels, source links, filters, and API failure. The real local database is checked separately with `PRAGMA integrity_check` and an API smoke test.
