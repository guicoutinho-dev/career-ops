# SydneyStays — Track 1 Employer Lead to Verified Job Enrichment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert prioritized hospitality and cleaning Employer Leads in `Rockdale`, `Kogarah`, `Hurstville`, and `Mascot` into verified, location-accurate job opportunities by refining employer-site career discovery and bridging Career Ops' 131 existing ATS adapters (`providers/*.mjs`).

**Architecture:** A Python priority-queue selector filters `hub.db` and `.osm.pbf` to hospitality/barista/bartender/hotel/housekeeping/cleaning employers (preserving `retail store` rows untouched in SQLite), inspects their websites for internal `/careers` pages and outbound ATS links/redirects under hardened SSRF rules, and invokes a read-only Node CLI bridge over `providers/_registry.mjs` (`skipIds: ['local-parser']`). Strict suburb/role filters accept an ATS vacancy as `verified_job` **only** with explicit job-level evidence naming a target suburb/postcode and operational hospitality/cleaning relevance, never inferring job location from OSM branch count or fabricating missing pay/visa fields.

**Tech Stack:** Python 3.12, FastAPI, SQLite (schema v4), `httpx`/`httpcore`, `osmium`, `pytest`, Node.js 24 (`providers/_registry.mjs`, `providers/_http.mjs`, `providers/_ip-guard.mjs`), Playwright, and the existing Impeccable Jobs UI (`web/template.html`).

**Spec:** `docs/superpowers/specs/2026-11-14-employer-ats-enrichment-design.md` (and baseline spec `docs/superpowers/specs/2026-11-13-sydneystays-final-engine.md`).

## Global Constraints

- Stay strictly in the isolated worktree `C:/Users/guicx/.codex/worktrees/sydneystays-employer-pilot/career-ops` (`codex/sydneystays-employer-pilot`); never push, merge, or deploy automatically, and never carry forward old Git history containing Gmail OAuth credentials (`config/gmail_*`).
- Reuse Career Ops' existing `131` ATS adapters in `providers/*.mjs` through `providers/_registry.mjs` and `providers/_ip-guard.mjs`; zero ATS parsers duplicated in Python.
- Preserve FastAPI, SQLite, APScheduler (keep recurring collection disabled), localStorage keys, ODbL attribution (`j.discovery_source === 'OpenStreetMap discovery' || j.source === 'OpenStreetMap discovery'`), deterministic pagination/filters (`X-Total-Count`), and the existing `147` rows + user state in `sydney-hub/data/hub.db`.
- Provenance separation (schema `v4`): distinguish `discovery_source = 'OpenStreetMap discovery'` from the real job listing `source` (ATS provider name or `'Official employer website'`), and store `ats_provider`, `role_bucket`, `suburb_match_basis`, and `check_outcome` in structured columns rather than embedding structured tags inside `opportunity_evidence.excerpt`.
- No fabricated job titles, pay, employment type, requirements, or WHV 462 status (`NULL` / `'unknown'` when omitted by source).
- Explicit job-location evidence required: single-venue presence in OSM is **not** sufficient to assign an unlocated ATS job; unknown/outside-suburb jobs never enter `verified_job`.
- Never use `checked_at IS NOT NULL` alone as proof of a successful HTTP fetch when prioritizing leads; differentiate `reachable_ok` / `reachable_no_title` vs `blocked` / `timeout` / `error`.
- Zero results (`0` listings), `403`, or timeouts never mark an employer as closed/not-hiring.
- Validation goal: discover and manually audit **>= 5** real `verified_job` records with valid application URLs across the 4 suburbs; if fewer than `5` are found, report observed metrics and limitations honestly without marking the functional goal complete.

## Review Focus

1. **Unlocated or multi-branch ATS postings masquerading as local roles:** An ATS board (`pageup.mjs` list row with `location=""`, `"Sydney NSW"`, or a chain store in `Parramatta`/`Melbourne`) returned for a Mascot/Rockdale employer must be dropped into `ats_jobs_ambiguous_location` or `ats_jobs_outside_suburbs` unless an allowlisted ATS detail page explicitly proves one of the 4 pilot suburbs; pin in **Task 5**.
2. **Error/timeout rows mistaken for `reachable_ok` in the 60-employer queue:** A `hub.db` row with a non-null `checked_at` paired with `Website check outcome: blocked|timeout|error` must still be classified as failed and excluded from Priority Tiers 1–2; pin in **Task 2**.
3. **SSRF edge cases missed by mock-only transport tests:** A redirect to multicast `224.0.0.1`, empty-username `http://@127.0.0.1/`, non-HTTP port `:6379`, or `https://` → `http://` downgrade must be blocked before connecting, plus a real local `127.0.0.1` socket test without mocking `connect_tcp`; pin in **Task 3**.
4. **Cross-protocol/www URL redirect duplicating an existing lead or failing to preserve user state:** An OSM lead stored at `http://cafe.example/` whose subsequent live inspection redirects to `https://www.cafe.example/` must keep the original `opportunity_id`, notes, and status without creating a duplicate row; pin in **Task 1**.
5. **Corporate group roles or JS/ATS bridge injection passing through Node:** An outbound URL attempting to trigger `local-parser` or returning an `Accountant`/`IT Support` vacancy in `Mascot` must have `local-parser` skipped by the bridge and non-target corporate roles filtered out of `verified_job`; pin in **Task 4** & **Task 5**.

---

## File Structure

