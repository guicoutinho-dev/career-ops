# Execution Ledger — SydneyStays Jobs Operational Local Engine

- **Execution Date:** 2026-11-14
- **Approved Checkout:** `D:\AntigravityProjetos\sydneystays-v4-v5-preview`
- **Approved Branch:** `codex/sydneystays-v4-v5-preview`
- **Base Commit:** `e53e9199ddd19cbe8db32bcb2ead79ffaf452147`
- **Initial Plan Commit:** `b855f137c782a6197554ae412ba05527fb1ddaf9`
- **Plan:** `docs/superpowers/plans/2026-11-14-sydneystays-jobs-operational.md`
- **Spec:** `docs/superpowers/specs/2026-11-14-sydneystays-published-jobs-design.md`

## Baseline Original Databases (Never Mutate)

| Artifact | SHA-256 | LastWriteTime |
|----------|---------|---------------|
| V4 Pilot DB (`C:\Users\guicx\.codex\worktrees\sydneystays-employer-pilot\career-ops\sydney-hub\data\hub.db`) | `0B8BA88BCC656A7D4312BBE02243957CDC1B8BBF49EFCA9FB8946E18CC2A457F` | `2026-11-14 12:12:03` |
| Main Worktree v2 DB (`D:\AntigravityProjetos\career-ops\sydney-hub\data\hub.db`) | `F0E13096464FF683B2263D90098768A718645E9BEF16B4BAA5FD6C7C150B11FC` | `2026-11-13 23:18:14` |

## Prisma Preflight & Rulings (Pre-Task 1 Gate)

Blocked Task 1 prior to code implementation on **2 Critical + 6 Important** findings, resolved in the plan commit prior to Task 1 code:

1. **[Critical 1] Pre-schema/pre-snapshot isolated DB path guard:** `--db`, `bootstrap_isolated_jobs_db`, `run_published_jobs`, `jobs_app.py`, and `rollback_catalog.py --pre-run` must reject V4/main DB paths and any path resolving (including symlinks / `..`) outside the marker-approved isolated directory (`data/jobs_isolated/` with `.sydneystays-jobs-isolated`) **before** `ensure_published_schema`, `snapshot_published_run`, or SQLite open; Task 1 tests rejection leaving outside DB SHA-256 and `mtime` (`st_mtime_ns`) untouched.
2. **[Critical 2] 7-day real observation gate on scheduler:** `evaluate_scheduler_gate` / `PublishedSchedulerService` requires `seven_day_status == "passed"` (7 distinct `Australia/Sydney` calendar days in `yield_journal.jsonl`) in addition to `20/10/5` and 100% per-listing audit (`manual_audit_confirmed`); Task 6 pins Day 1 (`seven_day_status == "pending"`) blocked even when `20/10/5` and audit pass.
3. **[Important 1] Per-board ATS policy:** `source_policies.yml` governs ATS authorization per individual board (`ats:<provider>:<slug>`), never inheriting provider-wide approval.
4. **[Important 2] Manifest independent provenance:** `published_boards.yml` entries require independent `discovery_origin`, non-circular employer `referrer_url`, `evidence_ref`, and `verified_at`, with a dedicated test in Task 2.
5. **[Important 3] Workable `source_url` / `evidence_url` contract:** `opportunities.source_url` stores Workable `<url>` verbatim while `opportunity_evidence.evidence_url` and `ListingCheck.evidence_url` persist the verified canonical/API URL consistently.
6. **[Important 4] Task 3 commit hygiene:** Task 3 commit explicitly stages `sydney-hub/hub/discovery/published_filter.py` if modified alongside `geocode.py` and `tests/test_geocode.py`.
7. **[Important 5] Full E2E with fixture `verified_job`:** Task 7/8 includes `runner -> isolated DB -> jobs_app.py API -> UI/Playwright` E2E with a fixture `verified_job`.
8. **[Important 6] Expected hygiene scan baseline failure on inherited `HEAD`:** `check_publish_hygiene.py` on inherited `HEAD` (`d31174e5` ancestor + tracked `USER.md`/`applications/*`) is expected to fail (`is_safe_to_publish = False`); Task 8 verifies zero new unignored/sensitive files added on top of the documented baseline.

## Task Status

