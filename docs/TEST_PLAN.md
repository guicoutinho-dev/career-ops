# Australia Job Hunter — Test Plan

## 1. Unit Testing
- `tests/unit/test_normalization.py`: Validates URL cleaning, Australian location standardization, and content hashing.
- `tests/unit/test_deduplication.py`: Tests exact and near-match deduplication against existing database records.
- `tests/unit/test_scoring.py`: Verifies weight math, hard blocker triggers (e.g. citizenship requirements), and confidence calculation.
- `tests/unit/test_approval_invalidation.py`: Ensures that modifying a document hash invalidates existing approvals.

## 2. Integration Testing
- `tests/integration/test_sqlite_migrations.py`: Applies versioned migrations, verifies schema integrity, rollback, and concurrency locks.
- `tests/integration/test_career_ops_sync.py`: Validates two-way synchronization between `data/applications.md` and `data/applications.db`.
- `tests/integration/test_jobspy_adapter.py`: Tests JobSpy adapter with mocked provider payloads.

## 3. Security Testing
- `tests/security/test_prompt_injection.py`: Injects adversarial prompts into job description fields and asserts that safety rules are maintained.
- `tests/security/test_submission_blocker.py`: Verifies that unapproved applications cannot trigger submission logic.
