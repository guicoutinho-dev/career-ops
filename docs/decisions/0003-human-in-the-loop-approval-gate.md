# ADR 0003: Mandatory Cryptographic/Hashed Human-in-the-Loop Approval Gate

## Status
Accepted

## Context
Autonomous submission of job applications or external emails carries legal, professional, and ethical risks—especially concerning visa declarations, work authorizations, and candidate claims. Agents must never possess the autonomous authority to submit an application or send an email without verified human intervention.

## Decision
1. Introduce a strict **Manual Approval Gate** for all application submissions and external communications.
2. Before any application can transition to `APPROVED` or `READY_TO_SUBMIT`, an immutable review package is assembled containing:
   - Application ID, Job ID, and Employer details.
   - Exact job description hash.
   - Generated CV and Cover Letter hashes.
   - Proposed question responses.
   - Explicit declaration of visa/WHV assumptions and salary expectations.
3. The package hash (`package_hash = SHA256(...)`) is recorded in the `approvals` table.
4. Approval requires deliberate operator action via CLI or verified local API. Any change to the job description, CV, cover letter, or answers immediately invalidates existing approvals.
5. Unattended workers are physically and logically blocked from calling external submission endpoints.

## Consequences
- **Positive:** Absolute protection against hallucinated claims, unauthorized legal attestations, or accidental submissions.
- **Negative:** Requires active operator involvement before submission proceeds.
