# SydneyStays Employer Discovery Pilot — measured result (2026-11-14 UTC)

## Automated OSM run

The bounded live CLI attempted Rockdale, Kogarah, Hurstville, and Mascot in that order. The first Overpass query for each suburb returned `ReadTimeout` after 25 seconds. The runner stopped further categories within each failed suburb and continued to the next. The ignored local report is `sydney-hub/data/pilot-reports/employer-pilot-20261114T030812Z.json`; a verified pre-write SQLite snapshot and manifest were created beside the isolated worktree database.

| Metric | Result |
| --- | ---: |
| Provider requests attempted | 4 |
| Provider failures | 4 `ReadTimeout` |
| Employer leads / unverified posts / verified jobs | 0 / 0 / 0 |
| New records / duplicate URLs | 0 / 0 |
| Elapsed | 101.75 seconds |
| Provider API fee / cost per verified job | Unknown for this failed run / undefined |

No source response was received, so coverage, source link quality, duplication rate, and cost per verified job cannot be inferred from this run. A timeout was not treated as an expired vacancy.

## Separate official-site probe

To test real website access without relying on Overpass, four employer-controlled domains found through public search were inspected once each. This is a **manual seed probe**, not an OSM discovery result. The ignored local report is `sydney-hub/data/pilot-reports/manual-official-sites.json`.

| Suburb | Official website checked | Check outcome | Persisted classification |
| --- | --- | --- | --- |
| Rockdale | [Hotel Rockdale](https://www.hotelrockdale.com.au/) | blocked | none |
| Kogarah | [Kogarah Clubhouse](https://www.kogarahclubhouse.com.au/) | blocked | none |
| Hurstville | [Niji Sushi Bar Hurstville](https://hurstville.niji.com.au/) | homepage read, secondary page blocked | none |
| Mascot | [Newmarket Hotel Mascot](https://newmarkethotel.com.au/) | homepage read | `employer_lead` |

The probe made five website requests. Its first run found a possible Hurstville lead on a partially checked site, persisted only the fully checked Mascot lead, and found zero specific verified job listings. After the independent review fix, the safe-client repeat recorded **one fully checked lead, one existing-URL duplicate, zero new records, and three unknown link checks**; see the ignored `manual-official-sites-safe.json` report. No link was declared broken. The persisted Mascot record has stable ID `site_6b3d9acbf54a591745e5` and original website URL. The `source` is `Manual official-site seed`, so it cannot be mistaken for an OSM or confirmed-job result. Provider cost for manual search was not priced; cost per verified job is undefined.

## Decision

The website inspection, catalog persistence, rollback, API, and Jobs presentation paths are implemented and verified with fixtures. The live OSM→employer discovery pilot **has not met its completion gate** because the source returned no data. Do not expand collection or claim local market coverage. Re-run the same four-suburb pilot when a licensed, reachable business source is available, then manually review every verified listing before enabling recurring collection.