| Path | Responsibility | Action |
| --- | --- | --- |
| `sydney-hub/requirements.txt` | Declare `osmium>=4.0` alongside existing runtime deps | Modify |
| `sydney-hub/hub/storage/db.py` | Migrate schema `v3 -> v4` (`discovery_source`, `ats_provider`, `role_bucket`, `suburb_match_basis`, `check_outcome`), back up `v3` (`--target-version 3`), rotate stale manifests for `v4` pilot snapshot (`--target-version 4`) | Modify |
| `sydney-hub/rollback_catalog.py` | Support `--target-version` `0..4` (`3` pre-v4, `4` pre-Track-1 pilot) | Modify |
| `sydney-hub/hub/storage/catalog.py` | Scheme/www canonical URL matching, explicit check-outcome classifier (`check_outcome` column + legacy fallback), provenance separation (`discovery_source` vs `source`), `last_verified_at` persistence, clean `description`, `offset` pagination + filtered `count()` | Modify |
| `sydney-hub/hub/discovery/models.py` | Carry `outbound_urls`, `ats_provider`, `ats_jobs_*` audit metadata cleanly | Modify |
| `sydney-hub/hub/discovery/osm_pbf.py` | Expand hospitality/cleaning OSM tag mapping into canonical categories | Modify |
| `sydney-hub/hub/discovery/priority.py` | Build balanced 60-employer Track 1 queue differentiating `reachable_ok` vs `blocked`/`timeout`/`error` and skipping retail/social | Create |
| `sydney-hub/hub/discovery/website.py` | Hardened SSRF checks, root `/` + `/careers`/`/jobs` fallback, container-scoped evergreen check, outbound ATS link/iframe/redirect collection | Modify |
| `sydney-hub/hub/discovery/ats_bridge.mjs` | Read-only Node CLI invoking `providers/_registry.mjs` (`skipIds: ['local-parser']`) + `makeHttpCtx()` | Create |
| `sydney-hub/hub/discovery/ats_bridge.py` | Safe `subprocess.run(..., shell=False)` wrapper + schema validation for `detect` and `fetch` modes + unsupported ATS catalog list | Create |
| `sydney-hub/hub/discovery/ats_filter.py` | Strict explicit-suburb disambiguator (with bounded ATS detail fetch when list omits location) + hospitality/cleaning role classifier | Create |
| `sydney-hub/run_employer_pilot.py` | Orchestrate Track 1 priority queue → site inspection → ATS bridge → location/role filter → SQLite + Track 1 metrics JSON | Modify |
| `sydney-hub/hub/api/app.py` | Add `offset: int = Query(0, ge=0)` and `X-Total-Count` header on `GET /api/opportunities` | Modify |
| `sydney-hub/web/template.html` | Render `last_verified_at` freshness / 14-day stale notice in `#jobsDetailDrawer` and pagination/truncation awareness | Modify |
| `sydney-hub/tests/serve_ui_fixture.py` | Add OSM coordinates + ATS-sourced `verified_job` with `last_verified_at` to fixture | Modify |
| `sydney-hub/tests/test_storage.py`, `test_catalog.py`, `test_osm_pbf.py`, `test_website_discovery.py`, `test_employer_pilot.py`, `test_api.py`, `test_jobs_ui.mjs` | TDD tests covering prerequisites, queue prioritization, SSRF, UI freshness/pagination | Modify |
| `sydney-hub/tests/test_priority_queue.py`, `test_ats_bridge.py`, `test_ats_filter.py` | Focused unit & real-Node integration suites for new Track 1 modules | Create |
| `docs/superpowers/reviews/2026-11-14-track1-ats-pilot-results.md` | Measured 60-employer pilot metrics & 100% manual audit report | Create |

---

### Task 1: Pre-Pilot Prerequisites (`osmium`, Schema v4 Provenance Columns & Rollback, Canonical URL Dedup & Clean Lead Metadata)

**Files:**
- Modify: `sydney-hub/requirements.txt`
- Modify: `sydney-hub/hub/storage/db.py:68-165,203-271`
- Modify: `sydney-hub/rollback_catalog.py`
- Modify: `sydney-hub/hub/storage/catalog.py`
- Test: `sydney-hub/tests/test_storage.py`, `sydney-hub/tests/test_catalog.py`

**Interfaces:**
- Produces:
  - Schema `v4` columns on `opportunities`: `discovery_source TEXT`, `ats_provider TEXT`, `role_bucket TEXT`, `suburb_match_basis TEXT`; and on `opportunity_evidence`: `check_outcome TEXT`. `Database(path)` backs up `v3 -> v4` under `target_version=3` (`.catalog-v4-rollback.json`), while `snapshot_catalog_for_pilot(path)` snapshots `v4` under `target_version=4` (`.employer-pilot-rollback.json`), rotating stale manifests whose backup `user_version != 4` or stale `after_sha256`. `restore_catalog_backup(path, target_version=3)` restores `v3` (falling back to `.employer-pilot-rollback.json` only if `.catalog-v4-rollback.json` does not exist).
  - `CatalogRepository._url_key(url: str) -> str` (strips `www.` prefix and normalizes `http`/`https` into `https` for identity lookup while storing original `source_url`).
  - `CatalogRepository.upsert_discovered_lead(candidate: EmployerCandidate, *, check_outcome: str, observed_at: float, checked_at: float | None = None) -> tuple[list[str], list[str]]` (`opportunities.description = None`; `discovery_source = candidate.discovery_source`; `check_outcome` stored in `opportunity_evidence.check_outcome` instead of encoding diagnostics in `description`).

- [ ] **Step 1: Write failing tests for v3->v4 migration/rollback, v4 pilot snapshot rotation, URL canonicalization across `http`/`www.`, and clean lead `description`**

```python
# In sydney-hub/tests/test_storage.py
def test_version_three_upgrade_backs_up_v3_and_migrates_structured_provenance(tmp_path):
    # Builds a v3 DB with 1 OSM lead, opens Database(path) -> upgrades to v4 with discovery_source backfilled,
    # verifies PRAGMA user_version == 4 and restore_catalog_backup(path, target_version=3) restores v3 cleanly.

def test_pilot_snapshot_rotates_stale_v2_manifest_and_preserves_v4_rows(tmp_path):
    db_path = tmp_path / "hub.db"
    db = Database(db_path)
    repo = CatalogRepository(db)
    repo.upsert_discovered_lead(
        EmployerCandidate("https://keep.example/", "OpenStreetMap discovery", "Mascot", "cafe", business_name="Keep Cafe"),
        check_outcome="ok", observed_at=100.0, checked_at=100.0,
    )
    stale_backup = tmp_path / "hub.db.pre-employer-pilot-stale.db"
    sqlite3.connect(stale_backup).close()
    manifest = tmp_path / "hub.db.employer-pilot-rollback.json"
    manifest.write_text(json.dumps({
        "backup": str(stale_backup),
        "before_sha256": hashlib.sha256(stale_backup.read_bytes()).hexdigest(),
        "after_sha256": "0" * 64,
    }), encoding="utf-8")

    snap = snapshot_catalog_for_pilot(db_path)
    assert sqlite3.connect(snap).execute("PRAGMA user_version").fetchone()[0] == 4
    assert sqlite3.connect(snap).execute("SELECT COUNT(*) FROM opportunities").fetchone()[0] == 1

# In sydney-hub/tests/test_catalog.py
def test_http_to_https_www_redirect_reuses_lead_id_and_keeps_description_clean(tmp_path):
    repo = CatalogRepository(Database(tmp_path / "hub.db"))
    lead = EmployerCandidate("http://cafe.example/", "OpenStreetMap discovery", "Rockdale", "cafe", business_name="Cafe One")
    new_ids, _ = repo.upsert_discovered_lead(lead, check_outcome="timeout", observed_at=10.0)
    assert repo.list()[0]["description"] is None
    assert repo.list()[0]["discovery_source"] == "OpenStreetMap discovery"
    assert repo.list()[0]["evidence"][0]["check_outcome"] == "timeout"

    inspection = SiteInspection("ok", [
        WebsiteFinding("verified_job", "https://www.cafe.example/", "Cafe One", "Barista", "Barista opening — Apply", 20.0),
    ], 1)
    # Leading URL canonicalization matches existing http://cafe.example/ when root domain/path match
    upd_new, upd_existing = repo.upsert_site_inspection(lead, inspection)
    assert upd_new == [] and upd_existing == new_ids
    assert len(repo.list()) == 1
```

