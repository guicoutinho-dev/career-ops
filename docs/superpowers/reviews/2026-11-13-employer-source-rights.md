# Employer pilot source-rights decision — 2026-11-13 (updated 2026-11-14 for Geofabrik `.osm.pbf`)

## Decision

After public Overpass API endpoints timed out (`ReadTimeout` at 25s) or returned HTTP 502 before delivering response data, switch the local pilot discovery source from public Overpass HTTP queries to the local **Geofabrik New South Wales `.osm.pbf` extract** (`new-south-wales-latest.osm.pbf`). The extract is streamed locally with `osmium.FileProcessor` and C++ tag/ID filters restricted to the four pilot suburbs (`Rockdale`, `Kogarah`, `Hurstville`, `Mascot`) without loading the full NSW dataset into memory.

The adapter extracts business names (`name`), categories, website URLs (`website` / `contact:website`), and coordinates (`lat`, `lng`) **only when present in the OSM source**. Actual job postings, roles, and hiring statuses must come strictly from inspecting official employer websites (`inspect_website`). A discovered business alone (or one whose website cannot be read/confirmed) remains `employer_lead`; it never becomes `verified_job` without checked `job_listing` evidence (`supports_job=1`). Do not use `gosom` or persist Google Places business content for this pilot.

## Permission, retention, attribution

- [OSM copyright and ODbL](https://www.openstreetmap.org/copyright): OSM data may be copied and adapted with attribution; public distribution of an adapted database can trigger share-alike duties. Display “Business discovery data © OpenStreetMap contributors · ODbL” with a link to that page in Jobs (rendered automatically whenever `source == 'OpenStreetMap discovery'`). A public product launch requires a separate licence review of the combined database/export obligations.
- [OSM `website` key](https://wiki.openstreetmap.org/wiki/Key:website): denotes a feature's official website, though individual tags can be missing, social-only, or stale. Website inspection and manual review confirm whether an official website publishes a real vacancy.
- Large datasets (`sydney-hub/data/osm/*.osm.pbf`), local Python vendor wheels (`sydney-hub/data/vendor/`), `data/hub.db`, and pilot JSON reports (`sydney-hub/data/pilot-reports/`) remain git-ignored under `sydney-hub/data/`. No recurring scheduled collection is enabled.

## Cost and limits

- Reading the local Geofabrik `.osm.pbf` extract requires zero runtime API calls (`requests=0`) and has zero API fee (`provider_cost_estimate=0.0`).
- Website inspection checks official URLs sequentially using the SSRF-guarded `safe_website_client()` (blocking non-public IPs, private DNS answers, and cross-site redirects, capped at 4 pages, 3 redirects, 2 MB per page, and 8s timeout per request). Website timeouts, 403/429 blocks, and HTTP errors are recorded as unknown/failed checks, never as expired or closed jobs.
- Google Places remains a later option only after a permission/retention decision specific to employer lead discovery.
