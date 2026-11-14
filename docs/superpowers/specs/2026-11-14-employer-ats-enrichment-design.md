# SydneyStays — Employer Lead to Verified Job Enrichment (Track 1 Design)

**Date:** 2026-11-14  
**Status:** Ready for planning (`superpowers:writing-plans` next; builder-checked)  
**Baseline:** Delivery 2 (`codex/sydneystays-employer-pilot`, local `.osm.pbf` + SSRF-guarded website inspector, `146` OSM `employer_lead` rows in `sydney-hub/data/hub.db`)  
**Related Spec:** `docs/superpowers/specs/2026-11-13-sydneystays-final-engine.md`

---

## 1. Context & Diagnosis (Why Delivery 2 Returned 0 Verified Jobs)

Delivery 2 proved safe, zero-cost offline discovery over Geofabrik NSW (`new-south-wales-latest.osm.pbf`), persisting `146` `employer_lead` records (`0` fabricated fields, `0` false `verified_job` labels), but produced **`0` verified jobs**. Direct inspection of `sydney-hub/data/hub.db` and `sydney-hub/hub/discovery/` shows five compounding causes:

1. **Sparse OSM websites (`713 / 861` = `82.8%` missing):** Small independent venues frequently lack an independent website tag.
2. **Non-target retail dominated the budget (`78 / 146` = `53.4%`):** `hub/discovery/osm_pbf.py` maps all `shop=*` to `'retail store'` (e.g., opticians, butcheries, funeral parlours, blinds shops), while `cleaning service` captured **`0`** entries (`office=cleaning` only). Only **`68` leads** were hospitality (`cafe: 15`, `restaurant: 39`, `bar: 10`, `hotel: 4`).
3. **Social profiles and network blocks (`18` social + `40` failed checks):** `13` URLs point to `instagram.com` and `5` to `facebook.com`; `27` sites returned `blocked` (401/403/429), `6` `timeout`, and `7` `error`, leaving **`44` reachable, non-social hospitality websites**.
4. **Venue subpath URLs and external ATS isolation (`hub/discovery/website.py`):**
   - OSM tags often point to store/venue pages (e.g., `/store/starbucks-hurstville/`, `/eat-and-drink.html`, `/contact-us`) rather than site root `/`, so footer links to `/careers` on `/` are never visited.
   - `_same_site` discards subdomains (e.g., `careers.*`) and every outbound link or redirect to an external Applicant Tracking System (ATS) such as PageUp, SmartRecruiters, Workday, Dayforce, Greenhouse, Lever, or Employment Hero.
5. **Overly narrow static HTML matching (`website.py:190-198`):** Requires a role keyword match specifically inside `<h1>`, an `<a>` tag matching `_APPLY` (ignoring `<form>` and `Email resume / mailto:` application instructions without the word "apply"), no `_CAREER` token in `<h1>`, and vetoes the entire page if `_EVERGREEN` phrases (e.g., `"expressions of interest"` in a page footer) appear anywhere on the page.

---

## 2. Intent, Scope & Guardrails

**Intent:** Convert discovered `employer_lead` businesses in `Rockdale`, `Kogarah`, `Hurstville`, and `Mascot` into `verified_job` records when a real, open, location-matching vacancy exists — without breaking SSRF boundaries, fabricating fields, polluting pilot suburbs with outside-Sydney chain roles, or losing existing SQLite/UI state.

### Scope Decisions (Approved by User)
- **Selected Architecture:** **Approach 3 (Two-Track Pipeline)**, implementing **Track 1 only** in this pilot:
  1. Hospitality/cleaning prioritization over OSM & existing catalog leads.
  2. Internal career-page discovery & refined employer-site verification.
  3. Outbound external ATS detection + read-only Node bridge reusing Career Ops' **existing `131` ATS adapters (`providers/*.mjs`)** instead of duplicating parsers in Python.