- [ ] **Step 2: Run tests to verify failure**

Run: `Push-Location sydney-hub; python -m pytest tests/test_storage.py tests/test_catalog.py -k "version_three_upgrade or rotates_stale or http_to_https_www" -v`
Expected: FAIL (`Existing pilot backup is missing or changed`, duplicate ID created, `description` contains `"Website check outcome: timeout"`, schema remains `v3`).

- [ ] **Step 3: Implement updates in `requirements.txt`, `db.py`, `rollback_catalog.py`, and `catalog.py`**
  - Add `osmium>=4.0` to `sydney-hub/requirements.txt`.
  - In `sydney-hub/hub/storage/db.py`: add schema `v4` (`discovery_source`, `ats_provider`, `role_bucket`, `suburb_match_basis` on `opportunities`; `check_outcome` on `opportunity_evidence`), backfill `discovery_source = 'OpenStreetMap discovery'` where `source = 'OpenStreetMap discovery'`, back up `v3 -> v4` under `target_version = 3` (`.catalog-v4-rollback.json`), put pre-pilot `v4` snapshot under `target_version = 4` (`.employer-pilot-rollback.json`), rotate stale pilot manifests cleanly (`user_version != 4` or changed `after_sha256`), and keep legacy `target_version = 3` fallback if `.catalog-v4-rollback.json` is absent. Update `rollback_catalog.py` `--target-version` to accept `(0, 1, 2, 3, 4)`.
  - In `sydney-hub/hub/storage/catalog.py`: normalize `_url_key` scheme (`http` → `https`) and strip `www.` on `netloc`; keep `last_verified_at` written to `opportunities.last_verified_at` on `verified_job` upsert; accept optional `checked_at: float | None = None` in `upsert_discovered_lead`, store `discovery_source=candidate.discovery_source` and `description=None` on `opportunities`, store `check_outcome` cleanly on `opportunity_evidence.check_outcome` (with `"Website check outcome: ..."` read fallback in `list_employer_check_states()` only when `check_outcome IS NULL`), and expose structured fields in `list()`. Note: in `upsert_site_inspection`, match `_url_key(candidate.website_url)` only when `finding.verification_state != "verified_job"` OR when `url == candidate.website_url` normalized (distinct job URLs like `/careers/barista` still get their own `site_` ID per job, preserving 1:N employer→jobs!).

- [ ] **Step 4: Run tests and verify pass**

Run: `Push-Location sydney-hub; python -m pytest tests/test_storage.py tests/test_catalog.py -v`
Expected: PASS (`0` failed).

- [ ] **Step 5: Commit Task 1**

```bash
git add sydney-hub/requirements.txt sydney-hub/hub/storage/db.py sydney-hub/rollback_catalog.py sydney-hub/hub/storage/catalog.py sydney-hub/tests/test_storage.py sydney-hub/tests/test_catalog.py
git commit -m "feat: migrate SQLite schema to v4 provenance columns and canonicalize lead URLs"
```

---

### Task 2: Check-Outcome Classifier & 60-Employer Hospitality/Cleaning Priority Queue

**Files:**
- Modify: `sydney-hub/hub/discovery/osm_pbf.py:30-43,102-207`
- Modify: `sydney-hub/hub/storage/catalog.py` (add `list_employer_check_states()`)
- Create: `sydney-hub/hub/discovery/priority.py`
- Test: `sydney-hub/tests/test_osm_pbf.py`, `sydney-hub/tests/test_priority_queue.py`

**Interfaces:**
- Produces:
  - `classify_check_outcome(checked_at: float | None, excerpt: str | None) -> Literal['reachable_ok', 'reachable_no_title', 'blocked', 'timeout', 'error', 'uninspected']` in `hub/discovery/priority.py`.
  - `CatalogRepository.list_employer_check_states() -> list[dict]` returning `{id, source_url, company, suburb, category, lat, lng, verification_state, checked_at, excerpt}` for OSM leads.
  - `build_priority_queue(pbf_candidates: list[EmployerCandidate], db_states: list[dict], *, max_employers: int = 60, priority: Literal['hospitality', 'all'] = 'hospitality') -> PriorityQueueResult` returning `.selected: list[EmployerCandidate]`, `.counts: dict` (`retail_preserved_skipped`, `social_only_skipped`, `failed_history_skipped`, `tier1_reachable`, `tier2_fresh_osm`).

- [ ] **Step 1: Write failing tests for expanded OSM tags, non-conflation of `checked_at` errors, and 60-employer round-robin queue**

