# SydneyStays Published Jobs Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement and start measuring a direct, evidence-backed backpacker job feed in Sydney without requiring an OSM employer lead; final seven-day acceptance is a later observation, not a claim at code completion.

**Architecture:** Add a source-neutral published-listing contract and source identity map to the existing SQLite catalog. Start with Workable's official XML feed and independently sourced ATS boards; evaluate Adzuna AU separately until publication terms are confirmed. Apply deterministic job/region filters, optional TfNSW transit observations, then expose the results through existing FastAPI and Jobs UI.

**Tech Stack:** Python 3.12, FastAPI, SQLite, httpx, stdlib XML, existing Node ATS bridge, APScheduler unchanged, Playwright UI tests.

**Spec:** `docs/superpowers/specs/2026-11-14-sydneystays-published-jobs-design.md`

## Global Constraints

- No push, merge, deploy, recurring collection, Career Ops upgrade, new framework, or redesign of Jobs.
- Spending cap **AU$0**. Adzuna account exists, but publication/commercial license is unconfirmed: its adapter remains evaluation-only and must not write ads into the public catalog.
- Transit reference windows: Tuesday 09:00 and Friday 18:00, `Australia/Sydney`, from a clearly labelled Rockdale origin. Unknown location or unavailable route remains `commute_unknown`.
- `verified_job` requires a specific source listing and checked evidence. `published_at` is never `last_verified_at`; missing salary, contract, experience, certifications, visa and travel time remain unknown.
- `recent` for pilot: source publication in preceding 30 days and successful check in preceding 48 hours. An undated live listing may be displayed, but cannot count toward the 20-recent target.
- Do not use the CC BY-NC company datasets consumed by `scan-ats-full.mjs` without explicit permission. Each ATS seed must record an independent official referrer or first-party index.
- Preserve existing opportunity IDs, `localStorage` keys and the 155 OSM leads. Database writes require a verified backup, reversible migration and idempotency.
- No credentials in repo, reports, logs, URLs or test fixtures. Fixed source hosts, bounded responses and request budgets; timeout is never evidence of closure.

## Review Focus

1. **Oversized or malformed Workable XML:** parser must stop at byte/record caps without partial catalog writes (Task 3 test).
2. **A purported official ATS seed that redirects to private IP or unrelated host:** reject before fetch; no SSRF or silent host substitution (Task 4 test).
3. **Adzuna 401/429 or an account with no publication license:** report failure or evaluation only, never write ads to Jobs (Task 5 test).
4. **Transit result with only car journeys, DST shift or missing exact destination:** return unknown rather than a fabricated <=45-minute badge (Task 6 test).
5. **Cross-source duplicate and stable favorite ID:** retain first opportunity ID, all original source URLs and evidence; do not merge two branches of the same chain by company/title alone (Tasks 1 and 7 tests).

## File Structure

| File | Responsibility |
| --- | --- |
| `sydney-hub/hub/discovery/published.py` | Small immutable listing/verification contract and recency rule |
| `sydney-hub/hub/discovery/published_filter.py` | Deterministic role and regional/location classification |
| `sydney-hub/hub/discovery/workable.py` | Bounded streaming parser of first-party Workable XML |
| `sydney-hub/hub/discovery/published_ats.py` | Board seed/referrer validation and existing `AtsBridge` adaptation |
| `sydney-hub/hub/discovery/adzuna.py` | AU API evaluation, with no catalog writer |
| `sydney-hub/hub/discovery/transit.py` | TfNSW route request/response and explicit unknown outcomes |
| `sydney-hub/hub/storage/db.py`, `catalog.py` | v5 backup/rollback, source identity and commute observations |
| `sydney-hub/run_published_jobs.py` | Manual, bounded source-independent pilot runner and JSON telemetry |
| `sydney-hub/hub/api/app.py`, `web/template.html` | Existing Jobs query/display of source freshness and commute state |
| `sydney-hub/tests/test_published_*.py`, `test_api.py`, `test_jobs_ui.mjs` | Behavioral, integration and desktop/mobile checks |

### Task 1: Published listing identity, evidence and reversible schema v5

