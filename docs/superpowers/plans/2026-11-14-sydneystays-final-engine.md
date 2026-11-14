# SydneyStays Final Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans or direct TDD execution task-by-task.

**Goal:** Complete Deliveries 3, 4, and 5 of `docs/superpowers/specs/2026-11-13-sydneystays-final-engine.md` on top of the verified V4+V5 operational baseline (`5caa292c`), delivering the definitive working SydneyStays discovery, matching, 88-day assessment, conservative lifecycle, and career-prep engine.

**Base:** `codex/sydneystays-v4-v5-preview` (`5caa292c`) in isolated worktree `D:\AntigravityProjetos\sydneystays-final-engine` on branch `codex/sydneystays-final-engine`.

## Global Constraints
- Never mutate protected V4/main DBs (`D:\AntigravityProjetos\career-ops\sydney-hub\data\hub.db`, `C:\Users\guicx\.codex\worktrees\sydneystays-employer-pilot\career-ops\sydney-hub\data\hub.db`).
- Keep `hub/domain/*` pure (no I/O, no imports from other `hub` modules).
- Only `hub/storage/*` imports `sqlite3`.
- Never infer an employer from a Facebook post author or a certification from a role name.
- Never promote leads/unverified posts to `verified_job`; never turn network errors/403/429/timeouts into `expired`.
- Never guarantee 88-day visa credit from insufficient facts; show `needs_review` with missing facts.
- Never auto-submit applications; never fabricate candidate or opportunity claims.

## Tasks
1. **Task 1 (Delivery 3): Conservative Community Post Extractor & Excerpt Provenance**
   - `hub/domain/community_extract.py`, `hub/storage/catalog.py`, `tests/test_community_extract.py`
2. **Task 2 (Delivery 4A): Deterministic Matching Engine, Unresolved Hook & Suspicious Signals**
   - `hub/domain/matching.py`, `hub/api/app.py`, `web/jobs-v5-engine.js`, `tests/test_matching.py`
3. **Task 3 (Delivery 4B): Versioned Subclass 462 Specified Work Classifier & Separate 88-Day View**
   - `hub/domain/specified_work_462.py`, `hub/storage/catalog.py`, `hub/api/app.py`, `web/jobs-v5-engine.js`, `web/template.html`, `tests/test_specified_work_462.py`
4. **Task 4 (Delivery 5A): Schema v6 Conservative Expiry, Incremental Refresh & Rollback**
   - `hub/storage/db.py`, `hub/discovery/expiry.py`, `hub/storage/catalog.py`, `run_published_jobs.py`, `rollback_catalog.py`, `tests/test_expiry_and_refresh.py`
5. **Task 5 (Delivery 5B): Career Intelligence Application Prep, Visible Channel Health UI & Full Verification**
   - `hub/career/prep.py`, `hub/api/app.py`, `web/template.html`, `tests/test_career_prep.py`, `tests/test_preview_e2e.mjs`, `tests/test_jobs_ui.mjs`
