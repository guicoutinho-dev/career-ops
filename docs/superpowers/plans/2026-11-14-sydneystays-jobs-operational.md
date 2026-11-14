# SydneyStays Jobs Operational Local Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (Native flow) or superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement every missing operational component so the SydneyStays Published Jobs motor runs locally and observably on an isolated SQLite catalog without Facebook—enforcing persisted fail-closed source policies, specific-listing verification, exact-destination geocoding without fabricating suburb centroids, persistent `429` cooldowns, 7-day yield journaling, per-listing 100% audit verification, and opt-in scheduling strictly gated behind pilot acceptance.

**Architecture:** Extend `sydney-hub` inside the existing Modular Monolith (`hub/settings.py`, `hub/storage/`, `hub/discovery/`, `hub/scheduler/`, `hub/api/`) and add a dedicated `jobs_app.py` composition root serving FastAPI + Olympian Atlas Jobs UI on `127.0.0.1` without `MaestriPortalScraper`, `MaestriAgentClassifier`, or Facebook `RoutineRunner`. Every source and individual ATS board (`ats:<provider>:<slug>`) requires an explicit persisted `SourcePolicy` (`publish_allowed` defaults to `false`, removing `run_published_jobs.py:159`'s hardcoded `True`). `--live` runs validate the target `--db` against the marker-approved isolated directory (`data/jobs_isolated/`) before schema/snapshot/SQLite open, write only to an isolated `data/jobs_isolated/jobs_hub.db` snapshot (`?mode=ro` + `.backup()`), capture a pre-run rollback manifest, log HTTP requests/dedup/cost/bytes to `data/pilot-reports/yield_journal.jsonl`, obey `Retry-After` cooldowns without bypass, and keep Adzuna and APScheduler fail-closed behind explicit 7-day + audit gates.

**Tech Stack:** Python 3.12 (FastAPI, Uvicorn, Pydantic v2, SQLite3 URI read-only backup, httpx, PyYAML, APScheduler, pytest), Node.js 24 (`providers/*.mjs` bridge, `node:test`, Playwright).

**Spec:** `docs/superpowers/specs/2026-11-14-sydneystays-published-jobs-design.md`  
**Pilot Baseline & Measured Constraints:** `docs/superpowers/reviews/2026-11-14-published-jobs-pilot.md`  
**Checkout & Base Commit:** `D:\AntigravityProjetos\sydneystays-v4-v5-preview`, branch `codex/sydneystays-v4-v5-preview`, base `e53e9199`.  
**Execution Scope:** Local implementation and isolated local live pilot only (already authorized by the user; no push, no merge, no deploy).

---

## Global Constraints

- **Local scope only — no push, merge, deploy, Career Ops upgrade, or UI redesign.** Never mutate `C:\Users\guicx\.codex\worktrees\sydneystays-employer-pilot\career-ops\sydney-hub\data\hub.db`, `D:\AntigravityProjetos\career-ops\sydney-hub\data\hub.db`, `sites\sydneystays-publish\`, or `sites\sydney-opportunity-hub\`.
- **Fail-closed isolated DB guard BEFORE SQLite/schema/snapshot:** All `--db` CLI options, `bootstrap_isolated_jobs_db`, `run_published_jobs`, `jobs_app.py`, and `rollback_catalog.py --pre-run` MUST validate the resolved target path **before** calling `ensure_published_schema`, `snapshot_published_run`, `sqlite3.connect()`, or creating sidecars. Any path pointing to V4/main DBs or resolving (including via `..` or symlinks) outside the marker-approved isolated directory (`sydney-hub/data/jobs_isolated/` containing `.sydneystays-jobs-isolated`, or an explicit test `isolated_root` containing `.sydneystays-jobs-isolated`) is rejected unconditionally with zero modification to hash or `mtime`.
- **No Facebook in the Jobs-only local service.** `jobs_app.py` must never invoke `resolve_maestri()`, `MaestriPortalScraper`, `MaestriAgentClassifier`, `catalog.sync_posts()`, or `import_legacy_seed()`. `app.py`'s existing Facebook scheduler stays separate and is never started by `jobs_app.py`.
- **Persisted fail-closed source policies & attribution/terms gates (no assumed commercial license):**
  - **Workable XML (`https://www.workable.com/boards/workable.xml`):** Per [Workable Help](https://help.workable.com/hc/en-us/articles/4420464031767-Utilizing-the-XML-Job-Feed), the feed contains *Jobs by Workable* postings, updates hourly, serves job boards/partners, and **each posting `<url>` must NOT be altered** (store verbatim in `opportunities.source_url`; record any verified canonical listing/API check URL consistently in `opportunity_evidence.evidence_url` and `ListingCheck.evidence_url`). Measured pilot size: **139.94 MiB** gzip -> **834.27 MiB** XML (**171,081** `<job>` elements, **3,333** `AU`, **41** eligible pre-refinement), subject to Cloudflare **`HTTP 429` (`Retry-After: 3584`/`3600`)**. Default `publish_allowed: false` in `source_policies.yml` until terms review is explicitly recorded; when `429` cooldown is active, respect `Retry-After` strictly with zero bypass or retries.
  - **Official ATS boards (`published_boards.yml` + per-board `source_policies.yml`):** Restrict `_BOARD_HOSTS` strictly to bridge-verified providers already supported in `published_ats.py` (`greenhouse`, `lever`, `ashby`). Do not add other ATS vendors without first verifying their `providers/<id>.mjs` bridge contract against `ats_bridge.mjs`. Policy authorization MUST be per individual board (`ats:<provider>:<slug>`, e.g. `ats:greenhouse:acmecafe`), NEVER provider-wide wildcard inheritance (`ats:greenhouse` cannot auto-approve arbitrary Greenhouse boards). Every manifest entry requires independent provenance (`discovery_origin`, non-circular employer `referrer_url`, `evidence_ref`, `verified_at`); reject `Riley-Dorrington`/`CC BY-NC`/Adzuna/aggregator origins and self-referencing boards.
  - **TfNSW Open Data (`https://opendata.transport.nsw.gov.au/data/dataset/trip-planner-apis`):** Stop Finder (`/v1/tp/stop_finder`) + Trip Planner (`/v1/tp/trip`), free Bronze plan (60,000 calls/day, 5 req/s), requires key + `TFNSW_FREE_QUOTA_CONFIRMED=1` + terms/attribution review.
  - **Adzuna AU:** Spending cap **AU$0**. Never insert Adzuna adverts into `CatalogRepository` (`publish_allowed: false`, `eval_only: true`) without confirmed commercial/publication license; evaluation runs only with `--evaluate-adzuna`, `ADZUNA_FREE_QUOTA_CONFIRMED=1`, and local credentials.
- **Honest evidence, exact-destination geocoding, and 7-day pilot reality:**
  - `verified_job` requires specific-listing evidence (`source_type='job_listing'`, `supports_job=1`, non-null `checked_at`) and rejects closed postings and generic careers/search pages (`title + \bapply\b` alone is insufficient).
  - Workable XML and ATS bridge listings currently return `lat=None, lng=None`, so `TFNSW_API_KEY` alone cannot meet the `>= 10` `<= 45 min` commute target. Only explicit API/JSON-LD coordinates or an explicit NSW street address (`streetAddress` + suburb/postcode) geocoded with recorded evidence may produce `location_tier="exact"` and `commute_observations`. Generic localities (`"Sydney NSW"`) and bare suburb names (`"Rockdale NSW"`) MUST stay `unknown`/`suburb_only` and `commute_unknown=True`—never fabricate straight-line or suburb-centroid commutes. If `< 10` exact destinations are measurable, mark the `10_commute_le_45` gate blocked/`not_met` and keep the scheduler inactive.
  - **7-day acceptance is real multi-day observation, never promised in code.**
- **Localhost & credential hygiene:** Bind `127.0.0.1` / `localhost` only; never commit or print credentials, `.db` files, or personal config YAML.

---

## Review Focus

1. **Hardcoded `SourcePolicy(..., True)` in `run_published_jobs.py:159` or provider-wide ATS wildcard writing unapproved boards/sources to the catalog:** An unapproved source or an unapproved individual ATS board (`ats:greenhouse:unapproved`) must execute zero catalog writes even with `--live` and even when another board on the same ATS provider is approved. **Pinned tests:** `test_published_runner.py::test_live_run_on_unapproved_source_performs_zero_catalog_writes`, `test_source_policy.py::test_ats_policy_is_per_board_and_never_inherits_provider_approval`.
2. **HTML `title + \bapply\b` false positives on generic careers lists or closed jobs (`verify_job_page`) & Workable URL integrity:** Generic careers directories or `"Applications closed"` pages with a footer `"Apply"` link must never promote to `verified_job`; Workable `opportunities.source_url` stays verbatim while `opportunity_evidence.evidence_url` records the verified canonical/API URL consistently. **Pinned tests:** `test_published_runner.py::test_verify_job_page_rejects_closed_or_generic_pages_and_requires_specific_evidence`, `test_published_catalog.py::test_published_upsert_rejects_adzuna_and_preserves_explicit_suburb_and_verbatim_url`.
3. **Generic localities or suburb centroids leaking into `exact` / `commute_observations`:** Workable/ATS return `lat=None, lng=None`; falling back to suburb centroids fabricates strict `<= 45 min` commutes, while generic `"Sydney NSW"` must stay `unknown`. **Pinned test:** `test_geocode.py::test_generic_location_and_suburb_only_remain_unknown_while_explicit_street_address_geocodes`.
4. **Isolated DB guard bypass, V4/main path mutation before schema/snapshot, or missing `.published-v5-rollback.json` crash:** `--db` / `bootstrap_isolated_jobs_db` / runner / service must reject V4/main paths and any path resolving outside the marker-approved isolated directory BEFORE calling `ensure_published_schema` or `snapshot_published_run`, preserving SHA-256 and `mtime` (`st_mtime_ns`) untouched. **Pinned tests:** `test_jobs_bootstrap.py::test_rejects_v4_main_and_outside_db_paths_before_schema_or_snapshot_leaving_hash_and_mtime_untouched`, `test_jobs_bootstrap.py::test_bootstrap_isolated_jobs_db_preserves_source_sha_refuses_existing_and_handles_v5`.
5. **Scheduler bypassing 7-day gate, Workable `429` cooldown bypassed, generic audit confirmation, or `jobs_app.py` touching Facebook:** Scheduler must remain blocked on Day 1 (`seven_day_status="pending"`) even when `20/10/5` and 100% per-listing audit pass; cooldowns cannot be bypassed; Facebook routes return `409`. **Pinned tests:** `test_jobs_scheduler.py::test_scheduler_gate_blocks_day_1_even_when_20_10_5_and_audit_pass`, `test_published_runner.py::test_workable_cooldown_persists_across_runs_without_bypass`, `test_pilot_gate.py::test_confirm_pilot_audit_requires_per_listing_records_for_100_percent_of_verified_jobs`, `test_jobs_service.py::test_jobs_only_service_disables_facebook_and_blocks_fb_mutations`.

---

## File Structure

| File | Action | Responsibility |
| --- | --- | --- |
| `sydney-hub/.gitignore` | Modify | Ignore `config/jobs.yml`, `config/source_policies.yml`, `config/published_boards.yml`, `data/jobs_isolated/`, `data/jobs_hub.db*`, `data/published-state/`, `data/pilot-reports/` |
| `sydney-hub/config/jobs.example.yml` | Create | Jobs-only operational config template (`127.0.0.1:8796`, isolated `data/jobs_isolated/jobs_hub.db`, `scheduler_enabled: false`, `geocode_street_addresses: false`, zero secrets) |
| `sydney-hub/config/source_policies.example.yml` | Create | Fail-closed persisted source policy registry (`publish_allowed: false` defaults, per-board `ats:<provider>:<slug>` entries, Workable/TfNSW/Adzuna terms, quotas, attribution, `cost_cap_aud: 0.0`) |
| `sydney-hub/hub/settings.py` | Modify | Add `PublishedJobsSettings`, `JobsConfig`, `load_jobs_settings(path: Path)` (no Facebook fields required) |
| `sydney-hub/hub/discovery/source_policy.py` | Create | Load/validate `source_policies.yml` fail-closed per source and per individual ATS board (`resolve_source_policy` / `resolve_board_policy`, never inheriting provider-level approval for unapproved boards) |
| `sydney-hub/hub/storage/db.py` | Modify | Add `ensure_published_schema`, `snapshot_published_run`, `restore_published_run` |
| `sydney-hub/hub/storage/jobs_bootstrap.py` | Create | Strict `validate_isolated_jobs_db_path` (marker `.sydneystays-jobs-isolated` + `resolve()` inside approved isolated dir + V4/main path refusal BEFORE schema/snapshot) & `?mode=ro` + `.backup()` bootstrapper |
| `sydney-hub/rollback_catalog.py` | Modify | Support `--target-version 5` and `--pre-run` restore on validated isolated Jobs DB |
| `sydney-hub/hub/discovery/published_ats.py` | Modify | Keep `_BOARD_HOSTS` strictly to verified `greenhouse`, `lever`, `ashby`; `<= 2` same-host career redirects; independent provenance/origin checks; CC BY-NC/aggregator rejection; `audit_official_boards_manifest` |
| `sydney-hub/hub/storage/catalog.py` | Modify | Enforce `policy.publish_allowed` + unconditional Adzuna refusal; preserve Workable `<url>` verbatim in `opportunities.source_url`; extract explicit `suburb`/`state` (never from `"Sydney NSW"`) and `ats_provider`; record failed recheck outcomes without demoting `verified_job` |
| `sydney-hub/hub/discovery/geocode.py` | Create | Extract explicit API/JSON-LD coordinates or explicit NSW street address (`streetAddress`); opt-in bounded free geocoding (`1 req/s`, `AU$0`) with recorded provenance; generic/suburb-only stay `unknown`/`suburb_only` |
| `sydney-hub/tests/test_geocode.py` | Create | TDD suite pinning exact-address extraction/geocoding vs generic/suburb-only `unknown` preservation |
| `sydney-hub/hub/discovery/health.py` | Create | Persistent `SourceHealthStore`, `parse_retry_after`, 429 cooldown circuit-breaker (no bypass), request/bytes/dedup/cost/yield telemetry, append-only `yield_journal.jsonl` |
| `sydney-hub/run_published_jobs.py` | Modify | Replace hardcoded `SourcePolicy(..., True)` with `source_policies.yml`; harden `verify_job_page`; integrate explicit-address geocoding + TfNSW; persist run report + `yield_journal.jsonl`; add `--config`, `--db`, `--policies`, `--state-dir`, `--skip-workable`, `--audit-boards`, `--pilot-status`, `--confirm-pilot-audit` (no `--ignore-cooldown`) |
| `sydney-hub/hub/discovery/pilot_gate.py` | Create | Honest Spec Section 5 `PilotScorecard`, 7-day journal span, separated External Gates (`E1`–`E5`), 100% per-listing audit validation (`--audit-file`) before setting `manual_audit_confirmed=True` |
| `sydney-hub/hub/scheduler/jobs_scheduler.py` | Create | Fail-closed `evaluate_scheduler_gate` + `PublishedSchedulerService` (opt-in only after pilot gate + per-listing audit confirmation, serialized, Workable >=20h floor) |
| `sydney-hub/jobs_app.py` | Create | Jobs-only local service (`127.0.0.1:8796`, zero Facebook/Maestri, optional gate-guarded scheduler) |
| `sydney-hub/hub/api/app.py` | Modify | Backwards-compatible `jobs_only`, `jobs_health_fn`, `jobs_runner_fn`, `jobs_running_fn` on `AppDeps`; add `GET /api/jobs/health`, `POST /api/jobs/runs`; fail closed Facebook routes when `jobs_only=True` |
| `sydney-hub/web/jobs-v5-engine.js` | Modify | Map backend `role_bucket` tokens (`barista`, `bartender`, `kitchen`, `front_of_house`, `hospitality`, `cleaner`, `housekeeping`, `retail`, `warehouse`) in `ROLE_BUCKET_LABELS` |
| `sydney-hub/tests/test_source_policy.py`, `test_jobs_bootstrap.py`, `test_published_health.py`, `test_pilot_gate.py`, `test_jobs_scheduler.py`, `test_jobs_service.py` | Create | Unit & integration TDD suites |
| `sydney-hub/tests/test_settings.py`, `test_published_ats.py`, `test_published_catalog.py`, `test_published_runner.py`, `test_api.py`, `test_jobs_v5_engine.mjs` | Modify | Extend existing test suites |

---

### Task 1: Isolated Config, Fail-Closed Source Policy Registry & DB Bootstrap/Rollback

**Files:**
- Modify: `sydney-hub/.gitignore`
- Create: `sydney-hub/config/jobs.example.yml`, `sydney-hub/config/source_policies.example.yml`
- Modify: `sydney-hub/hub/settings.py`, `sydney-hub/hub/storage/db.py`, `sydney-hub/rollback_catalog.py`
- Create: `sydney-hub/hub/discovery/source_policy.py`, `sydney-hub/hub/storage/jobs_bootstrap.py`
- Test: `sydney-hub/tests/test_settings.py`, `sydney-hub/tests/test_source_policy.py`, `sydney-hub/tests/test_jobs_bootstrap.py`

**Interfaces:**
- Produces:
  - `PublishedJobsSettings` (`db_path="data/jobs_isolated/jobs_hub.db"`, `isolated_db_dir="data/jobs_isolated"`, `policies_path="config/source_policies.yml"`, `boards_path="config/published_boards.yml"`, `state_dir="data/published-state"`, `reports_dir="data/pilot-reports"`, `max_verifications=30`, `workable_enabled=True`, `workable_cooldown_seconds=3600`, `workable_min_interval_hours=20`, `geocode_street_addresses=False`, `origin_label="Rockdale Station"`, `scheduler_enabled=False`, `schedule=[]`, `adzuna_evaluate_only=True`)
  - `JobsConfig(server: ServerSettings, paths: PathSettings, published_jobs: PublishedJobsSettings, root: Path)` + `load_jobs_settings(path: Path) -> JobsConfig`
  - `SourcePolicyEntry` + `load_source_policies(path: Path) -> dict[str, SourcePolicyEntry]` + `resolve_source_policy(policies: dict[str, SourcePolicyEntry], source_id: str) -> SourcePolicy` + `resolve_board_policy(policies: dict[str, SourcePolicyEntry], provider_id: str, slug: str) -> SourcePolicy` (fail-closed: `publish_allowed=False` if missing, `terms_reviewed_at is None`, `cost_cap_aud > 0`, `eval_only=True`, or `source_id.startswith("adzuna")`; ATS board policies strictly require exact `ats:<provider>:<slug>` key and NEVER inherit `publish_allowed=True` from a provider-level key)
  - `ISOLATED_DB_MARKER = ".sydneystays-jobs-isolated"` + `validate_isolated_jobs_db_path(db_path: str | Path, *, isolated_root: Path, require_marker: bool = True) -> Path` (resolves symlinks/`..`, explicitly refuses known V4/main `hub.db` paths or any path outside `isolated_root`, and checks `.sydneystays-jobs-isolated` marker BEFORE any SQLite open, `ensure_published_schema`, or `snapshot_published_run`)
  - `ensure_published_schema(path: str | Path, *, isolated_root: Path) -> int`, `snapshot_published_run(path: str | Path, *, isolated_root: Path) -> Path`, `restore_published_run(path: str | Path, *, isolated_root: Path, force: bool = False) -> Path | None` (each calls `validate_isolated_jobs_db_path` FIRST!)
  - `bootstrap_isolated_jobs_db(source_db: Path | None, dest_db: Path, *, isolated_root: Path) -> dict[str, object]` (no overwrite flag; validates `dest_db` inside `isolated_root` BEFORE creating directories/marker or copying; always refuses existing `dest_db` and `source_db == dest_db`)

- [ ] **Step 1: Write failing tests for config, per-board fail-closed policy registry, pre-schema/pre-snapshot isolated DB guard, and rollback**

In `sydney-hub/tests/test_settings.py`:
- `test_loads_jobs_settings_without_facebook_fields_and_refuses_non_local(tmp_path)`: verifies `load_jobs_settings` fallback (`db_path == "data/jobs_isolated/jobs_hub.db"`), `scheduler_enabled is False`, `adzuna_evaluate_only is True`, `geocode_street_addresses is False`, and `ValueError` on `0.0.0.0`.

In `sydney-hub/tests/test_source_policy.py`:
- `test_unapproved_or_adzuna_policy_fails_closed_at_registry_and_catalog(tmp_path)`:
  - `source_policies.example.yml` yields `publish_allowed is False` for `workable`, `ats:greenhouse:acmecafe`, `ats:lever:acmecafe`, `ats:ashby:acmecafe`, and `adzuna_au`.
  - Unknown `source_id` resolves to `SourcePolicy(source_id, publish_allowed=False, ...)`.
  - `publish_allowed: true` without `terms_reviewed_at` raises `ValueError`; `publish_allowed: true` on `adzuna_au` raises `ValueError`.
  - Approved `workable` (`terms_reviewed_at: "2026-11-14"`, `publish_allowed: true`, `cost_cap_aud: 0.0`) resolves `publish_allowed=True` with attribution `"Jobs by Workable"`.
- `test_ats_policy_is_per_board_and_never_inherits_provider_approval(tmp_path)`:
  - Even if `ats:greenhouse` or `ats:greenhouse:approved-board` is approved with `publish_allowed: true`, `resolve_board_policy(policies, "greenhouse", "unapproved-board")` resolves `publish_allowed=False` (`source_id == "ats:greenhouse:unapproved-board"`).

In `sydney-hub/tests/test_jobs_bootstrap.py`:
- `test_rejects_v4_main_and_outside_db_paths_before_schema_or_snapshot_leaving_hash_and_mtime_untouched(tmp_path)`:
  - Create real SQLite files mimicking V4 (`career-ops/sydney-hub/data/hub.db`), main (`data/hub.db`), and an outside path `tmp_path / "outside.db"`, plus a symlink inside `isolated_root` pointing to `outside.db`. Record each file's exact `sha256` and `st_mtime_ns`.
  - Call `validate_isolated_jobs_db_path`, `bootstrap_isolated_jobs_db(..., outside_dest, isolated_root=isolated_root)`, `ensure_published_schema(outside_db, isolated_root=isolated_root)`, and `snapshot_published_run(outside_db, isolated_root=isolated_root)` (plus missing-marker `isolated_root`).
  - Assert every call raises `ValueError`/`PermissionError` **before** touching SQLite or creating any `.rollback.json`/backup sidecar, and assert every outside DB's `sha256` and `st_mtime_ns` remain 100% unchanged!
- `test_bootstrap_isolated_jobs_db_preserves_source_sha_refuses_existing_and_handles_v5(tmp_path)`:
  - Copies a `user_version=5` DB lacking `.published-v5-rollback.json` via `?mode=ro` + `.backup()` into marked `isolated_root`; asserts source SHA-256 and `st_mtime_ns` unchanged, zero `-wal`/`-shm` next to source, `ensure_published_schema(dest, isolated_root=isolated_root) == 5`, and `PRAGMA integrity_check == "ok"`.
  - Asserts `bootstrap_isolated_jobs_db(src, src, isolated_root=isolated_root)` raises `ValueError` and `bootstrap_isolated_jobs_db(src, existing_dest, isolated_root=isolated_root)` raises `FileExistsError` unconditionally.
- `test_pre_run_snapshot_and_rollback_restores_exact_v5_state(tmp_path)`: `snapshot_published_run` -> mutate -> `restore_published_run(..., isolated_root=isolated_root, force=True)` restores exact pre-run rows and `integrity_check == "ok"`.

- [ ] **Step 2: Run RED tests**

Run in `D:\AntigravityProjetos\sydneystays-v4-v5-preview\sydney-hub`:
`python -m pytest -ra tests/test_settings.py tests/test_source_policy.py tests/test_jobs_bootstrap.py`
Expected: FAIL (`ImportError` / `ModuleNotFoundError`).

- [ ] **Step 3: Implement config, fail-closed source policy registry, and isolated DB bootstrap/rollback**

1. Update `sydney-hub/.gitignore` (`config/jobs.yml`, `config/source_policies.yml`, `config/published_boards.yml`, `data/jobs_isolated/`, `data/jobs_hub.db*`, `data/published-state/`, `data/pilot-reports/`).
2. Create `sydney-hub/config/jobs.example.yml` (`127.0.0.1:8796`, `data/jobs_isolated/jobs_hub.db`, `isolated_db_dir: data/jobs_isolated`, `scheduler_enabled: false`, `geocode_street_addresses: false`) and `sydney-hub/config/source_policies.example.yml` documenting Workable XML (hourly feed, `<url>` unaltered, job-board partner model), per-board Official ATS examples (`ats:greenhouse:example`, `ats:lever:example`, `ats:ashby:example` — never provider-wide wildcard approval), TfNSW Trip Planner (`Bronze free`, `60000/day`, `5/s`), and Adzuna AU (`eval_only: true`, `publish_allowed: false`, `cost_cap_aud: 0.0`)—all `publish_allowed: false` by default.
3. Implement `PublishedJobsSettings`, `JobsConfig`, `load_jobs_settings` in `sydney-hub/hub/settings.py`.
4. Implement `sydney-hub/hub/discovery/source_policy.py` with strict Pydantic validation and per-board `resolve_board_policy` (failing closed whenever exact `ats:<provider>:<slug>` approval is absent).
5. Implement `validate_isolated_jobs_db_path` (marker `.sydneystays-jobs-isolated` + `resolve()` inside `isolated_root` + V4/main path refusal BEFORE SQLite open/schema/snapshot) and `bootstrap_isolated_jobs_db` in `sydney-hub/hub/storage/jobs_bootstrap.py`. Add `ensure_published_schema`, `snapshot_published_run`, `restore_published_run` (calling `validate_isolated_jobs_db_path` FIRST, manifest `.published-run-rollback.json`, exclusive lock, active WAL/SHM refusal) in `sydney-hub/hub/storage/db.py`, and add `--target-version 5` / `--pre-run` with isolated DB validation to `sydney-hub/rollback_catalog.py`.

- [ ] **Step 4: Run GREEN tests**

Run: `python -m pytest -ra tests/test_settings.py tests/test_source_policy.py tests/test_jobs_bootstrap.py tests/test_storage.py`
Expected: PASS.

- [ ] **Step 5: Commit Task 1**

```bash
git add sydney-hub/.gitignore sydney-hub/config/jobs.example.yml sydney-hub/config/source_policies.example.yml sydney-hub/hub/settings.py sydney-hub/hub/discovery/source_policy.py sydney-hub/hub/storage/db.py sydney-hub/hub/storage/jobs_bootstrap.py sydney-hub/rollback_catalog.py sydney-hub/tests/test_settings.py sydney-hub/tests/test_source_policy.py sydney-hub/tests/test_jobs_bootstrap.py
git commit -m "feat(jobs): add fail-closed source policies, isolated config, and DB rollback"
```

---

### Task 2: Bridge-Verified Official ATS Manifest Audit & Catalog Guards

**Files:**
- Modify: `sydney-hub/hub/discovery/published_ats.py`
- Modify: `sydney-hub/hub/storage/catalog.py`
- Test: `sydney-hub/tests/test_published_ats.py`, `sydney-hub/tests/test_published_catalog.py`

**Interfaces:**
- Produces in `hub/discovery/published_ats.py`:
  - `BoardAuditResult(company: str, provider_id: str, slug: str, board_url: str, referrer_url: str, discovery_origin: str, evidence_ref: str, verified_at: str, valid: bool, reason: str)`
  - `validate_board_provenance(entry: dict) -> tuple[bool, str]` (requires independent `discovery_origin in {"employer_careers_page", "employer_direct_link", "operator_verified_careers"}`, non-empty `evidence_ref` + ISO `verified_at`, non-circular `referrer_url != board_url`, and rejects `"adzuna"`, `"riley_dorrington"`, `"job_board_aggregator"`, `"cc_by_nc"`, `"self_inferred"`)
  - `audit_official_boards_manifest(path: Path, client: httpx.Client) -> list[BoardAuditResult]`
  - Restricts `_BOARD_HOSTS` strictly to bridge-verified providers already supported in `published_ats.py`: `{"greenhouse", "lever", "ashby"}`; allows `<= 2` same-registrable-host HTTPS redirects on `referrer_url` (`_public_url` on every hop); rejects `Riley-Dorrington`/`CC BY-NC`/Adzuna provenance and entries failing `validate_board_provenance`.
- Modifies `CatalogRepository.upsert_published_listing`:
  - Enforces `policy.publish_allowed` AND rejects `listing.source_id.startswith("adzuna")` unconditionally.
  - Enforces Workable URL contract: `opportunities.source_url` stores Workable `<url>` **verbatim** (never altered/rewritten), while `opportunity_evidence.evidence_url` stores `check.evidence_url` (verified canonical/API URL) consistently without overwriting `opportunities.source_url`.
  - Populates explicit `suburb`/`state` (never from `"Sydney NSW"`/`"Greater Sydney"`) and `ats_provider`; records non-`ok` recheck outcomes on `source_feed` evidence without demoting an existing `verified_job`.

- [ ] **Step 1: Write failing tests for manifest provenance/audit, restricted bridge-verified providers, and catalog guards**

In `sydney-hub/tests/test_published_ats.py`:
- `test_manifest_requires_independent_provenance_and_rejects_aggregators_or_self_origin(tmp_path)`:
  - Rejects entries missing `discovery_origin`/`evidence_ref`/`verified_at`, entries with `discovery_origin="adzuna"` or `"job_board_aggregator"` or `"riley_dorrington"`, and circular self-referencing `referrer_url == board_url`.
  - Accepts entries with independent `discovery_origin="employer_careers_page"`, distinct `referrer_url`, `evidence_ref`, and ISO `verified_at`.
- `test_manifest_rejects_cc_by_nc_unverified_providers_and_cross_host_redirects(tmp_path)`:
  - Rejects `Riley-Dorrington`/`job-board-aggregator` provenance and rejects unverified provider IDs outside `{"greenhouse", "lever", "ashby"}`.
  - Allows `<= 2` same-registrable-host HTTPS redirects (`cafe.com.au/careers` -> `www.cafe.com.au/careers/` linking to `https://job-boards.greenhouse.io/acmecafe`).
  - Rejects cross-host redirects (`other.example`) and private IPs; `audit_official_boards_manifest` returns structured `BoardAuditResult` without raising.

In `sydney-hub/tests/test_published_catalog.py`:
- `test_published_upsert_rejects_adzuna_and_preserves_explicit_suburb_and_verbatim_url(tmp_path)`:
  - `source_id="adzuna_au"` raises `PermissionError` even if `SourcePolicy(..., True)` is passed.
  - `"Rockdale NSW"` + `ats:greenhouse:acmecafe` sets `suburb="Rockdale"`, `state="NSW"`, `ats_provider="greenhouse"`; `"Sydney NSW"` leaves `suburb=None`.
  - Workable `listing.source_url="https://apply.workable.com/j/ABC"` + `check.evidence_url="https://www.workable.com/api/v2/accounts/cafe/jobs/ABC"` keeps `opportunities.source_url == "https://apply.workable.com/j/ABC"` verbatim while storing `evidence_url == "https://www.workable.com/api/v2/accounts/cafe/jobs/ABC"` in `opportunity_evidence` (matching `ListingCheck.evidence_url`).
  - Failed recheck preserves `verification_state == "verified_job"` and `last_verified_at`.

- [ ] **Step 2: Run RED tests**

Run: `python -m pytest -ra tests/test_published_ats.py tests/test_published_catalog.py`
Expected: FAIL.

- [ ] **Step 3: Implement manifest audit and catalog guards**

Implement `audit_official_boards_manifest`, `<= 2` same-host redirect handling, and `CC BY-NC`/Adzuna provenance rejection in `published_ats.py` (keeping `_BOARD_HOSTS` strictly at `greenhouse`, `lever`, `ashby`). Update `CatalogRepository.upsert_published_listing` with explicit suburb extraction, `ats_provider`, verbatim `source_url` preservation, and unconditional Adzuna refusal.

- [ ] **Step 4: Run GREEN tests**

Run: `python -m pytest -ra tests/test_published_ats.py tests/test_published_catalog.py tests/test_ats_bridge.py`
Expected: PASS.

- [ ] **Step 5: Commit Task 2**

```bash
git add sydney-hub/hub/discovery/published_ats.py sydney-hub/hub/storage/catalog.py sydney-hub/tests/test_published_ats.py sydney-hub/tests/test_published_catalog.py
git commit -m "feat(jobs): add bridge-verified ATS manifest audit and catalog guards"
```

---

### Task 3: Exact-Destination Extraction & Non-Fabricating Geocoding

**Files:**
- Create: `sydney-hub/hub/discovery/geocode.py`
- Create: `sydney-hub/tests/test_geocode.py`
- Modify: `sydney-hub/hub/discovery/published_filter.py` (add optional `exact_location_basis` clarity if helpful, preserving existing signature)

**Interfaces:**
- Produces in `hub/discovery/geocode.py`:
  - `ExtractedLocationEvidence(lat: float | None, lng: float | None, street_address: str | None, source_basis: Literal["api_geo", "jsonld_geo", "street_geocoded", "none"], evidence_url: str | None)`
  - `extract_posting_location_evidence(payload: str, *, content_type: str, evidence_url: str) -> ExtractedLocationEvidence` (extracts explicit numeric `(lat, lng)` from `application/ld+json` `JobPosting.jobLocation.geo` or Workable v2 JSON `location`, or extracts explicit `streetAddress` + `addressLocality`/`postalCode` in NSW)
  - `is_explicit_nsw_street_address(address: str | None) -> bool` (requires street number + street type/name + NSW locality/postcode; rejects `"Sydney NSW"`, `"Greater Sydney"`, `"Rockdale NSW"`, `"2216"`)
  - `geocode_explicit_street_address(street_address: str, *, client: httpx.Client, tfnsw_api_key: str | None = None, enabled: bool = False) -> tuple[float, float] | None` (uses TfNSW `stop_finder` `type_sf=coord/address` or OSM Nominatim AU at `<= 1 req/s`, `AU$0`, bounding coordinates strictly inside Greater Sydney `-34.25..-33.45`, `150.45..151.55`; returns `None` when `enabled=False` or address is non-explicit)

- [ ] **Step 1: Write failing RED tests for exact-address extraction/geocoding vs generic/suburb-only preservation**

In `sydney-hub/tests/test_geocode.py`, write tests for:
- `test_generic_location_and_suburb_only_remain_unknown_while_explicit_street_address_geocodes()`:
  - `is_explicit_nsw_street_address("Sydney NSW")` is `False`; `is_explicit_nsw_street_address("Rockdale NSW")` is `False`; `is_explicit_nsw_street_address("452 Princes Hwy, Rockdale NSW 2216")` is `True`.
  - `geocode_explicit_street_address("Sydney NSW", client=mock_client, enabled=True)` makes **0** HTTP requests and returns `None`.
  - `geocode_explicit_street_address("Rockdale NSW", client=mock_client, enabled=True)` makes **0** HTTP requests and returns `None` (never uses suburb centroid).
  - `geocode_explicit_street_address("452 Princes Hwy, Rockdale NSW 2216", client=mock_client, enabled=True)` returns verified `(lat, lng)` inside Sydney bounds, and returns `None` if response falls outside Greater Sydney or `enabled=False`.
  - `classify_published` on a listing with `"Sydney NSW"` or `"Rockdale NSW"` (no geocoded street address) returns `location_tier="unknown"` or `"suburb_only"`, never `"exact"`.
- `test_extract_posting_location_evidence_parses_jsonld_geo_and_street_address()`:
  - Parses `<script type="application/ld+json">` `JobPosting` with `geo.latitude`/`geo.longitude` -> `source_basis="jsonld_geo"`.
  - Parses `JobPosting` with `streetAddress` only -> `lat=None, lng=None, street_address="452 Princes Hwy, Rockdale NSW 2216"`.
  - Parses Workable v2 JSON without street address (`{"location": {"city": "Sydney", "region": "NSW"}}`) -> `lat=None, lng=None, street_address=None, source_basis="none"`.

- [ ] **Step 2: Run RED tests**

Run: `python -m pytest -ra tests/test_geocode.py`
Expected: FAIL (`ModuleNotFoundError: No module named 'hub.discovery.geocode'`).

- [ ] **Step 3: Implement `sydney-hub/hub/discovery/geocode.py`**

Implement `ExtractedLocationEvidence`, `extract_posting_location_evidence`, `is_explicit_nsw_street_address`, and `geocode_explicit_street_address` with `1 req/s` throttling, `AU$0` free-only endpoints (`api.transport.nsw.gov.au/v1/tp/stop_finder` when `tfnsw_api_key` is present, or `nominatim.openstreetmap.org/search` with compliant `User-Agent` and `follow_redirects=False`), and Greater Sydney bounds verification.

- [ ] **Step 4: Run GREEN tests**

Run: `python -m pytest -ra tests/test_geocode.py tests/test_published_filter.py tests/test_transit.py`
Expected: PASS.

- [ ] **Step 5: Commit Task 3**

```bash
git add sydney-hub/hub/discovery/geocode.py sydney-hub/hub/discovery/published_filter.py sydney-hub/tests/test_geocode.py
git commit -m "feat(jobs): add exact street-address extraction and non-fabricating geocoder"
```

---

### Task 4: Hardened Verification, Persistent 429 Cooldown, 7-Day Yield Journal & Runner

**Files:**
- Create: `sydney-hub/hub/discovery/health.py`
- Modify: `sydney-hub/run_published_jobs.py`
- Create: `sydney-hub/tests/test_published_health.py`
- Modify: `sydney-hub/tests/test_published_runner.py`

**Interfaces:**
- Produces in `hub/discovery/health.py`:
  - `SourceHealthSnapshot`: `source_id`, `last_run_at`, `last_outcome` (`"ok" | "cooldown" | "error" | "skipped_unapproved" | "skipped"`), `cooldown_until: float | None`, `retry_after_seconds: int | None`, `consecutive_failures: int`, `last_error: str | None`, `requests: int`, `wire_bytes: int`, `duration_ms: int`, `fetched: int`, `eligible: int`, `new_count: int`, `updated_count: int`, `duplicate_within_source: int`, `verified_count: int`, `unverified_count: int`, `recent_count: int`, `explicit_modality_count: int`, `exact_coords_count: int`, `yield_per_request: float`, `cost_aud: float`, `license: str`
  - `parse_retry_after(header_value: str | None, *, now: float, default_seconds: int = 3600) -> int`
  - `SourceHealthStore(state_dir: Path)`: `load()`, `in_cooldown(source_id, now)`, `record_result(snapshot)`, `record_workable_fetch(now)`, `last_workable_fetch()`
  - `append_yield_journal(reports_dir: Path, entry: dict) -> Path` (appends one redacted JSON line per run to `yield_journal.jsonl`)
- Modifies `run_published_jobs.py`:
  - **Validates `--db` FIRST** via `validate_isolated_jobs_db_path(..., isolated_root=...)` BEFORE any `ensure_published_schema`, `snapshot_published_run`, or SQLite access (refusing V4/main and any unapproved/outside DB path with zero hash/mtime alteration).
  - **Removes** hardcoded `SourcePolicy(source, True, ...)` at line 159; loads `load_source_policies(policies_path)` and resolves policies **per source and per individual ATS board** (`resolve_board_policy(policies, provider_id, slug)` for `ats:<provider>:<slug>` — never inheriting provider-wide approval). When `policy.publish_allowed is False`, `--live` performs **0** catalog writes (`outcome="skipped_unapproved"`).
  - **Removes** any cooldown bypass (no `--ignore-cooldown` flag or parameter). On `HTTP 429`, records `cooldown_until = now + parse_retry_after(...)` and stops requesting that source without retry; subsequent runs while `now < cooldown_until` make **0** requests (`outcome="cooldown"`) while independent sources proceed.
  - Hardens `verify_job_page(listing, client)`:
    - Rejects closed/expired signals (`applications closed`, `position filled`, `no longer accepting applications`, `vacancy closed`, `this job is no longer available`, `job has expired`) -> `"closed_or_expired"`.
    - Requires specific-posting proof: Workable `/api/v2/accounts/<account>/jobs/<shortcode>` (`state == "published"`, matching shortcode/title), OR HTML `application/ld+json` `JobPosting` / matching title inside `<title>`/`<h1>`/`<h2>` PLUS specific apply action/form (never bare body text + footer `"Apply"`).
    - Keeps Workable `<url>` verbatim in `listing.source_url` (`opportunities.source_url`) while returning the verified canonical/API URL consistently in `ListingCheck.evidence_url` (`opportunity_evidence.evidence_url`).
    - Extracts `ExtractedLocationEvidence` and, only if `geocode_street_addresses=True` and address is explicit NSW street address, resolves exact `(lat, lng)` before TfNSW commute check.
  - Pre-live safety: after `validate_isolated_jobs_db_path` passes, calls `ensure_published_schema(db_path, isolated_root=...)` and `snapshot_published_run(db_path, isolated_root=...)`.
  - Records full `requests`, `wire_bytes`, `duplicate_within_source`, `collisions`, `cost_aud` (`0.0` vs `unknown` for Adzuna), and writes redacted run JSON + appends `yield_journal.jsonl`.

- [ ] **Step 1: Write failing tests for hardened verification, zero-write unapproved `--live`, persistent 429 cooldown, and 7-day journal**

In `sydney-hub/tests/test_published_health.py`:
- Test `parse_retry_after` (`"3584"`, HTTP-date, malformed fallback, `[60, 86400]` clamping), `SourceHealthStore` cooldown persistence, and `append_yield_journal`.

In `sydney-hub/tests/test_published_runner.py`:
- `test_live_run_on_unapproved_source_or_unapproved_ats_board_performs_zero_catalog_writes(monkeypatch, tmp_path)`:
  - Run `run_published_jobs(..., live=True, policies_path=...)` where Workable is `publish_allowed: false` AND only `ats:greenhouse:approved-cafe` is approved while `ats:greenhouse:unapproved-bar` is also in `published_boards.yml` -> `workable` and `ats:greenhouse:unapproved-bar` log `"skipped_unapproved"` with 0 writes for those unapproved sources (`CatalogRepository(Database(db_path)).count()` excludes both).
- `test_live_run_rejects_v4_or_outside_db_before_schema_or_snapshot(tmp_path)`:
  - Passing an outside DB or V4/main DB path to `run_published_jobs(..., live=True)` raises `ValueError` BEFORE schema/snapshot, preserving SHA-256 and `st_mtime_ns`.
- `test_verify_job_page_rejects_closed_or_generic_pages_and_requires_specific_evidence()`:
  - Generic directory `"<html><h1>Careers</h1><div>Barista</div><footer><a>Apply</a></footer></html>"` -> `"unconfirmed"`.
  - Closed job `"<html><h1>Barista</h1><p>Applications closed</p><button>Apply</button></html>"` -> `"closed_or_expired"`.
  - Specific posting `"<html><h1>Barista</h1><button>Apply now</button></html>"` -> `"ok"`.
- `test_workable_cooldown_persists_across_runs_without_bypass(monkeypatch, tmp_path)`:
  - Run 1 gets `429` (`Retry-After: 1800`) -> Run 2 makes 0 requests to Workable (`last_outcome == "cooldown"`), while an approved official ATS board (`ats:greenhouse:approved-cafe`) succeeds independently.
- Update `test_runner_caches_origin_stop_and_persists_canonical_workable_url`: assert `opportunities.source_url` keeps Workable's original `<url>` verbatim (`https://apply.workable.com/j/W1`), `opportunity_evidence.evidence_url` equals `https://www.workable.com/api/v2/accounts/cafe/jobs/W1` (consistent across `ListingCheck` and DB), `.published-run-rollback.json` is created inside the marked isolated dir, and `yield_journal.jsonl` records `requests`, `duplicate_within_source`, `collisions`, and `cost_aud == 0.0`.

- [ ] **Step 2: Run RED tests**

Run: `python -m pytest -ra tests/test_published_health.py tests/test_published_runner.py`
Expected: FAIL.

- [ ] **Step 3: Implement `hub/discovery/health.py` and upgrade `run_published_jobs.py`**

Implement `SourceHealthStore`, `append_yield_journal`, `load_source_policies` gate, hardened `verify_job_page`, exact-location geocoding hook, cross-source collision detection, `safe_website_client()` default, and CLI options (`--config`, `--db`, `--policies`, `--boards`, `--state-dir`, `--report-out`, `--skip-workable`, `--audit-boards`, `--max-verifications`, `--live`, `--evaluate-adzuna` — with ZERO cooldown-bypass flag).

- [ ] **Step 4: Run GREEN tests**

Run: `python -m pytest -ra tests/test_published_health.py tests/test_published_runner.py`
Expected: PASS.

- [ ] **Step 5: Commit Task 4**

```bash
git add sydney-hub/hub/discovery/health.py sydney-hub/run_published_jobs.py sydney-hub/tests/test_published_health.py sydney-hub/tests/test_published_runner.py
git commit -m "feat(jobs): enforce persisted source policies, hardened verification, and 429 cooldown"
```

---

### Task 5: Honest 7-Day Pilot Scorecard, External Gates & Per-Listing 100% Audit Verification

**Files:**
- Create: `sydney-hub/hub/discovery/pilot_gate.py`
- Modify: `sydney-hub/run_published_jobs.py` (add `--pilot-status`, `--export-audit`, `--confirm-pilot-audit --audit-file <path>`)
- Create: `sydney-hub/tests/test_pilot_gate.py`

**Interfaces:**
- Produces in `hub/discovery/pilot_gate.py`:
  - `ListingAuditRecord` (Pydantic model: `opportunity_id: str`, `source_url: str`, `link_status: Literal["valid", "invalid", "broken"]`, `title_company_match: bool`, `evidence_checked: bool`, `collision_checked: bool`, `collision_verdict: Literal["distinct", "duplicate_valid", "wrong_merge"]`, `promoted_lead_or_generic: bool`, `checked_at: str`)
  - `ExternalGatesStatus(workable_policy_approved: bool, workable_cooldown_clear: bool, workable_cooldown_until: float | None, official_ats_boards_count: int, tfnsw_configured: bool, adzuna_eval_configured: bool, adzuna_catalog_publish_allowed: bool)`
  - `PilotScorecard(recent_verified_count: int, recent_commute_le_45_count: int, recent_explicit_modality_count: int, exact_coords_count: int, suburb_only_count: int, zero_unverified_promoted: bool, observation_days: int, seven_day_series: list[dict], first_run_passed: bool, seven_day_status: Literal["pending", "passed", "failed"], manual_audit_confirmed: bool, audit_summary: dict | None, external_gates: ExternalGatesStatus, blockers: tuple[str, ...], audit_sample: list[dict])`
  - `evaluate_pilot_gates(db_path: Path, state_dir: Path, reports_dir: Path, boards_path: Path, policies_path: Path, *, now: datetime | None = None, env: Mapping[str, str] | None = None) -> PilotScorecard`
  - `confirm_pilot_audit(state_dir: Path, db_path: Path, reports_dir: Path, boards_path: Path, policies_path: Path, *, audit_records_path: Path, now: datetime | None = None) -> dict`:
    - Refuses with `ValueError` unless `scorecard.first_run_passed is True`.
    - Validates `audit_records_path` JSON covers **100%** of current `verified_job` IDs in `db_path` (exact `opportunity_id` + `source_url` match), `title_company_match is True` and `evidence_checked is True` and `collision_checked is True` on every record, `wrong_merge` count `== 0`, `promoted_lead_or_generic` count `== 0`, and `valid_links / total >= 0.95`. Only then writes `pilot_gate.json` (`manual_audit_confirmed: True`, `audited_ids`, `valid_link_ratio`, `wrong_merges: 0`, `confirmed_at`).

- [ ] **Step 1: Write failing tests for honest blockers, 7-day journal span, and strict per-listing 100% audit validation**

In `sydney-hub/tests/test_pilot_gate.py`:
- `test_scorecard_reports_honest_blockers_without_claiming_targets_when_sources_blocked(tmp_path)`:
  - Leads-only DB, Workable in cooldown, `publish_allowed=False`, `boards: []`, no `TFNSW_API_KEY` -> `first_run_passed is False`, `seven_day_status == "pending"`, `blockers` names `20_recent`, `10_commute_le_45`, `5_explicit_modality`, `workable_cooldown`, `workable_policy_unapproved`, `ats_manifest_empty`, `tfnsw_unconfigured`.
  - Also test case where `TFNSW_FREE_QUOTA_CONFIRMED=1` + `TFNSW_API_KEY` ARE present, but all 20 verified listings have `lat=None, lng=None` (`suburb_only_count=20`, `exact_coords_count=0`): `recent_commute_le_45_count == 0` and `first_run_passed is False` (`10_commute_le_45_blocked_missing_exact_coords`).
- `test_confirm_pilot_audit_requires_per_listing_records_for_100_percent_of_verified_jobs(tmp_path)`:
  - Seed 20 recent `verified_job` rows (10 with `commute_observations <= 45`, 5 `Casual`).
  - Missing one `opportunity_id` in `audit_records.json` -> `confirm_pilot_audit` raises `ValueError("incomplete_audit_coverage")`.
  - One record with `collision_verdict="wrong_merge"` or `promoted_lead_or_generic=True` -> raises `ValueError`.
  - 2 out of 20 (`90% < 95%`) with `link_status="broken"` -> raises `ValueError("link_validity_below_95_percent")`.
  - Complete 20/20 valid records (or 19/20 = 95% valid, `0` wrong merges, `0` promoted leads) -> persists `pilot_gate.json` and `evaluate_pilot_gates(...).manual_audit_confirmed is True`.
- `test_seven_day_status_requires_7_distinct_sydney_days_in_yield_journal(tmp_path)`: 3 days -> `"pending"`; 7 distinct `Australia/Sydney` calendar dates -> `"passed"`.

- [ ] **Step 2: Run RED tests**

Run: `python -m pytest -ra tests/test_pilot_gate.py`
Expected: FAIL.

- [ ] **Step 3: Implement `hub/discovery/pilot_gate.py` and CLI options in `run_published_jobs.py`**

Implement `ListingAuditRecord`, `PilotScorecard`, `evaluate_pilot_gates`, and `confirm_pilot_audit`, plus `--pilot-status`, `--export-audit`, and `--confirm-pilot-audit --audit-file <path>` in `run_published_jobs.py`.

- [ ] **Step 4: Run GREEN tests**

Run: `python -m pytest -ra tests/test_pilot_gate.py tests/test_published_runner.py`
Expected: PASS.

- [ ] **Step 5: Commit Task 5**

```bash
git add sydney-hub/hub/discovery/pilot_gate.py sydney-hub/run_published_jobs.py sydney-hub/tests/test_pilot_gate.py
git commit -m "feat(jobs): add 7-day pilot scorecard and per-listing 100% audit gate"
```

---

### Task 6: Opt-In Published Jobs Scheduler Guarded Fail-Closed by Pilot Gate

**Files:**
- Create: `sydney-hub/hub/scheduler/jobs_scheduler.py`
- Create: `sydney-hub/tests/test_jobs_scheduler.py`

**Interfaces:**
- Consumes: `JobsConfig`, `evaluate_pilot_gates`, `SourceHealthStore`, `run_published_jobs`, `parse_slots`
- Produces in `hub/scheduler/jobs_scheduler.py`:
  - `SchedulerGateResult(allowed: bool, reasons: tuple[str, ...])`
  - `evaluate_scheduler_gate(config: JobsConfig, *, now: datetime | None = None, env: Mapping[str, str] | None = None) -> SchedulerGateResult`
  - `PublishedSchedulerService(config: JobsConfig, run_fn: Callable[[], dict] | None = None, clock: Callable[[], float] = time.time)` (`is_running`, `is_active`, `gate_result`, `next_run_at()`, `start() -> bool`, `trigger_manual() -> dict`, `shutdown() -> None`)

- [ ] **Step 1: Write failing tests for fail-closed scheduler gate (including 7-day requirement) and Workable 20h floor**

In `sydney-hub/tests/test_jobs_scheduler.py`:
- `test_scheduler_gate_blocks_day_1_even_when_20_10_5_and_audit_pass(tmp_path)`:
  - Seed isolated DB with `20` recent `verified_job` rows (`10` commutes `<= 45`, `5` `Casual`), valid `pilot_gate.json` (`manual_audit_confirmed=True`), `scheduler_enabled=True`, `schedule=["07:30"]`, **but only 1 day in `yield_journal.jsonl`** (`seven_day_status == "pending"`).
  - `evaluate_scheduler_gate(...)` MUST return `allowed=False` with `"seven_day_observation_pending"` in `reasons`; `PublishedSchedulerService.start()` returns `False` and `next_run_at() is None`.
- `test_scheduler_gate_fails_closed_until_pilot_audit_confirmed(tmp_path)`: fails closed when `scheduler_enabled=False`, when `schedule` has 0 or >2 slots, when DB is outside marked isolated dir or missing/`user_version != 5`, when `first_run_passed=False` (including when `10_commute_le_45` is blocked), or when per-listing `pilot_gate.json` confirmation is missing/stale.
- `test_scheduler_starts_only_after_7day_gate_and_enforces_workable_daily_floor(tmp_path)`: passes gate ONLY when `scheduler_enabled=True`, `schedule=["07:30"]`, valid isolated v5 DB, `first_run_passed=True`, **`seven_day_status == "passed"` (7 distinct `Australia/Sydney` calendar days)**, and `manual_audit_confirmed=True`; serializes `trigger_manual()` (`RunAlreadyActive` on overlap) and sets `skip_workable=True` if fetched within `workable_min_interval_hours`.

- [ ] **Step 2: Run RED tests**

Run: `python -m pytest -ra tests/test_jobs_scheduler.py`
Expected: FAIL.

- [ ] **Step 3: Implement `hub/scheduler/jobs_scheduler.py`**

Keep Facebook `SchedulerService` (`hub/scheduler/service.py`) untouched. Enforce isolated DB path guard, `1 <= len(schedule) <= 2` (`Australia/Sydney` `CronTrigger`), DB v5 + integrity check, `first_run_passed`, `seven_day_status == "passed"`, and per-listing `manual_audit_confirmed`.

- [ ] **Step 4: Run GREEN tests**

Run: `python -m pytest -ra tests/test_jobs_scheduler.py tests/test_scheduler.py`
Expected: PASS.

- [ ] **Step 5: Commit Task 6**

```bash
git add sydney-hub/hub/scheduler/jobs_scheduler.py sydney-hub/tests/test_jobs_scheduler.py
git commit -m "feat(jobs): add fail-closed opt-in published scheduler"
```

---

### Task 7: Jobs-Only Local Service Without Facebook & Health API

**Files:**
- Modify: `sydney-hub/hub/api/app.py`
- Create: `sydney-hub/jobs_app.py`
- Modify: `sydney-hub/web/jobs-v5-engine.js`
- Create: `sydney-hub/tests/test_jobs_service.py`
- Modify: `sydney-hub/tests/test_api.py`, `sydney-hub/tests/test_jobs_v5_engine.mjs`

**Interfaces:**
- Modifies `AppDeps` in `hub/api/app.py`: optional `jobs_only: bool = False`, `jobs_health_fn: Callable[[], dict] | None = None`, `jobs_runner_fn: Callable[[], dict] | None = None`, `jobs_running_fn: Callable[[], bool] | None = None`.
- Adds endpoints in `create_app`:
  - `GET /api/jobs/health` -> redacted source health, cooldowns, yield journal summary, `pilot_scorecard`, `scheduler_active`, `scheduler_gate`
  - `POST /api/jobs/runs` -> spawns bounded manual Jobs run (`202` or `409` if running/disabled)
  - When `deps.jobs_only is True`: `POST /api/runs`, `POST /api/groups`, `PATCH /api/groups/{id}`, `DELETE /api/groups/{id}`, `POST /api/posts/seen`, `PATCH /api/posts/{id}` return `409`.
- Produces in `sydney-hub/jobs_app.py`:
  - `build_jobs_service(config: JobsConfig, *, start_scheduler: bool = False) -> tuple[FastAPI, PublishedSchedulerService]`
  - CLI: `python sydney-hub/jobs_app.py [--config config/jobs.yml] [--bootstrap-from <source_db>] [--host 127.0.0.1] [--port 8796] [--once]`

- [ ] **Step 1: Write failing tests for Jobs-only service, Facebook isolation, and JS role labels**

In `sydney-hub/tests/test_jobs_service.py`:
- `test_jobs_only_service_disables_facebook_and_blocks_fb_mutations(monkeypatch, tmp_path)`: monkeypatches `resolve_maestri` to raise `AssertionError`; verifies `build_jobs_service` starts cleanly on validated isolated DB, `GET /api/opportunities` & `GET /api/jobs/health` return `200`, `Host: evil.example:8796` returns `403`, and all Facebook routes return `409`.
- `test_jobs_only_manual_run_endpoint_and_cli_once(tmp_path)`: verifies `POST /api/jobs/runs` (`202`/`409` concurrency) and `python jobs_app.py --config <tmp_cfg> --once` outputs JSON (`mode == "jobs_only"`, `scheduler_active is False`).
- `test_e2e_runner_to_isolated_db_to_api_and_ui_with_fixture_verified_job(tmp_path)`: runs `run_published_jobs(..., live=True)` against mocked Workable/ATS HTTP fixture (approved policy + exact street address) -> writes `verified_job` into isolated SQLite DB -> boots `build_jobs_service` -> verifies `GET /api/opportunities` returns the `verified_job` with verbatim `source_url`, verified `evidence_url`, explicit `suburb`, and `GET /` serves HTML rendering the `verified_job` cleanly with zero Facebook calls.

In `sydney-hub/tests/test_jobs_v5_engine.mjs`:
- Add test checking `buildJobDetailModel` maps backend `role_bucket` tokens (`barista`, `bartender`, `kitchen`, `front_of_house`, `hospitality`, `cleaner`, `housekeeping`, `retail`, `warehouse`) cleanly.

- [ ] **Step 2: Run RED tests**

Run:
`python -m pytest -ra tests/test_jobs_service.py tests/test_api.py`
`node --test tests/test_jobs_v5_engine.mjs`
Expected: FAIL.

- [ ] **Step 3: Implement `hub/api/app.py`, `jobs_app.py`, and `web/jobs-v5-engine.js`**

Wire `jobs_app.py` purely to `JobsConfig`, `Database`, `CatalogRepository`, `PublishedSchedulerService`, and `build_dashboard`. Never import or invoke `hub.adapters.maestri_portal`, `hub.adapters.maestri_agent`, `hub.adapters.process`, `catalog.sync_posts()`, or `import_legacy_seed()`. Validate loopback host and unbound port before `uvicorn.run`.

- [ ] **Step 4: Run GREEN tests**

Run:
`python -m pytest -ra tests/test_jobs_service.py tests/test_api.py`
`node --test tests/test_jobs_v5_engine.mjs`
Expected: PASS.

- [ ] **Step 5: Commit Task 7**

```bash
git add sydney-hub/hub/api/app.py sydney-hub/jobs_app.py sydney-hub/web/jobs-v5-engine.js sydney-hub/tests/test_jobs_service.py sydney-hub/tests/test_api.py sydney-hub/tests/test_jobs_v5_engine.mjs
git commit -m "feat(jobs): add Facebook-free Jobs-only local service and health endpoints"
```

---

### Task 8: Full Verification Suites, Hygiene Gate, Rollback Drill & Original DB Preservation

**Files:**
- Verify all suites in `sydney-hub/tests/` and `sydney-hub/check_publish_hygiene.py`.

- [ ] **Step 1: Run full Python & JS unit test suites**

Run in `D:\AntigravityProjetos\sydneystays-v4-v5-preview\sydney-hub`:
- `python -m pytest -ra` -> Expected: all pass, 0 failures.
- `node --test tests/test_jobs_v5_engine.mjs` -> Expected: all pass.

- [ ] **Step 2: Run Playwright UI fixture & Jobs-only isolated E2E (leads-only + fixture verified_job)**

1. Run `python tests/serve_ui_fixture.py <free_port>` + `node tests/test_jobs_ui.mjs http://127.0.0.1:<free_port>`, then stop fixture server. Expected: exit 0.
2. Bootstrap a temporary isolated DB inside a marked isolated dir from `C:\Users\guicx\.codex\worktrees\sydneystays-employer-pilot\career-ops\sydney-hub\data\hub.db`, run `python jobs_app.py --config <temp_jobs.yml> --once` -> Expected: `155` `employer_lead`, `0` `verified_job`, `scheduler_active: false`.
3. Start `jobs_app.py` against that temp DB on a free loopback port, run `node tests/test_preview_e2e.mjs http://127.0.0.1:<port> --mode=leads-only`, verify `GET /api/jobs/health` -> `200` and `POST /api/runs` -> `409`, then stop server.
4. Run full Playwright E2E `runner (mock fixture) -> isolated DB -> jobs_app.py API -> Playwright Verified Jobs tab` proving a `verified_job` persists, loads via `/api/opportunities` and UI Verified tab (verbatim `source_url`, `<= 45 min` badge when exact address/commute present), with zero Facebook activity.

- [ ] **Step 3: Run pre-run rollback drill, original DB integrity check, and hygiene baseline check**

1. On the temporary isolated DB copy: `snapshot_published_run` -> test mutation -> `python rollback_catalog.py --database <temp_db> --pre-run --force` -> verify `PRAGMA integrity_check == "ok"` and exact row counts restored.
2. Verify SHA-256 and `LastWriteTime`/`st_mtime_ns` of `C:\Users\guicx\.codex\worktrees\sydneystays-employer-pilot\career-ops\sydney-hub\data\hub.db` and `D:\AntigravityProjetos\career-ops\sydney-hub\data\hub.db` are unchanged with zero `-wal`/`-shm` sidecars.
3. Run `python check_publish_hygiene.py` and `git status -s`: **NOTE (Expected Failure on Inherited HEAD):** `check_publish_hygiene.py` against inherited `HEAD` (`codex/sydneystays-v4-v5-preview` descending from `d31174e5`) **is EXPECTED to exit non-zero (`is_safe_to_publish = False`)** due to inherited historical Gmail credentials (`d31174e5`) and tracked `USER.md`/`applications/*`. Verify that **zero new** sensitive files, `.db` files, or personal config YAMLs (`config/jobs.yml`, `config/source_policies.yml`, `config/published_boards.yml`, `data/jobs_isolated/`) are added to `forbidden_tracked` or `git status -s`.

---

## External Gates & Honest Pilot Operation Boundary

Tasks 1–8 deliver the verified local Jobs infrastructure without promising market counts or auto-activating live scraping. Local live pilot execution and 7-day acceptance remain gated on external conditions:

- **Gate E1 (Persisted Source Policy & Workable XML Cooldown):** Explicit operator entry in ignored `config/source_policies.yml` after reviewing [Workable XML Help](https://help.workable.com/hc/en-us/articles/4420464031767-Utilizing-the-XML-Job-Feed) (*Jobs by Workable*, hourly feed, `<url>` unaltered); respect any active `HTTP 429` `Retry-After` cooldown strictly (`0` bypasses, max 1 fetch/day). When blocked, record `outcome: "cooldown"` honestly without impacting other sources.
- **Gate E2 (Official ATS Seeds):** Employer-verified career-page links for `greenhouse`, `lever`, or `ashby` in ignored `config/published_boards.yml`, passing `--audit-boards` (`0` `CC BY-NC` datasets).
- **Gate E3 (TfNSW Bronze Key & Exact Street-Address Coverage):** Operator supplies `TFNSW_FREE_QUOTA_CONFIRMED=1` + `TFNSW_API_KEY` (`AU$0`, `60,000/day`, `5/s`). Postings without explicit API/JSON-LD coordinates or explicit NSW street address stay `suburb_only` / `commute_unknown`; if `< 10` exact addresses exist, `10_commute_le_45` remains `not_met`/blocked and scheduler stays inactive.
- **Gate E4 (Adzuna AU License):** Evaluation-only (`--evaluate-adzuna` + `ADZUNA_FREE_QUOTA_CONFIRMED=1`). Catalog insertion remains blocked in both policy registry and `CatalogRepository` without confirmed commercial/publication license.
- **Gate E5 (7-Day Observation & Per-Listing 100% Audit):** Real 7-day `yield_journal.jsonl` span across 7 distinct `Australia/Sydney` calendar days, `20/10/5` thresholds, and 100% per-listing `--audit-file` validation (`>= 95%` valid links, `0` wrong merges) are required before `PublishedSchedulerService` can activate.

---

## Execution Handoff

Plan revised after Prisma preflight (2 Critical + 6 Important rulings recorded in `docs/superpowers/plans/2026-11-14-sydneystays-jobs-operational-progress.md`) for local **Native** execution in `D:\AntigravityProjetos\sydneystays-v4-v5-preview`. Task-by-task TDD implementation proceeds sequentially (commits per task; zero live runs until source/DB gates are explicitly satisfied).