**Files:** Create `sydney-hub/hub/discovery/published.py`, `sydney-hub/tests/test_published_catalog.py`. Modify `sydney-hub/hub/storage/db.py`, `catalog.py`, `sydney-hub/tests/test_storage.py`.

**Interfaces:** Produce `PublishedListing(source_id, external_id, source_url, title, company, location, published_at, employment_type, description, lat, lng)` with nullable fields except source/title/company/URL; `ListingCheck(outcome, checked_at, evidence_url, excerpt)`; `SourcePolicy(source_id, publish_allowed, attribution, max_requests)`; `CatalogRepository.upsert_published_listing(listing, check, policy) -> str`; `is_recent(listing, check, now) -> bool`. Later tasks consume these exact types/methods.

- [ ] **Step 1: Write failing tests** for: verified ATS listing creates `job_listing` evidence and an ID; same `source_id+external_id` with changed URL keeps that ID and both source URLs; same company/title at two locations stays two jobs; policy with `publish_allowed=False` refuses a catalog write; failed/timeout check neither closes nor promotes; `published_at=None` is not recent; a 31-day posting or 49-hour check is not recent; v4→v5 rollback restores v4 bytes/counts on a temp database.
- [ ] **Step 2: Run RED.** Run: `rtk proxy python -m pytest -q sydney-hub/tests/test_published_catalog.py sydney-hub/tests/test_storage.py`. Expected: new tests fail for missing model/method/schema; existing storage tests pass.
- [ ] **Step 3: Implement minimum.** Add a `published_source_keys(source_id, external_id, opportunity_id)` mapping, keep original URLs in evidence, and add a v5 `commute_observations` table keyed by opportunity/origin/departure window. Extend the existing v4 backup/restore path without changing old IDs or reinterpreting old `opportunity_commutes` rows. `SourcePolicy.publish_allowed` defaults to false; the repository enforces it, then verifies check outcome, nonempty title/URL, and evidence URL before setting `verified_job`; store only fields explicitly supplied.
- [ ] **Step 4: Run GREEN.** Same command. Expected: all listed tests pass, `PRAGMA integrity_check='ok'` in the rollback test.
- [ ] **Step 5: Commit.** `rtk git add sydney-hub/hub/discovery/published.py sydney-hub/hub/storage/db.py sydney-hub/hub/storage/catalog.py sydney-hub/tests/test_published_catalog.py sydney-hub/tests/test_storage.py`; `rtk git commit -m "feat: persist published listing evidence with stable ids"`.

### Task 2: Backpacker roles, regional evidence and unknown fields

**Files:** Create `sydney-hub/hub/discovery/published_filter.py`, `sydney-hub/tests/test_published_filter.py`. Modify `sydney-hub/hub/discovery/ats_filter.py` only to reuse/extend its existing role patterns if needed.

**Interfaces:** Consume `PublishedListing`; produce `classify_published(listing: PublishedListing) -> PublishedMatch` where `PublishedMatch.role_bucket` and `location_tier` are nullable/explicit (`exact`, `suburb_only`, `unknown`) and no field is guessed. Task 7 uses `role_bucket` and `location_tier`.

- [ ] **Step 1: Write failing tests** for barista, bar staff, kitchen hand, waitstaff, room attendant, retail assistant and picker/packer synonyms; corporate title exclusions; explicit Sydney/NSW vs outside-NSW location; generic Sydney remains approximate; missing contract, RSA/RCG and WHV remain unknown, not entry-level/casual by default.
- [ ] **Step 2: Run RED.** Run: `rtk proxy python -m pytest -q sydney-hub/tests/test_published_filter.py`. Expected: missing classifier / role coverage failures.
- [ ] **Step 3: Implement minimum** with deterministic patterns; preserve exact source title/location, classify only supported positive evidence. Do not add an LLM or infer visa eligibility.
- [ ] **Step 4: Run GREEN.** Same command plus `sydney-hub/tests/test_ats_filter.py`. Expected: all pass; prior four-suburb filter still behaves as before.
- [ ] **Step 5: Commit.** `rtk git add sydney-hub/hub/discovery/published_filter.py sydney-hub/hub/discovery/ats_filter.py sydney-hub/tests/test_published_filter.py`; `rtk git commit -m "feat: classify backpacker roles and regional evidence"`.

