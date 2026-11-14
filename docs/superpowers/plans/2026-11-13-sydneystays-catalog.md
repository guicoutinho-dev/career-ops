# SydneyStays Catalog Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Serve trustworthy persisted opportunities in the current SydneyStays Jobs UI.

**Architecture:** Extend the existing SQLite/FastAPI app with opportunity, employer, and evidence records. Import legacy inputs conservatively, then load the existing Jobs UI from the API with an explicit failure state.

**Tech Stack:** Python 3.12, FastAPI, SQLite, pytest, vanilla JavaScript, Playwright.

**Spec:** `docs/superpowers/specs/2026-11-13-sydneystays-catalog.md`

## Global Constraints

- No new collector, framework, Career Ops update, or redesign.
- Preserve current database and browser saved state; backup before migration.
- Treat all historic claims as unverified until checked evidence exists.
- Keep local data and credentials out of Git.

## Review Focus

- A legacy local-hunt `approved` post must remain readable after repair, with its JSON unchanged.
- A generic careers page must remain an employer lead, even if its old record has a job title and salary.
- A failed API must not make embedded jobs reappear.
- A saved favorite keyed by an old ID must remain attached to its opportunity.
- A transient network failure must not mark an opportunity expired.

---

### Task 1: Reproducible safe baseline

**Files:** `.gitignore`, `sydney-hub/`, sample config, build script, test fixtures.

- [x] Confirm source and SQLite backups, integrity, and Git ignore behavior.
- [x] Version source, assets, and tests explicitly; exclude local seeds, generated HTML, database, backups, and settings.
- [x] Make a clean checkout buildable with empty local seed data and a sample config.
- [x] Run existing Python and static UI tests, then commit the baseline.

### Task 2: Database repair and rollback

**Files:** `sydney-hub/hub/storage/db.py`, migration/rollback CLI, tests.

- [x] Write failing tests for lossless `approved` repair, idempotence, backup, and rollback refusal when DB changed after migration.
- [x] Run tests and observe the expected failures.
- [x] Implement a transactional migration and explicit rollback with SQLite backup/integrity checks.
- [x] Run targeted and full Python tests; apply to the real database only after backup verification; commit.

### Task 3: Unified opportunity records

**Files:** storage/domain modules, composition root, conservative legacy importer, tests.

- [x] Write failing tests for employer lead vs unverified post, unknown fields, post provenance, stable IDs, and idempotent sync.
- [x] Run tests red, implement the smallest schema/import path, then run green.
- [x] Import local historic data without displaying unsupported claims.

### Task 4: Query API

**Files:** `hub/api/app.py`, schemas/storage filters, API tests.

- [x] Write failing tests for result shape, filters, invalid inputs, preserved URL, and no verified legacy records.
- [x] Run tests red, add `GET /api/opportunities`, run green and full Python suite.

### Task 5: Jobs UI integration

**Files:** `web/template.html`, `web/build_dashboard.py`, browser E2E tests.

- [x] Write E2E tests for API data, API failure, legacy saved state, lead labels, desktop/mobile, and source opening.
- [x] Run tests red, connect the existing interface to the API and remove embedded jobs, run green.
- [x] Rebuild generated HTML locally, verify actual database output and browser UI.

### Task 6: Final review and verification

- [x] Run unit, integration, E2E, real-data smoke, `git diff --check`, and secret/ignore checks (116 Python tests and browser E2E passed).
- [x] Request a fresh code review of the branch and fix Important/Critical findings with failing tests first (six findings plus the v1→v2 backup gap resolved and re-reviewed).
- [x] Report corrected problems, live data reaching UI, test results, rollback path, and remaining limits.
