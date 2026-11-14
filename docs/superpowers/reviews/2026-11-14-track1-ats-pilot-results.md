# SydneyStays — Delivery 3 Track 1 Pilot Results (2026-11-14 UTC)

## Verdict

- **Technical pipeline, safety, provenance & compatibility:** Verified (`181 passed` pytest + Playwright desktop/mobile/offline/pagination UI tests + Playwright smoke against updated `155`-row `data/hub.db`).
- **Functional Track 1 target (`>= 5` verified jobs in the 4 target suburbs):** **NOT MET (`0` verified jobs generated in live run).** Per acceptance rules, zero verified jobs are reported transparently with exact measured counts and blockers — no location inference from OSM alone and no fabrication.

## Pre-Pilot Backup & Rollback Proof

1. **Isolated temp-copy rollback proof (`sydney-hub/rollback_catalog.py`):**
   - Original `sydney-hub/data/hub.db`: `user_version = 3`, `147` opportunities, `78` `retail store`.
   - Upgraded temp copy via `Database(...)`: `user_version = 4`, `147` opportunities, `78` `retail store`.
   - `rollback_catalog.py --target-version 3 --force`: restored `user_version = 3`, `147` opportunities.
   - `snapshot_catalog_for_pilot(...)` + dummy write + `rollback_catalog.py --target-version 4 --force`: restored `user_version = 4`, `147` opportunities (`dummy = 0`), newer writes preserved in safety backup.
2. **Live `sydney-hub/data/hub.db` backups before `--live` pilot:**
   - Pre-v4 schema backup (`target_version=3`): `sydney-hub/data/hub.db.catalog-v4-rollback.json` → `hub.db.pre-catalog-v4-1794636617888616300.db` (`before_sha256`: `7d8846c5457a4aaa840d84d199dac5eac63fd4f1fc5e26fd4353579e742df9a6`).
   - Pre-Track-1 v4 pilot backup (`target_version=4`): `sydney-hub/data/hub.db.employer-pilot-rollback.json` → `hub.db.pre-employer-pilot-1794636617902924200.db` (`before_sha256`: `a3785243e9070bb1d5b3a706c1ba0ae8dc9def5c685fde83ec0fe97d2cac4208`). Stale Delivery 2 manifest rotated to `.superseded-1794636617902924200`.

## Live Pilot Telemetry (`--live --priority hospitality --max-employers 60`)

Source report: `sydney-hub/data/pilot-reports/employer-pilot-20261114T061301Z.json`

| Metric | Measured Value |
| --- | ---: |
| Employers in priority queue | 52 |
| Tier 1 (reachable hospitality/cleaning) | 44 |
| Tier 2 (fresh OSM hospitality/cleaning) | 8 |
| `retail store` preserved & skipped from queue | 78 |
| Social-only URLs skipped | 9 |
| Previous failed inspections skipped (`failed_history_skipped`) | 15 |
| Root + internal pages checked | 170 |
| Career pages / outbound links found | 42 |
| ATS boards detected (supported) | `avature`: 2, `smartrecruiters`: 1 |
| ATS boards detected (unsupported) | `applynow`: 1 |
| ATS jobs returned total | 50 |
| ATS jobs kept in target 4 suburbs | 0 |
| ATS jobs dropped outside suburbs | 50 |
| ATS jobs dropped ambiguous location | 0 |
| Verified jobs (`verified_job`) | 0 |
| Unverified job posts | 0 |
| Employer leads preserved | 52 (`8` new + `44` existing updated) |
| Failed site inspections (`error`: 3, `blocked`: 2) | 5 |
| HTTP requests (Python / ATS Bridge) | 169 / 3 |
| Adapter-isolated ATS errors | 2 (`avature` `HTTP 404 Not Found` in Mascot) |
| Provider API cost | $0.00 |
| Total runtime | 159.453s |

### Suburb Breakdown (Live Run)

| Suburb | Employers Checked | Verified Jobs | Employer Leads Preserved |
| --- | ---: | ---: | ---: |
| Rockdale | 5 | 0 | 5 |
| Kogarah | 9 | 0 | 9 |
| Hurstville | 32 | 0 | 32 |
| Mascot | 6 | 0 | 6 |
| **Total** | **52** | **0** | **52** |

## Post-Pilot SQLite, Idempotency & Manual Audit

1. **SQLite Integrity (`sydney-hub/data/hub.db`, read-only inspection):**
   - `PRAGMA integrity_check` = `ok`
   - `PRAGMA user_version` = `4`
   - Total opportunities: `155` (`147` baseline + `8` newly inspected hospitality/cleaning OSM leads; `155` `employer_lead`, `0` `verified_job`, `0` `unverified_job_post`).
   - `retail store` count preserved intact: `78`.
   - Provenance separation populated cleanly (`discovery_source = 'OpenStreetMap discovery'` on `154` OSM rows; legacy manual probe kept `discovery_source = NULL` and `source = 'Manual official-site seed'`).
   - `opportunity_evidence` rows: `155` total, `109` with `checked_at IS NOT NULL`, `check_outcome` distribution: `ok`: `47`, `ok_no_title`: `1`, `blocked`: `29`, `error`: `10`, `timeout`: `6`, `<null>`: `62`.
2. **Manual Audit of `verified_job` Records:**
   - `0` `verified_job` rows exist in `data/hub.db`, so 100% (`0 / 0`) were audited and zero false-positive local jobs entered the catalog. Note specifically that `50 / 50` SmartRecruiters roles were correctly rejected because their explicit job locations lay outside `Rockdale`, `Kogarah`, `Hurstville`, and `Mascot`.
3. **Repeat Dry-Run Idempotency (`employer-pilot-20261114T061610Z.json`):**
   - Priority queue shrank from `52` to `47` employers (`failed_history_skipped` increased from `15` to `20`), confirming failed Tier 2 inspections (`3` `error`, `2` `blocked`) were persisted accurately and excluded on re-run without altering `data/hub.db` during `--dry-run`.
4. **Live-DB API & Browser Smoke Test:**
   - FastAPI serving a temporary copy of the updated `155`-row `hub.db`: `GET /api/opportunities` returned HTTP `200`, `155` items, `X-Total-Count: 155`; `GET /api/opportunities?suburb=Rockdale&limit=5&offset=0` returned `5` items with `X-Total-Count: 19`.
   - Chromium Playwright smoke check against the updated `155`-row copy: `155` cards rendered, ODbL link visible in drawer, `errors: []`.

## Root Blockers Explaining `0` Verified Jobs in Track 1

1. **Low corporate ATS adoption among small local venues:** Of the `52` priority hospitality/cleaning employers with reachable OSM websites, `42` exposed internal or outbound links, yet only `4` matched ATS footprints (`2` Avature links returning HTTP 404, `1` SmartRecruiters board with 50 roles all outside the 4 target suburbs, and `1` unsupported `applynow` vendor).
2. **Unsupported boutique AU hospitality ATS & JS-rendered forms:** Local Australian hospitality employers frequently use unsupported regional portals (`applynow`, `scouttalent`, `employmenthero`, `dayforce`, custom contact forms) or walk-in/social channels rather than the 131 Career-Ops ATS JSON/HTML endpoints.
3. **Why Track 2 / portal feeds belong in the next delivery:** Achieving `>= 5` verified local casual hospitality/cleaning jobs reliably across these four suburbs requires Track 2 (e.g. authorized job-board/Adzuna feed integration with explicit suburb matching) or targeted AU hospitality ATS support beyond corporate `greenhouse`/`lever`/`smartrecruiters` boards.