### Task 3: Direct Workable XML source, independent of OSM

**Files:** Create `sydney-hub/hub/discovery/workable.py`, `sydney-hub/tests/test_workable_feed.py`.

**Interfaces:** Produce `iter_workable_listings(chunks: Iterable[bytes], *, max_bytes: int = 268435456, max_jobs: int = 100000) -> Iterator[PublishedListing]`; `fetch_workable(client: httpx.Client) -> list[PublishedListing]`. Task 7 consumes `fetch_workable`. Fixed source URL: `https://www.workable.com/boards/workable.xml`; preserve each `<url>` exactly.

- [ ] **Step 1: Write failing tests** using small real-shape XML fixtures: AU barista with RFC date/reference number, non-AU excluded from regional yield, missing date stays unknown, malformed/oversized XML and >max_jobs fail closed before persistence, URL is unchanged, parser does not depend on OSM IDs. Include chunk boundaries inside XML tags.
- [ ] **Step 2: Run RED.** Run: `rtk proxy python -m pytest -q sydney-hub/tests/test_workable_feed.py`. Expected: missing parser/fetch functions.
- [ ] **Step 3: Implement minimum** using stdlib streaming XML, bounded byte/record counts, fixed host, no redirects to unrelated hosts, modest pacing (one manual fetch per day, never hourly polling). The runner stages complete results before DB write so malformed streams cannot partially commit.
- [ ] **Step 4: Run GREEN.** Same command. Expected: all pass with bounded memory and exact URLs.
- [ ] **Step 5: Commit.** `rtk git add sydney-hub/hub/discovery/workable.py sydney-hub/tests/test_workable_feed.py`; `rtk git commit -m "feat: discover published jobs from Workable feed"`.

### Task 4: Independent official ATS board seeds via existing bridge

**Files:** Create `sydney-hub/hub/discovery/published_ats.py`, `sydney-hub/config/published_boards.example.yml`, `sydney-hub/tests/test_published_ats.py`. Modify `sydney-hub/hub/discovery/ats_bridge.py` and `.mjs` only if necessary to preserve native posting IDs.

**Interfaces:** Produce `load_official_boards(path: Path) -> list[OfficialBoard]` with board URL, official referrer URL, checked_at and provider ID; `validate_official_board(board: OfficialBoard, client: httpx.Client) -> bool` requiring the fetched referrer page to link to the board host/URL without unsafe redirects; `fetch_board_listings(board: OfficialBoard, bridge: AtsBridge) -> list[PublishedListing]` only accepts validated boards. Task 7 consumes the list; a board seed is never derived from OSM or Adzuna.

- [ ] **Step 1: Write failing tests** for a first-party employer career page linking to a Greenhouse/Lever/Ashby board; malformed or missing referrer rejected; private/unrelated/redirected host blocked; bridge fixture yields a real specific posting with source URL and explicit location; 429/timeout returns source error without blocking Workable. Include an external-ID/URL-alias case where the provider exposes a native ID.
- [ ] **Step 2: Run RED.** Run: `rtk proxy python -m pytest -q sydney-hub/tests/test_published_ats.py sydney-hub/tests/test_ats_bridge.py`. Expected: missing board loader/adapter tests fail, bridge regressions pass.
- [ ] **Step 3: Implement minimum.** Use existing `AtsBridge.detect/fetch` and its SSRF protection. The seed manifest requires provenance; the pilot assembles a small verified manifest from official employer career links or first-party indices, never from the CC BY-NC dataset. Retain `published_at=None` when provider omits it.
- [ ] **Step 4: Run GREEN.** Same command. Expected: all pass, including the real Node bridge integration fixture.
- [ ] **Step 5: Commit.** `rtk git add sydney-hub/hub/discovery/published_ats.py sydney-hub/config/published_boards.example.yml sydney-hub/tests/test_published_ats.py sydney-hub/hub/discovery/ats_bridge.py sydney-hub/hub/discovery/ats_bridge.mjs`; `rtk git commit -m "feat: scan independently sourced official ATS boards"` (stage only changed paths).

### Task 5: Adzuna AU evaluation behind publication gate

