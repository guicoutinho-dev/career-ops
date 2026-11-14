# Employer pilot source-rights decision — 2026-11-13

## Decision

Use the OpenStreetMap (OSM) `website` / `contact:website` tags through a public Overpass API for a **small, local, one-off pilot**. The adapter uses only the website URL and OSM element ID as a discovery pointer. Employer names, jobs, dates, and terms must come from an independently checked employer website. OSM provides no evidence of a vacancy. Do not use gosom or persist Google Places business content for this pilot.

## Permission, retention, attribution

- [OSM copyright and ODbL](https://www.openstreetmap.org/copyright): OSM data may be copied and adapted with attribution; public distribution of an adapted database can trigger share-alike duties. Display “Business discovery data © OpenStreetMap contributors · ODbL” with a link to that page in Jobs. A public product launch needs a separate licence review of the combined database/export obligations.
- [OSM `website` key](https://wiki.openstreetmap.org/wiki/Key:website) denotes a feature's official website, but an individual tag can be wrong or stale. The website check and manual sample decide whether the site belongs to the employer; the OSM tag alone cannot verify the company or hiring.
- [Overpass public instance guidance](https://wiki.openstreetmap.org/wiki/OSM_Server_Side_Script#Public_Overpass_API_instances): the FOSSGIS public endpoint timed out in a pilot query. The listed `overpass.private.coffee` instance permits projects without a fixed rate limit and asks for advance notice at large scale. This local one-off pilot caps itself at 24 sequential suburb/category requests, 300 KB per response (under 7.2 MB total), and 10 site candidates per category; it identifies itself with a User-Agent. No scheduled collection is enabled.
- Persist no OSM business name, address, coordinates, reviews, or map geometry. Persist source label, independently checked website facts, and original employer-site URLs. Local pilot reports remain ignored under `sydney-hub/data/pilot-reports/`.

## Cost and limits

The public OSM/Overpass endpoint has no API fee for this bounded pilot. The report records `provider_cost_estimate=0` for the API fee and request/elapsed-time counts; website hosting, operator time, and future production hosting are not included. The public endpoint has no availability guarantee and may throttle or reject queries; those outcomes remain collector failures, never evidence that an employer or job closed.

Google Places remains a later option only after a permission/retention decision specific to employer lead discovery. Its [current policy](https://developers.google.com/maps/documentation/places/web-service/policies) restricts storing/indexing Places content, with a stated Place ID exception. This decision does not authorize Maps scraping.