```python
# In sydney-hub/tests/test_priority_queue.py
def test_classify_outcome_never_treats_checked_at_with_error_excerpt_as_reachable():
    assert classify_check_outcome(1794630000.0, "Website check outcome: blocked") == "blocked"
    assert classify_check_outcome(1794630000.0, "Website check outcome: timeout") == "timeout"
    assert classify_check_outcome(1794630000.0, "Website check outcome: error") == "error"
    assert classify_check_outcome(1794630000.0, None) == "reachable_ok"
    assert classify_check_outcome(None, "Website check outcome: ok_no_title") == "reachable_no_title"

def test_priority_queue_skips_retail_social_and_failed_urls_and_balances_suburbs():
    db_states = [
        {"source_url": "https://ok-cafe.example/", "company": "OK Cafe", "suburb": "Rockdale", "category": "cafe",
         "lat": -33.95, "lng": 151.14, "checked_at": 100.0, "excerpt": None},
        {"source_url": "https://blocked-pub.example/", "company": "Blocked Pub", "suburb": "Mascot", "category": "bar",
         "lat": -33.92, "lng": 151.19, "checked_at": 100.0, "excerpt": "Website check outcome: blocked"},
        {"source_url": "https://retail-shop.example/", "company": "Optician", "suburb": "Hurstville", "category": "retail store",
         "lat": -33.96, "lng": 151.10, "checked_at": 100.0, "excerpt": None},
        {"source_url": "https://instagram.com/kogarahcafe", "company": "IG Cafe", "suburb": "Kogarah", "category": "cafe",
         "lat": -33.96, "lng": 151.13, "checked_at": 100.0, "excerpt": None},
    ]
    pbf = [
        EmployerCandidate("https://new-clean.example/", "OpenStreetMap discovery", "Kogarah", "cleaning service", business_name="Sparkle Clean"),
        EmployerCandidate("https://new-bakery.example/", "OpenStreetMap discovery", "Hurstville", "cafe", business_name="Hot Bake"),
        EmployerCandidate("https://new-shop.example/", "OpenStreetMap discovery", "Mascot", "retail store", business_name="Shoe Store"),
    ]
    res = build_priority_queue(pbf, db_states, max_employers=60, priority="hospitality")
    urls = [c.website_url for c in res.selected]
    assert urls == ["https://ok-cafe.example/", "https://new-clean.example/", "https://new-bakery.example/"]
    assert res.counts["retail_preserved_skipped"] >= 2
    assert res.counts["social_only_skipped"] == 1
    assert res.counts["failed_history_skipped"] == 1
```

Also add a test in `sydney-hub/tests/test_osm_pbf.py` asserting OSM tags `amenity=fast_food`, `shop=bakery`, `tourism=motel`, `shop=laundry`, and `craft=cleaning` map into `'restaurant'`, `'cafe'`, `'hotel'`, and `'cleaning service'`.

- [ ] **Step 2: Run tests to verify failure**

Run: `Push-Location sydney-hub; python -m pytest tests/test_priority_queue.py tests/test_osm_pbf.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'hub.discovery.priority'` and missing tag mappings).

- [ ] **Step 3: Implement expanded tag mappings in `osm_pbf.py`, `list_employer_check_states` in `catalog.py`, and `hub/discovery/priority.py`**
  - Update `_TAG_KEYS = ("amenity", "tourism", "office", "shop", "craft")` and `_category_for_tags()` in `osm_pbf.py`.
  - Add `list_employer_check_states()` in `catalog.py` querying latest `employer_website` evidence row (`ORDER BY observed_at DESC LIMIT 1`) per OSM `employer_lead` / opportunity.
  - Implement `classify_check_outcome()` and `build_priority_queue()` in `priority.py` with social-host detection (`instagram.com`, `facebook.com`, `fb.com`, `tiktok.com`, `linktr.ee`), explicit exclusion of `retail store` when `priority == 'hospitality'`, explicit exclusion of `blocked`/`timeout`/`error`, Tier 1 (`reachable_ok`, `reachable_no_title`) ordered round-robin by suburb followed by Tier 2 (fresh uninspected `.osm.pbf` hospitality/cleaning) round-robin by suburb, capped at `max_employers`.

- [ ] **Step 4: Run tests to verify pass**

Run: `Push-Location sydney-hub; python -m pytest tests/test_priority_queue.py tests/test_osm_pbf.py tests/test_catalog.py -v`
Expected: PASS.

- [ ] **Step 5: Commit Task 2**

```bash
git add sydney-hub/hub/discovery/osm_pbf.py sydney-hub/hub/discovery/priority.py sydney-hub/hub/storage/catalog.py sydney-hub/tests/test_osm_pbf.py sydney-hub/tests/test_priority_queue.py
git commit -m "feat: add explicit check-outcome priority queue and hospitality/cleaning OSM tags"
```

---

### Task 3: SSRF Hardening, Root/Careers Discovery & Outbound ATS Detection (`website.py`)

**Files:**
- Modify: `sydney-hub/hub/discovery/models.py` (add `outbound_urls: tuple[str, ...] = ()` to `SiteInspection` and `check_outcome: str = 'ok'` if helpful)
- Modify: `sydney-hub/hub/discovery/website.py`
- Test: `sydney-hub/tests/test_website_discovery.py`

**Interfaces:**
- Consumes: `SiteInspection`, `WebsiteFinding`.
- Produces:
  - `_is_public_ip(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool`
  - `SiteInspection.outbound_urls: tuple[str, ...]` (deduplicated external `http(s)` links, iframe `src`s, and external redirect `Location` targets captured without making off-site Python HTTP requests).

- [ ] **Step 1: Write failing unit & real-socket SSRF tests + career-discovery tests in `test_website_discovery.py`**

Include tests for:
1. `test_blocks_multicast_empty_userinfo_non_http_port_and_https_downgrade`: asserts `https://224.0.0.1/`, `http://@cafe.example/`, `https://cafe.example:6379/`, and `https://cafe.example/` → 302 `http://cafe.example/careers` all return `outcome == "blocked"` with `seen == []` (for invalid URLs) or `outcome == "blocked"` (on downgrade).
2. `test_real_local_socket_loopback_blocked_without_connect_tcp_mock`: starts a real `http.server.ThreadingHTTPServer` on `127.0.0.1:0` in a fixture (clearing the `monkeypatch` on `getaddrinfo`/`connect_tcp`), calls `inspect_website(f"http://127.0.0.1:{port}/", client=safe_website_client(), checked_at=1.0)`, and asserts `outcome == "blocked"` and `server_hits == 0`.
3. `test_public_backend_falls_back_to_second_public_ip_on_connect_error`: stubs `getaddrinfo` returning two public IPs (`2001:db8::1` or unreachable `93.184.215.1` then `93.184.215.14`), first `connect_tcp` raises `OSError`, second succeeds.
4. `test_subpath_url_seeds_root_and_probes_canonical_careers_capturing_outbound_ats`: starting at `https://venue.example/eat-and-drink.html` (with `/careers` on root `/`, plus `<iframe src="https://acme.pageuppeople.com/123/cw/en/listing/">` and `<a href="https://jobs.lever.co/acme">`) visits `/`, finds `/careers`, and records both external URLs in `result.outbound_urls` while making **0** off-site HTTP requests.
5. `test_footer_talent_pool_does_not_veto_explicit_listing_and_mailto_apply_works`: a careers page with `<h2>Barista</h2><p>Send resume to jobs@cafe.example <a href="mailto:jobs@cafe.example">Email resume</a></p>` and `<footer class="footer">Join our talent pool anytime</footer>` classifies the vacancy as `verified_job` while still returning `employer_lead` (not double-emitting `[employer_lead, unverified_job_post]`) when only hiring mentions exist.