**Files:** Create `sydney-hub/hub/discovery/adzuna.py`, `sydney-hub/tests/test_adzuna_eval.py`. Modify `sydney-hub/hub/settings.py` only for disabled-by-default evaluation settings; never add credential values to example YAML.

**Interfaces:** Produce `evaluate_adzuna_au(client: httpx.Client, *, app_id: str, app_key: str, queries: list[str], max_pages: int) -> AdzunaEvaluation` containing aggregate counts and policy-safe, redacted diagnostics. Task 7 calls only in explicit `--evaluate-adzuna` mode and never passes its results to `CatalogRepository`.

- [ ] **Step 1: Write failing tests** for `/v1/api/jobs/au/search/{page}` request shape, query/pagination bound, `created`/location/redirect URL parsing, `salary_is_predicted` not treated as advertised pay, missing credentials, 401/429/backoff and no secret in exception/report. Assert no catalog write can occur in evaluation mode.
- [ ] **Step 2: Run RED.** Run: `rtk proxy python -m pytest -q sydney-hub/tests/test_adzuna_eval.py`. Expected: missing evaluator tests fail.
- [ ] **Step 3: Implement minimum** with fixed Adzuna host, environment-only credentials, bounded pages/results, source attribution in the private evaluation report, and AU$0 quota guard. No public Adzuna rows until terms of the specific account permit it.
- [ ] **Step 4: Run GREEN.** Same command. Expected: all pass; diagnostics contain neither `app_id` nor `app_key`.
- [ ] **Step 5: Commit.** `rtk git add sydney-hub/hub/discovery/adzuna.py sydney-hub/hub/settings.py sydney-hub/tests/test_adzuna_eval.py`; `rtk git commit -m "feat: evaluate Adzuna AU without publishing ads"`.

### Task 6: Evidence-backed TfNSW commute observations

**Files:** Create `sydney-hub/hub/discovery/transit.py`, `sydney-hub/tests/test_transit.py`. Modify `sydney-hub/hub/storage/catalog.py`, `sydney-hub/tests/test_catalog.py`.

**Interfaces:** Produce `measure_commute(origin_stop: str, destination: tuple[float, float] | None, departure_at: datetime, *, client: httpx.Client, api_key: str | None) -> CommuteResult` (`minutes: int | None`, source, checked_at, outcome). Repository writes to v5 `commute_observations` keyed by opportunity/origin/departure window; no write for unknown.

- [ ] **Step 1: Write failing tests** for Tuesday 09:00/Friday 18:00 `Australia/Sydney` including DST; TfNSW `/v1/tp/trip` params (`depArrMacro=dep`, `itdDate`, `itdTime`, coordinate destination), `Authorization: apikey ...` header, journey time from requested departure to final arrival including wait, rejection of car-only journeys, and missing destination/key/route returning unknown. Existing undated `opportunity_commutes` values must not satisfy a timed <=45 filter.
- [ ] **Step 2: Run RED.** Run: `rtk proxy python -m pytest -q sydney-hub/tests/test_transit.py sydney-hub/tests/test_catalog.py`. Expected: new route/observation tests fail; prior catalog tests pass.
- [ ] **Step 3: Implement minimum** against TfNSW's documented free Bronze API, fixed host and 5/s upper rate limit, with no key in URL/log. Label the exact Rockdale station origin resolved through official Stop Finder. Only exact job destination coordinates can be measured. If access is absent, preserve unknown; do not install a GTFS routing framework as fallback in this plan.
- [ ] **Step 4: Run GREEN.** Same command. Expected: all pass, no fabricated commute and old values preserved.
- [ ] **Step 5: Commit.** `rtk git add sydney-hub/hub/discovery/transit.py sydney-hub/hub/storage/catalog.py sydney-hub/tests/test_transit.py sydney-hub/tests/test_catalog.py`; `rtk git commit -m "feat: record time-specific TfNSW commute evidence"`.

### Task 7: Manual source-independent runner, API and Jobs integration

