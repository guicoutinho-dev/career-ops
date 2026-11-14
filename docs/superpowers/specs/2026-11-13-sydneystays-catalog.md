# SydneyStays catalog reliability — design

## Goal

Make the existing Jobs screen read persisted opportunities from the local FastAPI service without treating historic, unsupported claims as verified jobs. Keep the existing visual design and browser-side saved state. Do not add collectors or change the Career Ops installation.

## Source and trust contract

- The existing `posts` table remains the Facebook collector's source of truth. Its 11 incompatible `approved` statuses become `skipped`; original AI/classification JSON is retained in archival columns, while body, URL, and user status remain intact. A database trigger rejects new `approved` records from the unsafe legacy local-hunt script.
- The 102 local `web/seed/jobs.json` entries contain 100 unique IDs and are historic input, not verified evidence. Import them idempotently with stable IDs, retain original URLs, and discard unsupported pay, requirements, visa, start-date, publication-date, and outreach claims from the displayed opportunity. Direct-employer records with no job evidence become employer leads. Other records become unverified job posts. Never call either an open job.
- Facebook `kind=job` posts become unverified job posts with post permalink, body, original timestamp, and source group. Matching posts override seed provenance. An author is not assumed to be the employer. Already injected local-hunt posts have their unsupported generated facts withheld.
- `verified_job` requires a specific `job_listing` evidence record with `supports_job=1` and an explicit `checked_at` timestamp. This delivery creates the model and gate but does not verify any historic record.
- Missing fields remain `null`/unknown. Network errors do not change verification state.

## Storage and API

SQLite holds `employers`, `opportunities`, and `opportunity_evidence`, alongside the existing tables. An opportunity has a stable ID, optional employer, role/title, location, source, original URL, first-seen time, optional published/checked times, and verification state: `verified_job`, `unverified_job_post`, or `employer_lead`. Evidence records preserve original URLs and the explicit fact they support. The migration is idempotent and creates a verified SQLite backup before touching an existing database. A rollback command restores that backup after an integrity check; it never silently overwrites a newer database.

`GET /api/opportunities` supports role, location/suburb, source, verification state, employment type, experience, requirement, origin-specific transit time, and limit. Commute filters include only measured routes; none exist yet. It returns display-safe records with source/evidence metadata and original IDs. The API never falls back to embedded jobs.

## UI

The existing card, drawer, filters, map, saved searches, localStorage keys, favorites, notes, and application status remain. Jobs load asynchronously from the API, then facets rebuild from the response. Loading, empty, and API-error states are visibly distinct. A failed API does not reveal old static seed jobs. Cards, drawer, and the Rockdale report mark verification state and use neutral language for leads; they hide unknown pay and requirements. All original IDs are retained; legacy `fav` status migrates into a separate favorites map so applications do not erase favorites. Contact with an employer lead is tracked separately from an application.

## Operational limits

FastAPI, SQLite, APScheduler, and the present local-only security boundary remain. The static Stays dataset is local user data. Code, assets, sample config, and tests are versioned; real databases, backups, personal seed files, generated HTML, tokens, and local settings are ignored. No new framework or network collector is added.

## Acceptance

1. Existing DB repair is lossless, idempotent, and rollbackable.
2. No historic entry is shown as verified, and unsupported claims are absent from API and UI.
3. The API exposes real persisted Facebook posts and historic leads/posts with source URLs, filters, and unknowns.
4. Browser saved state survives reload and stable-ID migration.
5. Desktop/mobile Jobs interactions pass; API failures are visible and do not show the old static catalog.
6. Unit, API integration, real-database smoke, and browser E2E tests pass.
