# Australia Job Hunter — Risk Register

| Risk ID | Description | Impact | Likelihood | Mitigation Strategy |
|---|---|---|---|---|
| **RSK-01** | Submitting unverified claims or hallucinated Australian work rights | Critical | Low | Hard fact-checking against `cv.md` with `verify-cv-facts.mjs` + mandatory human approval gate. |
| **RSK-02** | WHV 6-month work limitation violation | High | Medium | Explicitly tag roles with duration > 6 months and surface visa planning assumptions during evaluation. |
| **RSK-03** | Portal IP blocking / rate limiting by Seek or LinkedIn | Medium | Medium | Respect rate limits, random jitter, caching, zero-token HTTP scanning, and no bot-evasion hacks. |
| **RSK-04** | Prompt injection via untrusted job descriptions | High | Low | Treat job descriptions as untrusted data; sanitize and enforce structured schemas. |
| **RSK-05** | Accidental data leakage of candidate details | High | Low | Local storage only; exclude databases, credentials, and output drafts from Git. |
| **RSK-06** | Stale approvals submitted after documents modified | Critical | Low | Cryptographic hash binding (`package_hash`) immediately invalidates stale approvals upon changes. |