- [ ] **Step 2: Run tests to verify failure**

Run: `Push-Location sydney-hub; python -m pytest tests/test_website_discovery.py -v`
Expected: FAIL on the 5 new tests.

- [ ] **Step 3: Implement SSRF hardening and enhanced career discovery in `models.py` & `website.py`**
  - Update `SiteInspection` in `models.py` with `outbound_urls: tuple[str, ...] = ()` (defaulting to `()` for full backward compatibility).
  - In `website.py`: implement `_is_public_ip()`, update `_public_url` (`'@' in parts.netloc`, `parts.port in {None, 80, 443}`), forbid `https` → `http` downgrade inside `_fetch`, and loop over all validated public IPs in `_PublicBackend.connect_tcp`.
  - Use `_hidden_depth` counter in `_Page`, collect `<iframe src>` links too, seed root `/` when `website_url` has a non-root path, probe at most `/careers` and `/jobs` if no `_CAREER` link was discovered (`_MAX_PAGES = 6`), collect off-site `http(s)` links/redirects in `outbound_urls` (`blocked` only if an off-site redirect actually happened before any usable page when `queue` has no other work; record external ATS redirect target in `outbound_urls` cleanly), split page inspection per block/heading section so footer `_EVERGREEN` text doesn't falsely veto an explicit role listing section, support `mailto:` + `"email resume/cv"`, and eliminate `[employer_lead, unverified_job_post]` double-emission on the same URL.

- [ ] **Step 4: Run tests to verify pass**

Run: `Push-Location sydney-hub; python -m pytest tests/test_website_discovery.py -v`
Expected: ALL tests in `test_website_discovery.py` PASS.

- [ ] **Step 5: Commit Task 3**

```bash
git add sydney-hub/hub/discovery/models.py sydney-hub/hub/discovery/website.py sydney-hub/tests/test_website_discovery.py
git commit -m "feat: harden SSRF guards, discover root careers, and capture outbound ATS links"
```

---

### Task 4: Read-Only Career Ops ATS Bridge (`ats_bridge.mjs` + `ats_bridge.py`)

**Files:**
- Create: `sydney-hub/hub/discovery/ats_bridge.mjs`
- Create: `sydney-hub/hub/discovery/ats_bridge.py`
- Test: `sydney-hub/tests/test_ats_bridge.py`

**Interfaces:**
- Consumes: `providers/_registry.mjs` (`loadProviders`, `resolveProvider`), `providers/_http.mjs` (`makeHttpCtx`).
- Produces:
  - `AtsMatch(url: str, provider_id: str | None, supported: bool, unsupported_vendor: str | None)`
  - `AtsJob(title: str, url: str, company: str, location: str, description: str | None, posted_at: int | None)`
  - `AtsFetchResult(ok: bool, provider_id: str, jobs: list[AtsJob], error: str | None, requests: int)`
  - `AtsBridge(providers_dir: Path, *, allow_private_test_urls: bool = False, max_stdin_bytes: int = 262_144, max_stdout_bytes: int = 1_048_576, max_jobs_per_provider: int = 200, max_requests_per_provider: int = 25, max_total_requests: int = 250)` with `.detect(urls: list[str], *, company: str = "") -> list[AtsMatch]` and `.fetch(url: str, *, company: str, provider_id: str, timeout_s: float = 20.0) -> AtsFetchResult` (enforces input/output byte caps, strict schema validation, per-provider + global request budget tracking, timeout cap, and `try/except` isolation returning `{ok: False, error: ...}` without aborting the caller).

- [ ] **Step 1: Write failing real-Node integration tests in `sydney-hub/tests/test_ats_bridge.py`**

Write tests that execute the **real** `node sydney-hub/hub/discovery/ats_bridge.mjs`:
1. `test_detect_identifies_supported_ats_and_flags_unsupported_without_network`: passes URLs for `https://boards.greenhouse.io/acmecafe`, `https://careers.pageuppeople.com/999/cw/en/listing/`, `https://jobs.dayforcehcm.com/en-US/brand/CANDIDATEPORTAL`, `https://app.employmenthero.com/en/jobs/1`, and `https://evil.example/local-parser` → verifies `greenhouse` & `pageup` return `supported=True`, `dayforce` & `employmenthero` return `supported=False, unsupported_vendor=...`, and `local-parser` is **never** matched (`skipIds: ['local-parser']` enforced).
2. `test_real_bridge_fetches_from_live_local_greenhouse_and_pageup_fixture_servers`: starts a local `ThreadingHTTPServer` serving realistic Greenhouse API JSON (or mocks URL rewrite/test hook explicitly scoped via an env var ONLY when invoked under test, or tests `providers/greenhouse.mjs` / `smartrecruiters.mjs` / `pageup.mjs` with `_ip-guard.mjs` loopback verification + an in-repo stub provider test under `tmp_path` so real `loadProviders` + `resolveProvider` + `fetch` run end-to-end over real HTTP without hitting external internet!). Also add explicit tests verifying `ats_bridge.mjs` blocks loopback SSRF (`http://127.0.0.1/...`) in production mode when `CAREER_OPS_ALLOW_TEST_LOCALHOST` is unset, rejects oversized stdin/stdout payloads, stops cleanly when global/per-provider request budgets are exhausted, validates malformed provider JSON outputs safely, and isolates hanging/throwing providers via timeout without aborting subsequent calls.

- [ ] **Step 2: Run tests to verify failure**

Run: `Push-Location sydney-hub; python -m pytest tests/test_ats_bridge.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'hub.discovery.ats_bridge'`).

