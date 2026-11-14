# SydneyStays Employer Discovery Pilot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Discover and verify employer-site opportunities in Rockdale, Kogarah, Hurstville, and Mascot without turning a business lead or a failed website check into a confirmed vacancy.

**Architecture:** Keep the source adapter, website inspector, and catalog writer independent. The adapter supplies transient business references, the inspector records only website-supported facts, and the existing SQLite repository owns stable IDs, provenance, and verification state. Start with fixture and authorized-site tests; enable a live business source only after the rights gate below.

**Tech Stack:** Existing Python, httpx, FastAPI, SQLite, APScheduler, pytest, Playwright, and Impeccable Jobs UI; standard-library HTML parsing. No new framework.

**Spec:** `docs/superpowers/specs/2026-11-13-sydneystays-final-engine.md`

## Global Constraints

- Preserve the Delivery 1 baseline (`2b380e1`), the existing Jobs design, localStorage keys, and the FastAPI/SQLite/APScheduler stack. Do not update Career Ops to v1.40.0.
- A business from Maps alone is `employer_lead`; only a specific, checked `job_listing` may be `verified_job`. Timeout, 403, login wall, or malformed HTML means unknown check outcome, never expired.
- Never synthesize pay, WHV 462 eligibility, requirements, employer name, location, or an application URL. Save original source URLs and the supporting website excerpt.
- Keep collectors independent and default any new live collector to disabled. Credentials come from environment variables and stay out of Git. Back up the local SQLite database before live writes; do not edit ignored personal data during fixture tests.
- Respect source terms, rate limits, attribution, and content retention. Google Places permits storing Place IDs but restricts storage/indexing of Places content; do not persist its business name/address as an employer fact. Check the [official Places policy](https://developers.google.com/maps/documentation/places/web-service/policies) before enabling.
- Use the existing catalog/API and browser test conventions. Every change follows red → green → review → commit, with an isolated worktree created through `superpowers:using-git-worktrees` at execution time.

## Review Focus

1. An official website redirecting to localhost/private IP must be rejected before a request is sent; test in Task 1.
2. A generic careers page or “we are always hiring” must remain a lead or unverified post, never a verified job; test in Task 1.
3. A previously checked job whose site times out must retain its record; the pilot report records the failed check instead of calling the job closed; test in Tasks 2 and 4.
4. Repeated discovery of the same listing URL must preserve the existing opportunity ID, evidence, favorites, notes, and application status; test in Task 2 and browser step in Task 4.
5. One suburb/provider failure must not erase records or stop the other three suburbs; test in Task 4.

---

## Source-rights gate before live collection

Record the chosen source, exact permitted use, retention, attribution, API quota, and estimated pilot cost in `docs/superpowers/reviews/2026-11-13-employer-source-rights.md` before Task 3. Review the current Google Places terms for this employer-discovery use case. If permission is not established, do not run gosom or Places for persistent discovery; execute Tasks 1–2 and 4 with authorized website fixtures/seeds, report the live-source blocker, and choose a licensed source before Task 3. This gate is a product/legal decision, not a test that code can prove.

## File map

| Path | Responsibility |
| --- | --- |
| `sydney-hub/hub/discovery/models.py` | Typed transient candidates, site findings, and inspection outcomes. |
| `sydney-hub/hub/discovery/website.py` | Bounded public-site fetch, same-site career link discovery, and evidence classification. |
| `sydney-hub/hub/discovery/places.py` | Optional official provider adapter, enabled only after the source-rights gate. |
| `sydney-hub/hub/storage/catalog.py` | Atomic, idempotent upsert and exact-URL deduplication into existing tables. |
| `sydney-hub/hub/storage/db.py` | Reuse the verified SQLite backup and restore path for a pre-pilot snapshot. |
| `sydney-hub/run_employer_pilot.py` | Bounded four-suburb CLI, per-source failures, metrics, dry run, and JSON report under ignored local data. |
| `sydney-hub/config/settings.example.yml` | Disabled-by-default live-source settings and request budget. |
| `docs/superpowers/reviews/2026-11-13-employer-source-rights.md` | Versioned source-rights and cost decision; no API key or copied Places content. |
| `sydney-hub/tests/test_website_discovery.py`, `test_employer_pilot.py`, `test_catalog.py`, `test_api.py`, `test_jobs_ui.mjs` | Unit, repository, API, and browser proof without live network. |

### Task 1: Website evidence inspector

**Files:** Create `sydney-hub/hub/discovery/models.py`, `sydney-hub/hub/discovery/website.py`, `sydney-hub/tests/test_website_discovery.py`.

**Interfaces:**
- `EmployerCandidate(website_url: str, discovery_source: str, suburb: str, category: str, source_key: str | None = None)`; `source_key` is a Place ID only when the approved provider supplies it.
- `DiscoveryBatch(candidates: list[EmployerCandidate], requests: int, cost_estimate: float | None)`; `None` means the provider cost is unknown, not free.
- `WebsiteFinding(verification_state: Literal['verified_job','unverified_job_post','employer_lead'], source_url: str, employer_name: str | None, title: str | None, excerpt: str | None, checked_at: float)`.
- `SiteInspection(outcome: Literal['ok','timeout','blocked','error'], findings: list[WebsiteFinding], requests: int)`.
- `inspect_website(website_url: str, *, client: httpx.Client, checked_at: float) -> SiteInspection`; the client is injectable for tests. Only public HTTP(S) destinations may be requested, including each redirect. Inspect the homepage and at most three same-site career/listing pages, with a response-size and per-request timeout cap.

- [ ] **Step 1: Write failing tests** in `test_website_discovery.py`: `test_homepage_creates_lead` asserts one lead with the original website URL; `test_generic_careers_is_not_verified` asserts zero verified findings; `test_specific_barista_listing_is_verified` asserts title, exact URL, excerpt, and checked time; `test_timeout_has_no_job_finding` asserts `outcome='timeout'`; `test_redirect_to_loopback_is_blocked` asserts the fake client receives no loopback request. Keep pay and location absent from findings when the page omits them.
- [ ] **Step 2: Verify red.** Run `rtk pytest -q sydney-hub/tests/test_website_discovery.py`; expect failing imports/assertions, not a live request.
- [ ] **Step 3: Implement the listed interfaces.** Use `html.parser` and the existing `httpx` dependency. Limit redirects, bytes, pages, and domains; accept a verified listing only when a specific role page contains a job title and an application action. A company name is usable only when supported by the inspected website; otherwise return a finding for review, not an invented name. Keep source text as data, never instructions.
- [ ] **Step 4: Verify green.** Run `rtk pytest -q sydney-hub/tests/test_website_discovery.py`; expect all tests pass.
- [ ] **Step 5: Review and commit.** Check no network escapes the fake client and no unsupported claim enters findings. Commit only the three task files, e.g. `feat: classify employer website evidence`.

### Task 2: Persist findings without ID or state drift

**Files:** Modify `sydney-hub/hub/storage/catalog.py`, `sydney-hub/hub/storage/db.py`; extend `sydney-hub/tests/test_catalog.py`, `sydney-hub/tests/test_api.py`, and `sydney-hub/tests/test_storage.py`.

**Interfaces:** `CatalogRepository.upsert_site_inspection(candidate: EmployerCandidate, inspection: SiteInspection) -> tuple[list[str], list[str]]` returns `(new_ids, existing_ids)`. Reuse an existing ID on an exact normalized source URL; otherwise derive a stable ID from the website origin for a lead or from the listing URL for a job. Use existing `employers`, `opportunities`, and `opportunity_evidence`; evidence source types are `employer_website` and `job_listing`. Set `opportunities.source` to the candidate's discovery source while retaining the original inspected website URL as evidence. Never persist a Google Places name/address as a website fact.

- [ ] **Step 1: Write failing storage/API tests.** `test_site_lead_and_listing_upsert` asserts lead and verified listing with `job_listing.supports_job=1`; `test_hiring_mention_stays_unverified` asserts no verified record; `test_repeat_and_legacy_url_keep_id` asserts `(new_ids, existing_ids)` and one evidence row per URL/type; `test_timeout_preserves_status` asserts no opportunity updates; `test_verified_api_requires_checked_listing` asserts the original listing URL; `test_pilot_snapshot_restores_and_guards_new_writes` asserts rollback requires `force=True` after later writes.
- [ ] **Step 2: Verify red.** Run `rtk pytest -q sydney-hub/tests/test_catalog.py sydney-hub/tests/test_api.py`; expect new tests fail for missing upsert behavior.
- [ ] **Step 3: Implement the upsert in one SQLite transaction.** Normalize URLs for matching while retaining the original URL in evidence. Do not merge by fuzzy name or infer a vacancy from the provider. Preserve existing source-backed `published_at`, pay, visa, and requirements when the site does not state them; do not populate those fields from the discovery provider. Add `snapshot_catalog_for_pilot(path: Path) -> Path` in `db.py`, reusing `_backup()` and a separate manifest for `restore_catalog_backup(path, target_version=2)`; extend its allowed target versions to include 2.
- [ ] **Step 4: Verify green and rollback.** Run `rtk pytest -q sydney-hub/tests/test_catalog.py sydney-hub/tests/test_api.py sydney-hub/tests/test_storage.py`; expect all pass, including integrity and snapshot restoration.
- [ ] **Step 5: Review and commit.** Inspect SQL parameterization, transaction boundaries, foreign keys, saved-ID continuity, and exact original URLs. Commit only task files, e.g. `feat: persist inspected employer opportunities`.

### Task 3: Authorized business-source adapter

**Files:** Create `sydney-hub/hub/discovery/places.py`; modify `sydney-hub/config/settings.example.yml`; extend `sydney-hub/tests/test_employer_pilot.py`. Execute only after the source-rights gate permits this provider and its retention pattern. If another licensed source is chosen, replace `places.py` with a single adapter of the same interface and record the decision in the spec.

**Interfaces:** `discover_businesses(suburb: str, category: str, *, limit: int, client: httpx.Client, api_key: str) -> DiscoveryBatch`. The adapter returns transient website URL, category, suburb, source name, approved persistent key (Place ID for Places), request count, and a cost estimate only when the applicable price is known. It does not write SQLite. Credentials stay in the environment, not in the example config or report.

- [ ] **Step 1: Write failing adapter tests** with fake HTTP: `test_places_query_is_bounded` asserts exact suburb/category/limit and request count; `test_missing_website_is_skipped` asserts no candidate; `test_missing_key_sends_no_request` asserts an empty key raises before HTTP; `test_rate_limit_stops_at_budget` asserts no extra request; `test_places_response_keeps_only_allowed_fields` asserts the candidate contains only website URL, Place ID, category, suburb, and source.
- [ ] **Step 2: Verify red.** Run `rtk pytest -q sydney-hub/tests/test_employer_pilot.py`; expect missing adapter behavior.
- [ ] **Step 3: Implement the official adapter** using a field mask and request quota consistent with the recorded terms decision. Do not use gosom as a silent replacement for an unapproved API. Add only the disabled setting and bounded limit needed for this pilot.
- [ ] **Step 4: Verify green.** Run `rtk pytest -q sydney-hub/tests/test_employer_pilot.py`; expect all pass with no external calls.
- [ ] **Step 5: Review and commit.** Check attribution, retention, quota, and secret handling against the recorded rights decision; commit adapter, config, and tests.

### Task 4: Four-suburb pilot, measurement, and Jobs proof

**Files:** Create `sydney-hub/run_employer_pilot.py`; extend `sydney-hub/tests/test_employer_pilot.py`, `sydney-hub/tests/test_jobs_ui.mjs`, and `sydney-hub/tests/serve_ui_fixture.py`. Modify `sydney-hub/web/template.html` only if the existing labels/source links cannot display the new records correctly; preserve its layout and localStorage keys.

**Interfaces:** `run_pilot(suburbs: tuple[str, ...], categories: tuple[str, ...], *, discover: Callable[[str, str, int], DiscoveryBatch], inspect: Callable[[str], SiteInspection], catalog: CatalogRepository, max_requests: int, dry_run: bool) -> dict` returns per-suburb counts for leads, unverified and verified jobs, existing-URL duplicates, reachable source links, unknown link checks, request count, elapsed time, and provider cost estimate. The CLI defaults to the four suburbs, categories `cafe`, `restaurant`, `bar`, `hotel`, `cleaning service`, and `retail store`, and dry run; live mode requires the gate and an explicit flag.

- [ ] **Step 1: Write failing pilot tests.** `test_four_suburbs_are_isolated` asserts all four are attempted and one failure leaves the other three and old rows intact; `test_repeated_run_counts_existing_urls` asserts zero new IDs; `test_report_separates_unknown_links_and_cost` asserts unknown checks are not broken links and unknown cost is `null`; `test_dry_run_does_not_write` asserts no rows added; `test_disabled_provider_sends_no_request` asserts the default setting prevents external calls. Extend the browser fixture: a website-backed verified listing has its label and original link, keeps favorites/notes/status on reload, and fits mobile; the lead still says no confirmed job.
- [ ] **Step 2: Verify red.** Run `rtk pytest -q sydney-hub/tests/test_employer_pilot.py` and `rtk node sydney-hub/tests/test_jobs_ui.mjs`; expect the new assertions to fail.
- [ ] **Step 3: Implement the bounded runner and minimal UI support.** Write reports only under ignored `sydney-hub/data/pilot-reports/`; log source errors without tokens, phone numbers, or raw page text. Before live writes, make and integrity-check a SQLite snapshot. Keep live collection disabled by default and do not schedule recurring runs until the manual pilot passes.
- [ ] **Step 4: Verify green and review the live pilot.** Run `rtk pytest -q sydney-hub/tests`, `rtk node sydney-hub/tests/test_jobs_ui.mjs`, `rtk git diff --check`, then run the authorized pilot for Rockdale, Kogarah, Hurstville, Mascot. Manually sample every `verified_job` in this small pilot for a specific open listing, review duplicates and link validity, and publish the report's counts and cost per verified job. Any false verified label blocks expansion.
- [ ] **Step 5: Review and commit.** Check desktop/mobile and API-error state, snapshot restore, source attribution, and report redaction. Commit only reviewed task files; record the pilot metrics and remaining limits without committing local reports or credentials.

## Completion gate

The pilot is complete when fixture tests and browser checks pass, the authorized four-suburb run is reviewed, no lead is called a verified job, every verified item has a checked specific-listing URL, repeated runs preserve IDs, and the measured quality/cost report is available. If source rights or credentials prevent a live run, report the tested website-verification component as complete and the live Maps→employer step as blocked; do not label the pilot complete.