- [x] Task 1 (`24b251c1`): Isolated Config, Per-Board Fail-Closed Source Policy Registry & Pre-Schema/Snapshot DB Guard (`tests/test_settings.py`, `tests/test_source_policy.py`, `tests/test_jobs_bootstrap.py`, `tests/test_storage.py` — 29 passed)
- [x] Task 2 (`a1d69757`): Bridge-Verified Official ATS Manifest Provenance/Audit & Catalog Guards (`tests/test_published_ats.py`, `tests/test_published_catalog.py`, `tests/test_ats_bridge.py` — 21 passed)
- [x] Task 3 (`02f08247`): Exact-Destination Extraction & Non-Fabricating Geocoding (`tests/test_geocode.py`, `tests/test_published_filter.py`, `tests/test_transit.py` — 29 passed)
- [x] Task 4 (`0c927dc4`): Hardened Verification, Persistent 429 Cooldown, 7-Day Yield Journal & Runner (`tests/test_published_health.py`, `tests/test_published_runner.py` — 13 passed)
- [x] Task 5 (`5dc47dfb`): Honest 7-Day Pilot Scorecard, External Gates & Per-Listing 100% Audit Verification (`tests/test_pilot_gate.py`, `tests/test_published_runner.py` — 13 passed)
- [x] Task 6 (`e264cc05`): Opt-In Published Jobs Scheduler Guarded Fail-Closed by 7-Day + Audit Pilot Gate (`tests/test_jobs_scheduler.py`, `tests/test_scheduler.py` — 11 passed)
- [x] Task 7 (`1d545683`, `28ed489e`): Jobs-Only Local Service Without Facebook & Health API (`tests/test_jobs_service.py`, `tests/test_api.py`, `tests/test_jobs_v5_engine.mjs`)
- [x] Task 8 (this commit; incl. `983b4695` THE ICONIC Sonda seed + `dbe764a3` Prisma Task 1 DB-anchor & `source_policy` fixes): Full Verification Suites, Expected Hygiene Baseline Check, Rollback Drill & Original DB Preservation

## Final Verification Results (Post-`28ed489e`)
- `pytest -ra` (`sydney-hub/`): `289 passed, 2 skipped` (`osmium` optional)
- `node --test tests/test_jobs_v5_engine.mjs`: `6 passed, 0 failed`
- Playwright `tests/test_jobs_ui.mjs` (`serve_ui_fixture.py`): `ok`
- Temp isolated `jobs_app.py --once`: `mode="jobs_only"`, `scheduler_active=false`, `counts_by_state={"employer_lead": 155, "unverified_job_post": 0, "verified_job": 0}`
- Playwright `tests/test_preview_e2e.mjs --mode=leads-only` against `jobs_app.py`: `ok` (`POST /api/runs` -> `409`)
- Playwright fixture `verified_job` E2E (`run_published_jobs` mocked feed -> isolated DB -> `jobs_app.py` -> Chromium `#segCountVerified == "1"`, `"Morning Barista"`): `ok`
- Pre-run rollback drill (`snapshot_published_run` -> insert row -> `rollback_catalog.py --pre-run --force`): `PRAGMA integrity_check == "ok"`, schema v5, `0 verified_job`, `155 employer_lead`
- Original DBs preserved (`wal_shm=false`):
  - V4 (`C:\Users\guicx\.codex\worktrees\sydneystays-employer-pilot\career-ops\sydney-hub\data\hub.db`): SHA-256 `0B8BA88BCC656A7D4312BBE02243957CDC1B8BBF49EFCA9FB8946E18CC2A457F`
  - Main v2 (`D:\AntigravityProjetos\career-ops\sydney-hub\data\hub.db`): SHA-256 `F0E13096464FF683B2263D90098768A718645E9BEF16B4BAA5FD6C7C150B11FC`
- `python check_publish_hygiene.py`: exited `1` (`is_safe_to_publish=false`, inherited `HEAD` ancestor `d31174e5` + tracked `USER.md`/`applications/`/`config/gmail_*`; `dirty_sensitive=[]`)
- External gates unchanged (no `--live` scraping run): no `config/jobs.yml`/`config/source_policies.yml` live approvals, Workable cooldown & terms pending operator activation, `THE ICONIC` recorded in `config/published_boards.example.yml` with `ats:greenhouse:theiconic` `publish_allowed: false`, no `TFNSW_API_KEY` + quota confirmation, no commercial Adzuna license, and scheduler requires 7 real Sydney calendar days + 100% per-listing audit.