- [ ] **Step 3: Implement `ats_bridge.mjs` and `ats_bridge.py`**
  - `ats_bridge.mjs` reads one JSON payload from `stdin`:
    - Always loads providers with `loadProviders(providersDir)` and calls `resolveProvider({ name, careers_url: url }, providers, { skipIds: ['local-parser'] })` (`'local-parser'` unconditionally skipped).
    - In `detect` mode: zero network calls; matches against `providers` AND a small read-only known-unsupported regex map (`dayforcehcm.com` → `'dayforce'`, `employmenthero.com` → `'employmenthero'`, `scouttalent` → `'scout'`).
    - In `fetch` mode: resolves provider from URL (ignoring `'local-parser'`), runs `provider.fetch({ name: company, careers_url: url, provider: providerId }, makeHttpCtx(...))` inside an `AbortSignal.timeout(timeoutMs)`, and emits validated JSON on stdout.
  - `ats_bridge.py` calls `subprocess.run(["node", str(BRIDGE_JS), ...], input=..., capture_output=True, text=True, timeout=timeout_s + 2, shell=False)`, validates outputs into `AtsMatch` / `AtsFetchResult`, and cleanly surfaces timeout/parse/HTTP errors without raising unhandled exceptions.

- [ ] **Step 4: Run tests to verify pass**

Run: `Push-Location sydney-hub; python -m pytest tests/test_ats_bridge.py -v`
Expected: PASS.

- [ ] **Step 5: Commit Task 4**

```bash
git add sydney-hub/hub/discovery/ats_bridge.mjs sydney-hub/hub/discovery/ats_bridge.py sydney-hub/tests/test_ats_bridge.py
git commit -m "feat: add read-only Career Ops ATS adapter Node bridge"
```

---

### Task 5: Explicit Suburb-Location Evidence & Target-Role Filter (`ats_filter.py`)

**Files:**
- Create: `sydney-hub/hub/discovery/ats_filter.py`
- Test: `sydney-hub/tests/test_ats_filter.py`

**Interfaces:**
- Consumes: `AtsJob` (from Task 4), `safe_website_client` / `_public_url` (from Task 3) or injected fetcher for bounded detail check.
- Produces:
  - `FilteredAtsJob(job: AtsJob, matched_suburb: str, role_bucket: str, match_basis: Literal['explicit_job_location', 'ats_detail_location'], location_evidence: str)`
  - `AtsFilterSummary(kept: list[FilteredAtsJob], dropped_outside_suburbs: int, dropped_ambiguous_location: int, dropped_non_target_role: int, detail_requests: int)`
  - `filter_ats_jobs(jobs: list[AtsJob], *, expected_suburb: str, allowlisted_host: str, fetch_detail: Callable[[str], str | None] | None = None, max_detail_requests: int = 10) -> AtsFilterSummary`

- [ ] **Step 1: Write failing tests enforcing Explicit Suburb Evidence (Adjustment 1) and Operational Role Focus**

```python
# In sydney-hub/tests/test_ats_filter.py
def test_unlocated_and_generic_sydney_ats_jobs_are_never_kept_without_detail_evidence():
    # Even if an employer has only 1 entry in OSM, empty "" or "Sydney NSW" WITHOUT detail evidence MUST be dropped!
    jobs = [
        AtsJob("Barista", "https://careers.pageuppeople.com/1/cw/en/job/10/barista", "Local Pub", "", None, None),
        AtsJob("Cleaner", "https://boards.greenhouse.io/x/jobs/2", "Local Pub", "Sydney, NSW", None, None),
    ]
    res = filter_ats_jobs(jobs, expected_suburb="Rockdale", allowlisted_host="careers.pageuppeople.com", fetch_detail=lambda _u: "<p>Work across Greater Sydney</p>")
    assert res.kept == []
    assert res.dropped_ambiguous_location == 2

def test_ats_detail_evidence_unlocks_unlocated_pageup_job_only_when_pilot_suburb_named():
    jobs = [AtsJob("Bartender", "https://careers.pageuppeople.com/1/cw/en/job/11/bartender", "Pub Group", "", None, None)]
    res = filter_ats_jobs(
        jobs, expected_suburb="Rockdale", allowlisted_host="careers.pageuppeople.com",
        fetch_detail=lambda _u: "<h1>Bartender</h1><p>Location: Rockdale NSW 2216</p><a href='/apply'>Apply</a>",
    )
    assert len(res.kept) == 1
    assert res.kept[0].matched_suburb == "Rockdale"
    assert res.kept[0].match_basis == "ats_detail_location"

def test_outside_suburb_and_corporate_roles_are_dropped():
    jobs = [
        AtsJob("Barista", "https://x.example/1", "Cafe Chain", "Parramatta, NSW", None, None),
        AtsJob("Senior Accountant", "https://x.example/2", "Airport Hotel", "Mascot NSW 2020", None, None),
        AtsJob("Room Attendant / Housekeeping", "https://x.example/3", "Airport Hotel", "Mascot NSW 2020", None, None),
    ]
    res = filter_ats_jobs(jobs, expected_suburb="Mascot", allowlisted_host="x.example")
    assert [k.job.title for k in res.kept] == ["Room Attendant / Housekeeping"]
    assert res.kept[0].role_bucket == "housekeeping"
    assert res.dropped_outside_suburbs == 1
    assert res.dropped_non_target_role == 1
```

- [ ] **Step 2: Run tests to verify failure**

Run: `Push-Location sydney-hub; python -m pytest tests/test_ats_filter.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'hub.discovery.ats_filter'`).

- [ ] **Step 3: Implement `hub/discovery/ats_filter.py`**
  - Define exact regexes for the 4 pilot suburbs + official postcodes (`Rockdale`/`2216`, `Kogarah`/`2217`, `Hurstville`/`2220`, `Mascot`/`2020`).
  - Reject any non-empty location mentioning other cities/states (`Melbourne`, `VIC`, `Brisbane`, `QLD`, `Perth`, `Adelaide`, `CBD`, `Parramatta`, `Bondi`, `Chatswood`, etc.) when none of the 4 pilot suburbs/postcodes appear → `dropped_outside_suburbs`.
  - When list-level location/title/description/url is empty or generic (`"Sydney"`, `"Sydney NSW"`, `"NSW"`, `"Australia"`), perform bounded detail lookup (`fetch_detail` strictly constrained to same `allowlisted_host` and `max_detail_requests <= 10` per employer) to look for explicit target suburb/postcode text; if absent → `dropped_ambiguous_location`.
  - Tag role bucket (`barista`, `bartender`, `housekeeping`, `cleaner`, `kitchen`, `front_of_house`, `hospitality`) and drop corporate/back-office roles (`accountant`, `finance`, `software engineer`, `developer`, `it support`, `human resources`, `hr manager`, `legal counsel`, `marketing manager`, `payroll`) into `dropped_non_target_role`.

