# SydneyStays discovery engine — remaining scope

## Baseline and goal

The approved Delivery 1 is commit `2b380e1` on `codex/sydneystays-catalog`. FastAPI, SQLite, APScheduler, a persisted opportunity/evidence catalog, API filters, and the existing Jobs interface work together. Its historical catalog has no verified job; legacy records and Facebook posts do not gain that label automatically. Keep this commit as the reproducible baseline and do not update Career Ops to v1.40.0 during stabilization.

The final engine discovers recent, relevant work for backpackers through independent published-job, employer-website, and community channels. It preserves original evidence and unknowns, supports candidate matching and application preparation, and exposes collector health and cost. The Jobs design, localhost boundary, saved browser state, and existing stack remain.

## Delivery order and boundaries

1. **Employer discovery pilot (next):** Rockdale, Kogarah, Hurstville, Mascot; category → authorized business source → official website → specific career page/listing → evidence class. A discovered business alone is an `employer_lead`. A hiring mention without a confirmed live listing is `unverified_job_post`. A specific reachable listing with job details and application action is `verified_job` only when checked evidence is recorded. A timeout is `unknown`, never closure. Measure precision, duplicates, valid links, freshness, and cost by suburb.
2. **Published jobs:** add an authorized official feed first, then only sources whose permission and attribution have been checked. Integrate each through a small adapter and the existing catalog. JobSpy remains blocked until board-specific permission review; installed code is not evidence of authorized collection. Adzuna offers an official search API, but ongoing commercial use requires a licence; its salary predictions must stay distinct from advertised pay. Backpacker Job Board and hospitality sites remain feasibility research until a documented API or permission is found.
3. **Community jobs:** keep the existing Facebook collector independent. Extract title, place, pay, contact, date, and terms only from the original post with excerpt provenance. Do not infer an employer from an author or a certification from a role name. Unstructured/ambiguous posts remain unverified.
4. **Matching and 88-day mode:** deterministic role aliases, explicit/unknown WHV 462 acceptance, entry-level and experience, RSA/RCG, casual/part-time/temporary/seasonal, disclosed pay, freshness, measured transit, candidate skills/English, and suspicious-condition signals. LLM use only for unresolved cases, with source-grounded output. The 88-day view is separate; classify specified work using versioned official occupation, region/postcode, and date rules, and show `needs_review` when any necessary fact is missing.
5. **Career intelligence and release:** evaluate and prepare applications from the source-backed catalog without auto-submission; preserve favorites, notes, saved searches, and application statuses across deduplication. Add incremental refresh, conservative expiry, per-source health, retries/rate limits, cost ceilings, rollback rehearsal, and desktop/mobile end-to-end checks.

Each delivery must produce independently testable software. Later delivery plans should use findings from the earlier pilot rather than assume coverage or source access.

## Trust and operation contracts

- `verified_job` requires a specific `job_listing` evidence row with `supports_job=1` and a checked timestamp. Generic careers pages are leads. Failed requests cannot upgrade or expire a record.
- Store only facts supported by the original source. Missing salary, visa eligibility, requirements, location, and hiring status remain null/unknown. Estimated salary is never presented as advertised salary.
- Keep source adapters isolated: one failed collector neither changes another source's state nor clears its records. `source_url` and all evidence URLs remain available in the API and UI.
- Use SQLite's verified backup and rollback mechanism for every schema change; preserve existing opportunity IDs where an exact URL matches. Do not overwrite local ignored files or commit credentials/personally identifying data.
- Use the existing FastAPI, SQLite, APScheduler, Python tests, and Playwright browser test. Do not replace the Impeccable Jobs design or add a framework for these deliveries.
- Any external collection needs a terms, attribution, data-retention, and cost review before live use. Google Places policies restrict storage/indexing of Places content; a Place ID has a stated caching exception. Official employer-site facts should be acquired and evidenced separately. Adzuna's API terms require a licence for ongoing revenue-generating use. See [Google Places policy](https://developers.google.com/maps/documentation/places/web-service/policies), [Adzuna API terms](https://developer.adzuna.com/docs/terms_of_service), and [Adzuna search API](https://developer.adzuna.com/docs/search).
- Specified subclass 462 work is governed by industry, eligible area and duration rules, not just a job title or suburb. Recheck the [Home Affairs specified-work guidance](https://immi.homeaffairs.gov.au/visas/getting-a-visa/visa-listing/work-holiday-462/specified-462-work) before implementing the 88-day view.

## Final acceptance

- At least one authorized live source in each enabled channel yields source-backed records in Jobs; a disabled or blocked channel is visibly reported, not silently treated as empty.
- The four-suburb pilot reports counts by verification state, evidence precision from manual audit, duplicate rate, link validity, age since publication/check, requests and cost per verified listing. No lead is presented as an open job.
- Repeated runs are idempotent, preserve IDs and user state, and do not turn network errors into expired jobs. Verified listings have a documented recheck and expiry policy; stale evidence is visible.
- Matching returns explainable reasons and unknowns. The separate 88-day view never guarantees visa credit from insufficient facts.
- Unit, storage/API integration, real-database migration/rollback, and desktop/mobile browser tests pass against fixtures; a live pilot is reviewed manually before broadening collection.