- **Explicitly Deferred (Non-Goals):**
  - **Adzuna AU API (Track 2)** and any paid search/SERP engine (Google CSE, SerpAPI, JobSpy, Seek/Backpacker Job Board scraping).
  - Headless browser rendering of JS-only employer career SPAs (future phase if static + JSON ATS adapters remain insufficient).
  - Expanding discovery beyond `Rockdale`, `Kogarah`, `Hurstville`, and `Mascot`, enabling recurring APScheduler runs, or redesigning the Impeccable Jobs UI.
  - Modifying or deleting existing `retail store` rows in `sydney-hub/data/hub.db` (they remain preserved in SQLite, excluded only from this pilot's inspection priority queue).

### Anti-Goals & Invariants
- **Never interpret zero results as proof of no hiring:** A venue returning `0` listings (whether because of JS rendering, `403`, `timeout`, or no match) stays an `employer_lead` — never marked closed/expired/not-hiring.
- **Zero fabricated fields:** Missing `pay_display`, `pay_hourly_min`, `employment_type`, `experience_required`, `requirements`, and `visa_status` remain `NULL` / `'unknown'` unless explicitly stated in the verified source payload.
- **Preserve stack & baseline:** Keep FastAPI, SQLite, APScheduler, `CatalogRepository` evidence gate (`verified_job` requires `job_listing` with `supports_job=1` and `checked_at IS NOT NULL`), localStorage keys, and the Impeccable Jobs UI.

---

## 3. Target Architecture (Track 1 Pipeline)

### Component Overview

```text
  [1. Priority Queue Selector]
   Reads prioritized employers (OSM PBF + hospitality/cleaning tag expansion; excludes generic retail from queue, keeps DB intact)
          │
          ▼
  [2. Employer Site Career Discovery (Python, SSRF-hardened)]
   • Seed queue: stored URL + domain root '/' (if subpath) + bounded common paths (/careers, /jobs, /work-with-us)
   • Extract outbound links/iframes/redirects matching Career Ops ATS host patterns (allowlist, no arbitrary off-site crawl)
   • Refine static site verification (container-scoped evergreen check, mailto/form support)
          │
          ├──── (Direct verified vacancy on own site) ─────────────────────────┐
          │                                                                   │
          ├──── (Outbound ATS URL detected via providers/_registry.mjs) ──┐   │
          │                                                               ▼   ▼
          │                                            [3. Career Ops ATS Bridge (Node CLI, read-only)]
          │                                             • Loads providers/*.mjs (skipping 'local-parser')
          │                                             • Calls resolveProvider({ name, careers_url }) -> provider.fetch(entry, ctx)
          │                                             • Reuses providers/_ip-guard.mjs SSRF protections
          │                                             • Returns normalized JSON jobs with exact source_url & raw location
          │                                                               │
          │                                                               ▼
          │                                            [4. Strict Suburb & Role Filter (Python)]
          │                                             • Classifies target roles (hospitality/barista/bartender/hotel/housekeeping/cleaner)
          │                                             • Enforces explicit 4-suburb evidence: keeps job ONLY if ATS listing/detail
          │                                               explicitly names target suburb/postcode; unknown location -> dropped
          │                                             • Drops out-of-suburb & unlocated roles into audit counters
          │                                                               │
          ▼                                                               ▼
  [5. Catalog Persistence & UI Freshness (SQLite v4 + FastAPI + Jobs UI)]
   • Upserts via CatalogRepository (canonical URL key handles http/https & www. equivalence)
   • Distinguishes employer discovery_source ('OpenStreetMap discovery') from actual job source (ATS vendor / official website)
   • Stores ats_provider, role_bucket, suburb_match_basis, and check_outcome in structured v4 columns (excerpt stays faithful plain text)
   • Exposes last_verified_at & stale indicator in Jobs Explorer drawer while preserving ODbL attribution and deterministic pagination
```

---

### Detailed Component Design

#### 3.1 Prioritization & OSM Category Refinement (`hub/discovery/osm_pbf.py` & pilot selector)
- **Hospitality & Cleaning Priority Order & Canonical Taxonomy `[Design Call]`:** Map expanded OSM tags into the existing `_PILOT_CATEGORIES` strings (no schema taxonomy break) and prioritize solely:
  - `'cafe'`: `amenity` in `{"cafe"}` or `shop` in `{"bakery", "coffee", "pastry"}`
  - `'restaurant'`: `amenity` in `{"restaurant", "fast_food", "bistro", "food_court"}`
  - `'bar'`: `amenity` in `{"bar", "pub"}`
  - `'hotel'`: `tourism` in `{"hotel", "motel", "hostel", "guest_house"}`
  - `'cleaning service'`: `office=cleaning`, `shop` in `{"laundry", "dry_cleaning"}`, or `craft=cleaning`
- **Global 60-Employer Priority Queue Sourcing `[Design Call]`:** Differentiate prior HTTP inspection outcomes explicitly (`reachable_ok`, `reachable_no_title`, `blocked`, `timeout`, `error`, `uninspected`) rather than relying on `checked_at IS NOT NULL` alone. The Track 1 selector builds a **globally capped queue (`max_employers = 60`, balanced round-robin across the 4 suburbs)** ordered as:
  1. Tier 1: Existing `hub.db` hospitality/cleaning leads whose latest `employer_website` evidence confirms a successful HTTP fetch (`reachable_ok` — `checked_at IS NOT NULL` AND `excerpt` is not a `blocked`/`timeout`/`error` outcome; followed by `reachable_no_title` if needed) (`~44-45` venues).
  2. Tier 2: Newly unlocked `.osm.pbf` hospitality/cleaning candidates not yet in `hub.db` (from expanded `fast_food`, `bakery`, `bistro`, `food_court`, `motel`, `laundry`, `dry_cleaning`, `craft=cleaning` tags, up to remaining slots of the 60-employer cap).
  3. Any lead whose latest evidence recorded `blocked`, `timeout`, or `error` is **explicitly excluded** from Tiers 1–2 for this Track 1 pass (and preserved untouched in `hub.db`).
- **Generic Retail Handling:** Exclude generic `retail store` (`shop=*` outside bakery/coffee/pastry/laundry/dry_cleaning) from the Track 1 priority run queue (via `--priority hospitality` default). Existing `retail store` rows in `data/hub.db` are untouched.
- **Skip Non-Inspectable Social URLs from HTTP Budget Waste:** URLs whose host is `instagram.com`, `facebook.com`, `fb.com`, `tiktok.com`, or `linktr.ee` are logged as `social_only_skipped` leads (or left as existing `employer_lead` without burning HTTP requests against anti-bot social logins).

#### 3.2 Enhanced Employer Website Inspection (`hub/discovery/website.py`)
1. **SSRF & URL Hardening (Code Review Prerequisites Included):**
   - Reject multicast, unspecified, reserved, link-local, loopback, and private IPs (`addr.is_global and not addr.is_multicast and not addr.is_private and not addr.is_loopback and not addr.is_link_local and not addr.is_reserved and not addr.is_unspecified`).
   - Reject `'@' in parts.netloc` (closes empty-username `http://@host/` bypass), restrict `parts.port in {None, 80, 443}`, disallow `https://` → `http://` scheme downgrades on redirect, and iterate over all validated public IPs in `_PublicBackend.connect_tcp`.
2. **Root & Career Path Probing (Bounded):**
   - If the OSM `website_url` has a non-root path (e.g., `/store/hurstville` or `/contact-us`), seed the root URL `https://{host}/` in `queue` alongside the original URL so root navigation/footer links are scanned within `_MAX_PAGES` (raised moderately from `4` to `6` for career-page discovery while keeping overall per-site request ceiling bounded).
   - If no `_CAREER` link is discovered on the initial pages, probe at most 2 canonical fallback paths on the same site (`/careers`, `/jobs`) before concluding no internal careers page was found.
   - Ignore `script`/`style`/`noscript` using nesting-safe depth tracking (`_hidden_depth`).
3. **Refined Direct-Site Job Classification (Still Conservative):**
   - Expand role keywords (`_ROLE`) conservatively to explicitly cover backpacker priority targets: `barista`, `bartender`, `bar staff`, `waitstaff`, `waiter/waitress`, `kitchen hand`, `cook`, `chef`, `hospitality all-rounder`, `cafe all-rounder`, `cleaner`, `commercial cleaner`, `housekeeper`, `housekeeping`, `room attendant`, `front desk`, `receptionist` (only when paired with hospitality context).
   - Recognize direct application actions cleanly: `<a href="http(s)://...|mailto:...">` where anchor text OR surrounding section context expresses applying/sending a resume (`apply`, `send resume`, `email your cv`, `mailto:`).
   - Scope `_EVERGREEN` / `_CLOSED` checks so a generic footer notice (`"join our talent pool"`) does not automatically veto an explicit individual vacancy block (`h1`/`h2`/list item with role + apply link), while still keeping pages whose *only* copy is evergreen/speculative as `employer_lead` or `unverified_job_post`.
   - Fix double-emission bug: do not emit both `employer_lead` and `unverified_job_post` on the same URL.
4. **Outbound ATS Detection (No Arbitrary Off-Site Crawling):**
   - Inspect outbound `<a href>`, `<iframe src>`, and `301/302/303/307/308` `Location` targets encountered during inspection (without making HTTP requests to arbitrary third-party domains from Python).
   - Collect external URLs and pass them through the Career Ops ATS bridge matcher (`detect` only — zero-network in-memory match against `providers/*.mjs` patterns) or an explicit ATS host match (including `jobs.dayforcehcm.com`, `*.pageuppeople.com`, `*.myworkdayjobs.com`, `*.smartrecruiters.com`, `boards.greenhouse.io`, `jobs.lever.co`, `jobs.ashbyhq.com`, `*.bamboohr.com`, `*.employmenthero.com`, `*.teamtailor.com`, `*.pinpointhq.com`, `*.breezy.hr`, `*.recruitee.com`, etc.).
   - Also record `detected_ats_urls` on `SiteInspection` so unsupported ATS portals (e.g. `employmenthero.com` if not yet in `providers/*.mjs`, or `dayforcehcm` requiring Playwright) are explicitly counted as `ats_detected_unsupported` telemetry rather than silently dropped.

#### 3.3 Career Ops ATS Adapter Bridge (`providers/*.mjs` Reuse)
- **Zero Parser Duplication:** Reuse `providers/_registry.mjs` (`loadProviders`, `resolveProvider`) and `providers/_http.mjs` (`makeHttpCtx` + `_ip-guard.mjs`) via a small, strictly read-only Node CLI script (e.g., `sydney-hub/hub/discovery/ats_bridge.mjs` invoked by Python `subprocess.run` with JSON stdin/stdout, bounded timeout, and `shell=False`).
- **Two Modes in the Bridge:**
  1. `detect`: takes candidate URLs, loads providers with `skipIds: ['local-parser']`, runs `resolveProvider({ name, careers_url }, providers)` **with zero network calls**, and returns `{ url, matched: true, provider_id }` or `matched: false`.
  2. `fetch`: takes matched `{ name, careers_url, provider }`, calls `provider.fetch(entry, makeHttpCtx())` once under bounded timeout, and outputs `{ ok, provider_id, jobs: [{ title, url, company, location, postedAt, description }], error }`.
- **Dayforce Exclusion / Boundary Note:** `scan-dayforce.mjs` requires Playwright + CSRF bypass rather than `providers/_registry.mjs` HTTP JSON APIs; if Dayforce URLs are detected in the pilot, log them under `ats_detected_unsupported` (`dayforce`) for explicit feasibility evaluation rather than silently pulling Playwright into the bounded HTTP run.
- **Security & Resilience of the Subprocess Boundary:**
  - Strict `subprocess.run(["node", script, ...], shell=False)` with `skipIds: ['local-parser']` hardcoded in Node and sanitized environment.
  - Bounded stdin payload (`<= 256 KB`), bounded stdout payload (`<= 1 MB`), max jobs per provider (`<= 200`), max description length (`4,000` chars), per-provider HTTP request budget (`<= 25` requests), global Track 1 bridge request budget (`<= 250` requests), and capped per-provider timeout (`<= 20s` enforced by `AbortSignal.timeout` + Python subprocess timeout).
  - Strict runtime schema validation on all returned JSON records and per-adapter failure isolation (`try/except` + structured `{ ok: false, error }` results) so one broken, timing-out, or rate-limited provider never aborts discovery for remaining employers.

#### 3.4 Strict Suburb & Location Disambiguation (Critical Anti-Noise Control)
Chain ATS boards (e.g., Woolworths, Starbucks, Kogarah/Hurstville RSL groups, hotel chains) return vacancies across all of NSW or Australia, and OSM presence alone (even if an employer appears only once in OSM) does **not** prove where an ATS job is located. To prevent mixing outside-Sydney, CBD, or unlocated jobs into the four pilot suburbs:
1. **Explicit, Verifiable Job-Location Evidence Required (No OSM Single-Venue Inference):** Every ATS `Job` must present **explicit, verifiable evidence** placing the job in `Rockdale`/`2216`, `Kogarah`/`2217`, `Hurstville`/`2220`, or `Mascot`/`2020`.
2. Each ATS `Job` is classified by explicit location evidence:
   - If the job's `location`, `title`, `description`, or `url` from the ATS adapter explicitly names one of the 4 target suburbs or postcodes → assign to that exact suburb (`suburb_match = "explicit_job_location"`) and attach the matching location text/snippet as provenance.
   - Else if the job's `location` explicitly mentions **any other city, state, or Sydney suburb** (e.g., `Sydney CBD`, `Parramatta`, `Bondi`, `Melbourne`, `VIC`, `QLD`, etc.) → **drop** and increment `ats_jobs_dropped_outside_suburbs` (`0` cross-region leakage).
   - Else (job `location` is empty — as in `providers/pageup.mjs` list responses — or generic like `"Sydney"`, `"Sydney NSW"`, `"Australia"`):
     - Attempt at most a bounded per-employer cap (`<= 10` detail requests) of the posting's own ATS detail URL (strictly on the allowlisted ATS host under SSRF guards) ONLY if the ATS adapter omitted `location`/`description` on the list view. If the detail page explicitly names one of the 4 target suburbs/postcodes, assign with `suburb_match = "ats_detail_location"` and cite the excerpt.
     - Otherwise (still empty, generic `"Sydney NSW"`, or ambiguous) → **drop** into `ats_jobs_dropped_ambiguous_location`. Unknown job locations are **never** presented as verified local vacancies.
3. **Target Role Filtering & Relevance Tagging `[Design Call]`:** Tag matched jobs into operational backpacker buckets (`barista`, `bartender`, `hospitality`, `cleaner`, `housekeeping`, `kitchen`, `front_of_house`, `venue_operations`) while preserving the exact original `title` from the source (`title` is never rewritten). Drop clearly corporate/non-venue office postings on group ATS boards (e.g., `Accountant`, `Finance`, `IT Support`, `Software Engineer`, `HR`, `Marketing`, `Legal`) into `ats_jobs_dropped_non_target_role` so `verified_job` stays focused on hospitality/cleaning roles while `employer_lead` remains preserved.

#### 3.5 SQLite Persistence (Schema v4), Provenance, Rollback & API/UI Compatibility
- **SQLite Schema `v4` & Structured Provenance `[Design Call]`:** Migrate `PRAGMA user_version` cleanly `3 -> 4` in `hub/storage/db.py`:
  - Add structured nullable columns on `opportunities`: `discovery_source TEXT`, `ats_provider TEXT`, `role_bucket TEXT`, `suburb_match_basis TEXT`.
  - Add structured nullable column on `opportunity_evidence`: `check_outcome TEXT`.
  - Distinguish **employer discovery origin** (`discovery_source = 'OpenStreetMap discovery'`) from **actual job listing source** (`source`: real ATS provider name/ID such as `'PageUp'`, `'SmartRecruiters'`, or `'Official employer website'` for direct verified jobs; unverified OSM employer leads keep `source = 'OpenStreetMap discovery'`). During `v3 -> v4` migration, backfill `discovery_source = 'OpenStreetMap discovery'` on existing OSM rows.
  - Store `ats_provider`, `role_bucket`, `suburb_match_basis`, and `check_outcome` exclusively in their structured columns — **never** embed `[ats:...|role:...]` metadata tags inside `opportunity_evidence.excerpt`, keeping `excerpt` strictly faithful plain-text evidence from the source.
  - Update ODbL check in `web/template.html` to `(j.discovery_source === 'OpenStreetMap discovery' || j.source === 'OpenStreetMap discovery')` so ODbL attribution stays visible even when a verified job sets `source` to its ATS/official website.
- **Canonical URL Deduplication (`hub/storage/catalog.py`):**
  - Upgrade `_url_key` for lead matching so `http://cafe.example/` and `https://www.cafe.example/` resolve to the same existing lead ID while preserving `source_url` exactness for the winning evidence.
  - Do not write `"Website check outcome: ..."` into `opportunities.description`; record structured `check_outcome` and keep UI descriptions containing only real employer/listing text.
- **Schema v3->v4 Backup, Pre-Pilot v4 Snapshot & Dependency Hygiene `[Design Call]`:**
  - Opening the `147`-row `v3` `data/hub.db` creates a verified `v3` backup before `v4` migration (`restore_catalog_backup(..., target_version=3)` restores the exact `147`-row `v3` DB). `snapshot_catalog_for_pilot()` creates a pre-Track-1 `v4` backup (`target_version=4`, rotating any stale `user_version != 4` manifest cleanly) so `rollback_catalog.py --target-version 4 --force` restores the pre-Track-1 baseline and `--target-version 3 --force` restores pre-migration `v3`. Add `osmium>=4.0` to `sydney-hub/requirements.txt`.
- **API Pagination Compatibility & UI Freshness (Aligned to Final Engine Spec):**
  - Keep deterministic ordering (`ORDER BY o.first_seen_at DESC, o.id`) on `/api/opportunities`, support `offset: int = Query(0, ge=0)`, apply identical filter predicates to both `list()` and `count()` so `X-Total-Count` matches the filtered set accurately, and preserve all existing opportunity IDs, favorites, notes, application statuses, and `localStorage` keys.
  - Populate `opportunities.last_verified_at` on verified writes and expose `last_verified_at` + configurable staleness threshold (`stale_after_days: int = 14`, read-only indicator `"Verificada em DD/MM"` / `"Evidência >14 dias — revalidar"` in `#jobsDetailDrawer` of `web/template.html` without altering card layout).

---

## 4. Measurable Pilot Design & Telemetry

- **Target Cohort:** Up to **60 prioritized employers** across `Rockdale`, `Kogarah`, `Hurstville`, and `Mascot` (including the existing `44` reachable non-social hospitality leads + up to `16` additional hospitality/hotel/pub/cleaning candidates from the `.osm.pbf` with updated category tags).
- **Execution Mode:** Explicit manual CLI run (`--dry-run` default; `--live` after validated pre-pilot v3 snapshot), zero scheduled APScheduler background collection.
- **Per-Suburb & Pipeline Metrics Emitted to JSON Report (`sydney-hub/data/pilot-reports/`):**
  - `employers_in_priority_queue`, `retail_preserved_skipped`, `social_only_skipped`
  - `root_and_internal_pages_checked`, `career_pages_found`
  - `ats_detected_by_provider` (counts per `provider_id`, plus `ats_detected_unsupported`)
  - `ats_jobs_total_returned`, `ats_jobs_kept_target_suburbs`, `ats_jobs_dropped_outside_suburbs`, `ats_jobs_dropped_ambiguous_location`, `ats_jobs_dropped_non_target_role`
  - `verified_jobs_from_website`, `verified_jobs_from_ats`, `unverified_job_posts`, `employer_leads_preserved`
  - `failed_checks_by_outcome` (`blocked`, `timeout`, `error`)
  - `requests_python`, `requests_ats_bridge`, `elapsed_seconds`, `provider_cost_estimate` (`0.0`)

---

## 5. Acceptance Criteria

The Track 1 Enrichment Pilot is accepted **only when all criteria below pass** on a fresh verification run:

1. **Zero Parser Duplication & Real Adapter Execution:**
   - At least one end-to-end automated integration test executes the real Node bridge (`providers/_registry.mjs`) against fixture/local HTTP payloads for real Career Ops adapters (e.g., `pageup`, `smartrecruiters`, `greenhouse`/`lever`) returning verified listings.
   - Zero ATS parsing logic is reimplemented in Python.
2. **Real-World Conversion & Manual Audit Precision:**
   - The authorized 60-employer pilot runs across `Rockdale`, `Kogarah`, `Hurstville`, and `Mascot` cleanly (`errors == 0` unhandled, budget ceiling respected).
   - **Validation Target (`>= 5` verified jobs):** Finds and persists at least `5` real `verified_job` records with valid application URLs across the four suburbs; if fewer than `5` are found, report the measured results and observed limitations transparently without declaring the functional goal complete.
   - **100% of generated `verified_job` records** pass manual inspection: every item has an authentic job `title`, a real clickable listing URL (`source_url`) pointing to an open posting in one of the 4 suburbs (not Melbourne/CBD/other suburb), and matching `opportunity_evidence` (`source_type='job_listing'`, `supports_job=1`, `checked_at != NULL`).
   - **0% false positives:** generic careers pages, speculative `"always hiring"` pages, and failed/blocked/timed-out requests never appear as `verified_job`, and `0` results never mark a business as closed/not-hiring.
3. **Suburb Boundary Integrity:**
   - Multi-location chain ATS responses mentioning non-pilot locations and unlocated ATS postings (`""` or `"Sydney NSW"` without explicit target-suburb proof, even for single-OSM-entry businesses) are deterministically excluded from `verified_job` (`0` cross-region or unlocated job leaks in SQLite).
4. **Prioritization & Preservation of Existing Data:**
   - `retail store` rows already in `data/hub.db` remain completely intact (`count` unchanged, `0` deletions), while Track 1 HTTP budget prioritizes hospitality/barista/bartender/pub/hotel/housekeeping/cleaning targets.
   - Repeated pilot runs are idempotent: existing opportunity IDs, user `status`, notes, and favorites are preserved (`new_records == 0` on identical repeat), and `http://` → `https://www.` redirects do not duplicate leads.
5. **Security & Rollback Readiness:**
   - Python SSRF tests pass with real local socket checks and cover multicast (`224.0.0.1`), empty userinfo (`http://@host/`), non-HTTP ports, and HTTPS→HTTP downgrade blocks.
   - Node bridge enforces `skipIds: ['local-parser']` and `providers/_ip-guard.mjs`.
   - `osmium` is declared in `requirements.txt`, `Database` backs up `v3` before `v4` migration (`--target-version 3`), and `snapshot_catalog_for_pilot` / `rollback_catalog.py --target-version 4` cleanly restores the exact pre-pilot v4 database (`PRAGMA integrity_check == 'ok'`).
6. **Jobs Explorer & API Compatibility:**
   - FastAPI `/api/opportunities` (`offset`, deterministic ordering, matching filtered `X-Total-Count`, preserved IDs/favorites/notes/statuses), `pytest`, and Playwright browser tests (desktop & mobile) pass with `0` console/page errors, displaying `verified_job` items alongside `employer_lead`s, distinguishing `discovery_source` from `source`, showing `last_verified_at` in the detail drawer, and preserving ODbL attribution and zero fabricated salary/visa claims.

---

## 6. Left to the Builder / Implementation Planning Phase

To be sequenced in `superpowers:writing-plans`:
- Exact file splits for `ats_bridge.mjs` CLI options and Python wrapper in `sydney-hub/hub/discovery/`.
- Exact HTML block-scoping algorithm in `_Page` and fallback path limit (`_MAX_PAGES`).
- Whether pagination in `/api/opportunities` uses `offset` + `total` header/meta or bumped cap, and exact UI copy for staleness badges (`stale_after_days = 14`).