- [ ] **Step 4: Run tests to verify pass**

Run: `Push-Location sydney-hub; python -m pytest tests/test_ats_filter.py -v`
Expected: PASS.

- [ ] **Step 5: Commit Task 5**

```bash
git add sydney-hub/hub/discovery/ats_filter.py sydney-hub/tests/test_ats_filter.py
git commit -m "feat: enforce explicit suburb location proof and operational role filter for ATS jobs"
```

---

### Task 6: Wire Track 1 Enrichment into `run_employer_pilot.py` & `CatalogRepository`

**Files:**
- Modify: `sydney-hub/hub/discovery/models.py` (optional provenance fields on `WebsiteFinding`: `listing_source: str | None = None`, `ats_provider: str | None = None`, `role_bucket: str | None = None`, `suburb_match_basis: str | None = None`, `discovery_source: str | None = None`)
- Modify: `sydney-hub/hub/storage/catalog.py` (persist `discovery_source` vs actual `source`, structured `ats_provider`, `role_bucket`, `suburb_match_basis`, and `check_outcome`, keeping `opportunity_evidence.excerpt` faithful plain text)
- Modify: `sydney-hub/run_employer_pilot.py`
- Test: `sydney-hub/tests/test_employer_pilot.py`

**Interfaces:**
- Consumes: `build_priority_queue` (Task 2), `inspect_website` (Task 3), `AtsBridge` (Task 4), `filter_ats_jobs` (Task 5), `CatalogRepository` (Tasks 1–2).
- Produces:
  - `run_track1_enrichment(...) -> dict` (returning all Section 4 Track 1 telemetry counters; zero results keep existing `employer_lead` untouched; isolates per-employer/per-provider failures cleanly).
  - Updated CLI flags on `run_employer_pilot.py`: `--priority {hospitality,all}` (default `hospitality` for Track 1), `--max-employers` (default `60`), keeping `--dry-run` default and requiring `--live` + verified `v4` snapshot for writes.

- [ ] **Step 1: Write failing pilot orchestration tests in `test_employer_pilot.py`**

Add tests for:
1. `test_track1_enrichment_converts_ats_jobs_keeps_unlocated_as_lead_and_zero_results_never_expire`: simulates a 3-employer Track 1 queue (`hotel` with outbound `pageup` link yielding 1 Mascot housekeeper + 1 Parramatta waiter + 1 unlocated cleaner; `cafe` returning 0 jobs; `restaurant` returning 403 or hanging ATS provider isolated cleanly) → asserts only the Mascot housekeeper becomes `verified_job` with `supports_job=1`, `checked_at != None`, `last_verified_at != None`, `opportunities.discovery_source == 'OpenStreetMap discovery'`, `opportunities.source == 'PageUp'`, `ats_provider == 'pageup'`, `role_bucket == 'housekeeping'`, `suburb_match_basis` set cleanly, and `opportunity_evidence.excerpt` plain source text without `[ats:...]` tags; all 3 businesses remain preserved (`employer_lead`s never marked closed); telemetry counts `ats_jobs_dropped_outside_suburbs == 1`, `ats_jobs_dropped_ambiguous_location == 1`, `verified_jobs_from_ats == 1`.
2. `test_track1_live_preserves_retail_store_rows_in_hub_db`: seeds `retail store` rows in SQLite, runs `run_track1_enrichment` with `--priority hospitality`, verifies `retail store` rows remain `100%` unchanged and `retail_preserved_skipped` increments.

- [ ] **Step 2: Run tests to verify failure**

Run: `Push-Location sydney-hub; python -m pytest tests/test_employer_pilot.py -v`
Expected: FAIL on new Track 1 tests.

- [ ] **Step 3: Implement Track 1 orchestration in `models.py`, `catalog.py` & `run_employer_pilot.py`**
  - Keep `run_pilot()` backward-compatible for existing Delivery 2 unit tests, and add `run_track1_enrichment(...)` wired to `--priority` / `--max-employers` CLI options.
  - Persist `ok_no_title` cleanly via `check_outcome='ok_no_title'` and `checked_at` timestamp (still excluded safely from failures by `classify_check_outcome`), isolate each employer/ATS adapter call with `try/except` so one provider failure never aborts the rest of the queue, and record ATS/website `verified_job` items via `CatalogRepository.upsert_site_inspection` setting `discovery_source = candidate.discovery_source` (`'OpenStreetMap discovery'`), `source` to the actual job listing source (`'PageUp'`, `'SmartRecruiters'`, ..., or `'Official employer website'`), and structured `ats_provider`, `role_bucket`, `suburb_match_basis` columns while keeping `opportunity_evidence.excerpt` strictly faithful plain text.

- [ ] **Step 4: Run full Python backend test suite to verify pass**

Run: `Push-Location sydney-hub; python -m pytest -ra`
Expected: ALL backend tests PASS (`0` skipped, `0` failed).

- [ ] **Step 5: Commit Task 6**

```bash
git add sydney-hub/hub/discovery/models.py sydney-hub/hub/storage/catalog.py sydney-hub/run_employer_pilot.py sydney-hub/tests/test_employer_pilot.py
git commit -m "feat: orchestrate Track 1 hospitality priority, ATS bridge, and structured provenance persistence"
```

---

### Task 7: Jobs Explorer Freshness UI (`last_verified_at` + Staleness Notice), ODbL Provenance & API Pagination Compatibility

**Files:**
- Modify: `sydney-hub/hub/storage/catalog.py` (`offset: int = 0`, deterministic `ORDER BY o.first_seen_at DESC, o.id ASC`, and filtered `count()` helper on `CatalogRepository`)
- Modify: `sydney-hub/hub/api/app.py:177-194`
- Modify: `sydney-hub/web/template.html` (`loadJobsCatalog`, drawer freshness badge `last_verified_at` / 14-day stale warning, ODbL attribution check `(j.discovery_source === 'OpenStreetMap discovery' || j.source === 'OpenStreetMap discovery')`)
- Modify: `sydney-hub/tests/serve_ui_fixture.py`, `sydney-hub/tests/test_api.py`, `sydney-hub/tests/test_jobs_ui.mjs`

