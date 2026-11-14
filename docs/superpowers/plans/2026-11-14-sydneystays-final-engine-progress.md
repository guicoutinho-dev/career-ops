# SydneyStays Final Engine — Verification & Progress Ledger

**Branch:** `codex/sydneystays-final-engine` (worktree `D:\AntigravityProjetos\sydneystays-final-engine`, branched from `5caa292c`)
**Date:** 2026-11-14

## Implemented Commits

1. `d39c8adf` — **Delivery 3 (Community Extraction & Honest Provenance)**
   - `sydney-hub/hub/domain/community_extract.py` (`extract_community_post`)
   - `sydney-hub/hub/storage/catalog.py` (`CatalogRepository.sync_posts`)
   - `sydney-hub/tests/test_community_extract.py`
2. `5a1bca9c` — **Delivery 4 (Deterministic Matching + Subclass 462 88-Day Classifier)**
   - `sydney-hub/hub/domain/matching.py` (`CandidateProfile`, `MatchAssessment`, `evaluate_opportunity_match`, `resolve_unresolved_with_llm`)
   - `sydney-hub/hub/domain/specified_work_462.py` (`RULES_VERSION = "home-affairs-462-2026-11"`, `classify_specified_work_462`)
   - `sydney-hub/tests/test_matching.py`, `sydney-hub/tests/test_specified_work_462.py`
3. `4a197393` — **Delivery 5A (Incremental Refresh + Conservative Lifecycle Expiry, Schema v6)**
   - `sydney-hub/hub/discovery/expiry.py` (`select_incremental_candidates`, `should_recheck`, `is_explicit_expiry`, `is_transient_or_unknown`)
   - `sydney-hub/hub/storage/db.py` (`migrate_lifecycle_catalog`, `ensure_lifecycle_schema`, `.published-v6-rollback.json`)
   - `sydney-hub/hub/storage/catalog.py`, `sydney-hub/rollback_catalog.py`, `sydney-hub/run_published_jobs.py`
   - `sydney-hub/tests/test_expiry_and_refresh.py`
4. `61a78d8b` — **Delivery 5B (Career Prep + API Enrichment + JS Engine)**
   - `sydney-hub/hub/career/prep.py` (`load_candidate_facts`, `prepare_application_packet` with `auto_submit=False`)
   - `sydney-hub/hub/api/app.py` (`/api/opportunities`, `/api/opportunities/summary`, `POST /api/opportunities/{id}/prepare`), `app.py`, `jobs_app.py`
   - `sydney-hub/web/jobs-v5-engine.js`
   - `sydney-hub/tests/test_career_prep.py`, `sydney-hub/tests/test_final_engine_api.py`, `sydney-hub/tests/test_jobs_v5_engine.mjs`
5. `57f6cd17` — **Delivery 5C (Jobs UI: Channel Health, 88-Day Mode, Match/462 Panels, Manual Prep)**
   - `sydney-hub/web/template.html`, `sydney-hub/web/atlas.css`, `sydney-hub/web/index.html` (gitignored build output)
   - `sydney-hub/tests/test_jobs_ui.mjs`
6. `354b8e21` — **Source-of-Truth Hardening**
   - Restricted `load_candidate_facts` strictly to `config/profile.yml` and `cv.md` (excluding `USER.md` / `*.example.*`).

## Verification Results (Post-Final-Code-Edit)

| Check | Command | Result |
|---|---|---|
| Python Full Suite | `python -m pytest -ra` (in `sydney-hub/`) | **313 passed, 2 skipped** (`osmium` optional PBF tests) |
| JS Engine Unit Suite | `node --test tests/test_jobs_v5_engine.mjs` | **7 passed, 0 failed** |
| Playwright UI E2E | `node tests/test_jobs_ui.mjs http://127.0.0.1:18767` (fixture desktop + mobile + final engine panels) | **Passed (exit 0)** |
| Git Diff Hygiene | `git diff --check 5caa292c..HEAD` | **Passed (clean)** |
| Rollback Drill | `migrate_published_catalog` (v5) -> `migrate_lifecycle_catalog` (v6) -> `restore_catalog_backup(..., force=True, target_version=6)` -> v5 | `ROLLBACK_DRILL_OK v6=6(ok) -> v5=5(ok)` |
| Protected DB Check | SHA-256 of `D:\AntigravityProjetos\career-ops\sydney-hub\data\hub.db` | `F0E13096464FF683B2263D90098768A718645E9BEF16B4BAA5FD6C7C150B11FC` (unchanged) |

*Note:* The earlier isolated `jobs_app.py` E2E against a temp copy of `hub.db` (150 records: 130 community + 20 OSM leads) ran prior to commit `354b8e21` and passed (`Preview E2E (mixed) passed: 20 leads, 130 community, 0 verified`), while surfacing the `USER.md` bug fixed and covered in `354b8e21`.
