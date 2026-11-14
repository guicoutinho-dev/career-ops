# ADR 0002: JobSpy Adapter for Australian Multi-Source Discovery

## Status
Accepted

## Context
Australia's primary job portals include SEEK, LinkedIn, Indeed, and specialized ATS aggregators.
Career-ops includes a zero-token HTTP scanner for ATS and a native provider for SEEK. To broaden discovery without maintaining fragile custom scrapers, JobSpy provides a Python library supporting LinkedIn, Indeed, Glassdoor, and ZipRecruiter with geographical filtering.

## Decision
1. Implement a specialized Python adapter (`src/integrations/jobspy/adapter.py`) wrapping the JobSpy library with explicit Australian targeting (`country_which_at_job="australia"`).
2. Treat JobSpy strictly as an untrusted, rate-limited discovery source.
3. Enforce strict anti-abuse rules: no CAPTCHA bypassing, no bot evasion hacks, respect robots/rate-limits, and gracefully handle transient blocks.
4. Normalize all raw JobSpy results into canonical `JobCandidate` records before deduplication and ingestion into SQLite.

## Consequences
- **Positive:** Broadens search coverage across secondary channels while preserving native SEEK HTTP efficiency.
- **Negative:** Provider-specific rate limits and layout shifts require defensive error handling and fallbacks.
