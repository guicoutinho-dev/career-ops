# SydneyStays Employer Discovery Pilot — measured result (2026-11-14 UTC)

## Geofabrik NSW `.osm.pbf` automated pilot run (latest)

After public Overpass endpoints timed out (`ReadTimeout` at 25s, see below), the pilot was re-implemented against the local Geofabrik New South Wales extract (`sydney-hub/data/osm/new-south-wales-latest.osm.pbf`, MD5 `b0292cae3417fe329f8829d337e15a6c`) using C++-filtered streaming via `osmium.FileProcessor` (`EmptyTagFilter`, `KeyFilter`, and `IdFilter` for ways).

Before writing, `sydney-hub/data/hub.db` was rolled back cleanly to the pre-v3 schema snapshot (`rollback_catalog.py --target-version 2 --force`), then migrated to catalog schema v3 (`category`, `lat`, `lng` nullable columns). During initial live testing, 58 HTTP checks returned `error`; root-cause inspection showed 18 of 20 sampled sites actually returned HTTP 200 HTML between 314 KB and 1.36 MB, exceeding the initial `256_000` byte cap in `hub/discovery/website.py`. After adding a regression test (`test_reads_modern_page_above_256kb_and_rejects_oversized_response`) and raising `_MAX_BYTES` to `2_000_000` (2 MB), the full four-suburb live pilot (`sydney-hub/data/pilot-reports/employer-pilot-20261114T043409Z.json`) measured:

| Suburb | Discovered businesses | Missing websites | Source URL duplicates | Unique websites checked | Reachable (`ok`) | Failed checks | Employer leads | Verified jobs |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Rockdale | 117 | 101 | 0 | 16 | 9 | 7 | 16 | 0 |
| Kogarah | 75 | 57 | 0 | 18 | 13 | 5 | 18 | 0 |
| Hurstville | 354 | 259 | 1 | 94 | 72 | 22 | 94 | 0 |
| Mascot | 315 | 296 | 1 | 18 | 12 | 6 | 18 | 0 |
| **Total** | **861** | **713** | **2** | **146** | **106** | **40** | **146** | **0** |

- **Requests / Errors / Elapsed:** `181` HTTP requests (`0` unhandled errors; `248.719` seconds total runtime, including ~4 seconds streaming the NSW `.osm.pbf`).
- **Failed website check breakdown:** `27` `blocked` (HTTP 401/403/429), `7` `error` (HTTP 4xx/5xx, DNS/connection failure, or >2 MB), `6` `timeout`.
- **SQLite state after run (`sydney-hub/data/hub.db`):** `PRAGMA integrity_check` = `ok`, `PRAGMA user_version` = `3`, `147` opportunities (`146` OSM PBF `employer_lead` rows with source `category`, `lat`, `lng`, and `suburb`, plus `1` pre-existing manual probe lead `Newmarket Hotel Mascot`), `106` `employer_website` evidence rows with `checked_at` timestamps and `41` unchecked/failed lead evidence rows (`supports_job = 0`), `0` `verified_job`, `0` `unverified_job_post`.
- **Provider API fee:** `$0.00` (offline Geofabrik `.osm.pbf`); `cost_per_verified_job` is `null` (`0` verified jobs).

### Remaining limitations observed

1. **Sparse OSM website coverage:** `713 / 861` (`82.8%`) of matching businesses in OSM lack a `website` or `contact:website` tag.
2. **Social links in OSM tags:** `18 / 146` (`12.3%`) of discovered URLs point to Instagram (`13`) or Facebook (`5`) rather than an employer careers page.
3. **Bot/WAF blocking & timeouts:** `40 / 146` (`27.4%`) could not be read (`27` blocked, `7` error, `6` timeout); these remain conservative `employer_lead` entries with `checked_at = null`.
4. **Zero verified postings on static employer homepages:** Small local venues rarely expose static HTML `<h1>` role listings with same-origin `<a href>` apply links (many recruit via third-party ATS subdomains/portals, JS SPAs, or social/walk-in channels).
5. **Cross-location brand chains:** Different store-locator URLs sharing a brand domain (`www.woolworths.com.au`, `www.ikea.com`) normalize to distinct path URLs (`146` unique URLs, `2` exact-URL duplicates removed).

---

## Earlier Overpass API failure (retained for audit trail)

The earlier bounded Overpass CLI run (`sydney-hub/data/pilot-reports/employer-pilot-20261114T030812Z.json`) attempted Rockdale, Kogarah, Hurstville, and Mascot via `https://overpass.private.coffee/api/interpreter`. All 4 suburbs returned `ReadTimeout` after 25s (`101.75` seconds total, `0` records). That failure motivated the Geofabrik `.osm.pbf` local streaming adapter above.