**Interfaces:**
- Consumes: `GET /api/opportunities?limit=500&offset=0` returning deterministically ordered JSON list + exact filtered `X-Total-Count` response header.
- Produces: drawer badge `"Verificada em DD/MM/AAAA"` (and `"Evidência >14 dias — revalidar"` when age > 14 days) whenever `j.last_verified_at` exists on `verified_job`, ODbL attribution preserved even when `source` is an ATS provider and `discovery_source === 'OpenStreetMap discovery'`, plus multi-page fetch in `loadJobsCatalog` when `X-Total-Count > 500`.

- [ ] **Step 1: Write failing API pagination & browser freshness/provenance assertions**
  - In `test_api.py`: test `GET /api/opportunities?limit=1&offset=1` returns deterministically ordered second item with `response.headers['x-total-count'] == '2'`, test filtered queries (`?state=verified_job&suburb=Rockdale&limit=1&offset=0`) set `X-Total-Count` to the filtered total (not unfiltered total), and verify `discovery_source` + `source` round-trip cleanly without breaking favorites/notes/status IDs.
  - In `serve_ui_fixture.py` & `test_jobs_ui.mjs`: ensure an ATS-sourced `verified_job` with `discovery_source: 'OpenStreetMap discovery'` and `source: 'PageUp'` renders ODbL attribution cleanly, ensure `last_verified_at` tests both a fresh verified job and a >14-day stale verified job with `lat`/`lng` on OSM items, and assert in `test_jobs_ui.mjs` that `#jobsDetailDrawer` renders the freshness date / stale notice + ODbL link cleanly and keeps `errors == []` on desktop & mobile.

- [ ] **Step 2: Run API & UI tests to verify failure**

Run: `Push-Location sydney-hub; python -m pytest tests/test_api.py -v`
Expected: FAIL (`offset` / `x-total-count` missing).

- [ ] **Step 3: Implement `offset` + `X-Total-Count` in `catalog.py`/`app.py` and freshness + pagination in `web/template.html`**
  - Preserve existing card layout, CSS design tokens, `localStorage` keys, and ODbL link cleanly (`build_dashboard(Path('web'))` checked during tests).

- [ ] **Step 4: Run full Python + Playwright UI test suite**

Run:
```powershell
Push-Location sydney-hub
python -m pytest -ra
$p = Start-Process python -ArgumentList "tests/serve_ui_fixture.py" -PassThru -NoNewWindow
Start-Sleep -Seconds 2
try { node tests/test_jobs_ui.mjs } finally { Stop-Process -Id $p.Id -Force; Pop-Location }
```
Expected: ALL pytest items pass + `Jobs UI: persisted API, evidence labels, saved state, mobile, and API failure passed.`

- [ ] **Step 5: Commit Task 7**

```bash
git add sydney-hub/hub/storage/catalog.py sydney-hub/hub/api/app.py sydney-hub/web/template.html sydney-hub/tests/serve_ui_fixture.py sydney-hub/tests/test_api.py sydney-hub/tests/test_jobs_ui.mjs
git commit -m "feat: add opportunity pagination header and visible freshness badge in Jobs drawer"
```

---

### Task 8: Sanitized Worktree Check, Pre-Pilot v4 Snapshot, Live 60-Employer Pilot & 100% Manual Audit

**Files:**
- Create: `docs/superpowers/reviews/2026-11-14-track1-ats-pilot-results.md` (manual audit & pilot measurement report)

**Interfaces:**
- Executes read-only/dry-run and bounded `--live` CLI against `sydney-hub/data/hub.db` cleanly.

- [ ] **Step 1: Verify Git hygiene & safety before touching live `data/hub.db`**
  - Verify `git diff --check`, verify `sydney-hub/data/*` and `config/settings.yml` remain untracked/ignored, verify zero credentials introduced in Track 1 commits, and verify `python -m pytest -ra` + `test_jobs_ui.mjs` pass cleanly.
  - Test `v3 -> v4` backup (`--target-version 3 --force`) AND `v4` pilot snapshot rollback (`--target-version 4 --force`) on a temporary copy of `data/hub.db` first to prove rollback preserves the `147`-row baseline before `--live`.

- [ ] **Step 2: Run `--dry-run` Track 1 pilot first and inspect queue selection**

Run: `Push-Location sydney-hub; python run_employer_pilot.py --priority hospitality --max-employers 60`
Expected: `0` unhandled errors; verify priority queue selected `<= 60` hospitality/cleaning employers (`retail_preserved_skipped > 0`, `failed_history_skipped >= 40`).

- [ ] **Step 3: Execute authorized `--live` Track 1 pilot (`<= 60` employers)**

Run: `Push-Location sydney-hub; python run_employer_pilot.py --live --priority hospitality --max-employers 60`
Expected: upgrades `hub.db` cleanly to `v4` with verified `v3` backup + verified pre-Track-1 `v4` snapshot of the 147-row DB, writes updated catalog & JSON telemetry under `sydney-hub/data/pilot-reports/`.

- [ ] **Step 4: Perform 100% manual audit of every `verified_job` & run repeat idempotency check**
  - Query `sydney-hub/data/hub.db` (`?mode=ro`): verify `PRAGMA integrity_check == 'ok'`, verify `retail store` count unchanged (`78`), and open/inspect **100%** of `verified_job` `source_url` links manually (or via fetched evidence + HTTP check) confirming: authentic open role, valid application URL, and explicit location inside `Rockdale`, `Kogarah`, `Hurstville`, or `Mascot`.
  - Check against the validation target (`>= 5` real `verified_job` records): if `< 5`, document exact counts, detected ATS vendors (including unsupported `dayforce`/`employmenthero`/JS-SPA blockers), and remaining limitations honestly without marking the functional goal complete.
  - Write findings to `docs/superpowers/reviews/2026-11-14-track1-ats-pilot-results.md` and run final Playwright smoke check against a copy of the updated `data/hub.db`.

- [ ] **Step 5: Commit audit report (never commit `data/hub.db`, `.osm.pbf`, or `pilot-reports/*.json`; do NOT push or merge automatically)**

```bash
git add docs/superpowers/reviews/2026-11-14-track1-ats-pilot-results.md
git commit -m "docs: record Track 1 60-employer ATS enrichment pilot metrics and manual audit"
```
