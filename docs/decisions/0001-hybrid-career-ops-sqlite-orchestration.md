# ADR 0001: Hybrid Career-ops and SQLite Authoritative Orchestration

## Status
Accepted

## Context
The Australia Job Hunter extension requires an auditable, concurrent, transactional state store capable of tracking job discoveries, evaluations, document versions, approvals, and communication events.
The Career-ops core remains file-authoritative: `data/applications.md` is the tracker source of truth, and `data/pipeline.md` is its job inbox. The extension's SQLite database is authoritative only for its richer application lifecycle, review, approval, task, and event records.

## Decision
1. Use **SQLite (`data/applications.db`)** as the authoritative state store for the Australia Job Hunter extension's richer lifecycle records.
2. Treat imports from `data/applications.md` and `data/pipeline.md` as one-way bootstrap/ingestion only. Existing SQLite application records must not be overwritten by tracker imports; no automatic write-back to Career-ops files is performed.
3. Keep Career-ops' existing Markdown source-of-truth contract unchanged. Any future export from SQLite to those files requires a separate explicit design and conflict policy.
4. Every write to SQLite produces an immutable audit event in the `events` table with actor, timestamp, correlation ID, and state transition details.

## Consequences
- **Positive:** Full ACID transactions, schema migrations, foreign key integrity, fast indexing, structured JSON payloads, and robust crash recovery.
- **Negative:** Requires migration management; imported tracker/pipeline snapshots can become stale and must not be mistaken for a bi-directional sync.