**Files:** Create `sydney-hub/run_published_jobs.py`, `sydney-hub/tests/test_published_runner.py`. Modify `sydney-hub/hub/api/app.py`, `sydney-hub/web/template.html`, `sydney-hub/tests/test_api.py`, `sydney-hub/tests/test_jobs_ui.mjs`, `sydney-hub/tests/serve_ui_fixture.py`.

**Interfaces:** Consume Tasks 1–6. CLI `--dry-run` by default, explicit `--live`; independent per-source outcomes. API `/api/opportunities` retains existing contract/IDs, adds `role_bucket`, `recent`, `commute_window`, `commute_unknown` filters without breaking old filters.

- [ ] **Step 1: Write failing tests** for Workable succeeding when ATS fails and vice versa; source budgets/retries; two repeat runs preserving IDs; no Adzuna publication; `verified_job` evidence requirement; API pagination/filter count consistency; unknown commute displayed but excluded from strict <=45; API failure shown rather than static fallback; favorites/status persist on desktop and mobile.
- [ ] **Step 2: Run RED.** Run: `rtk proxy python -m pytest -q sydney-hub/tests/test_published_runner.py sydney-hub/tests/test_api.py`. Expected: new runner/filter tests fail; old API tests pass. Run UI fixture test and observe new assertions fail before UI changes.
- [ ] **Step 3: Implement minimum** with existing FastAPI/SQLite/Jobs conventions. Stage per-source results, then persist each complete batch. Keep APScheduler untouched. Add source and freshness wording without rearranging the Impeccable layout or localStorage keys.
- [ ] **Step 4: Run GREEN.** Run: `rtk proxy python -m pytest -q sydney-hub/tests`; launch `rtk proxy python sydney-hub/tests/serve_ui_fixture.py` in a separate local session, then run `rtk proxy node sydney-hub/tests/test_jobs_ui.mjs` and stop the fixture session. Expected: full Python and Playwright suites pass on desktop and mobile.
- [ ] **Step 5: Commit.** `rtk git add sydney-hub/run_published_jobs.py sydney-hub/hub/api/app.py sydney-hub/web/template.html sydney-hub/tests/test_published_runner.py sydney-hub/tests/test_api.py sydney-hub/tests/test_jobs_ui.mjs sydney-hub/tests/serve_ui_fixture.py`; `rtk git commit -m "feat: serve published jobs in existing Jobs explorer"`.

### Task 8: Safe first pilot run and independent final review

**Files:** Create `docs/superpowers/reviews/2026-11-14-published-jobs-pilot.md`; do not commit raw API results, SQLite, browser sessions or credentials.

**Interfaces:** Execute Task 7's manual CLI on a verified catalog snapshot. Produce source-by-source counts, real URLs and a **first-run** acceptance snapshot. The seven-day criterion remains pending until the observation window ends; no recurring task.

- [ ] **Step 1: Preflight.** Run full Python/Playwright suites, `git diff --check`, secret/file scan, SQLite backup/integrity and temp-copy rollback. Expected: clean tests, backup restorable, no sensitive staged file.
- [ ] **Step 2: Manual dry-run** with bounded Workable and independently verified ATS seeds; Adzuna evaluation only if account credentials exist locally and free quota is confirmed. Expected: source health and counts; no catalog write.
- [ ] **Step 3: Explicit `--live` on local DB** after snapshot, with no recurring scheduler. For each source, record fetched/eligible/verified/duplicate counts, real listing URLs, dates, modality, precision of location, and AU$0 actual spend. TfNSW queries only if free key and exact destination are available.
- [ ] **Step 4: Audit 100% of `verified_job` links**, then repeat run to prove idempotency; calculate first-run 20-recent, 10-measured-commute, 5-explicit-modality and 95%-valid-link gates. Any failed gate is reported as failed, never waived. Mark the seven-day verdict `pending` until seven days of controlled observation have elapsed; record unknowns and source permission gaps. Do not create an unattended recurring job to finish this task.
- [ ] **Step 5: Commit only the redacted review report.** `rtk git add docs/superpowers/reviews/2026-11-14-published-jobs-pilot.md`; `rtk git commit -m "docs: record published jobs pilot evidence and limits"`. Request one independent whole-branch review; fix Critical/Important findings with RED→GREEN tests, rerun suites, then finish branch locally. Do not push, merge or deploy.
