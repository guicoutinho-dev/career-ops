# Australia Job Hunter — Data Model Specification

## 1. State Ownership
SQLite (`data/applications.db`) is authoritative for the Australia Job Hunter extension's structured lifecycle records (jobs, evaluations, documents, reviews, approvals, tasks, and events), with ACID guarantees, foreign keys, and WAL mode.

Career-ops core files retain their own contract: `data/applications.md` remains the canonical tracker and `data/pipeline.md` remains the URL inbox. Imports from those files seed/ingest extension records; they are not a bi-directional synchronization channel. Re-importing must never overwrite an existing SQLite application status or score, and the extension does not write back to the Career-ops files.

## 2. Table Specifications

### `jobs`
Stores raw and normalized job posting metadata.
- `id`: TEXT PRIMARY KEY (e.g., `job_uuid` or `sha256(canonical_url)`)
- `source`: TEXT NOT NULL (e.g., `seek`, `jobspy_linkedin`, `jobspy_indeed`, `greenhouse`)
- `external_id`: TEXT
- `canonical_url`: TEXT UNIQUE NOT NULL
- `employer`: TEXT NOT NULL
- `title`: TEXT NOT NULL
- `location`: TEXT NOT NULL
- `description`: TEXT NOT NULL
- `publication_date`: TEXT
- `discovered_at`: TEXT NOT NULL
- `status`: TEXT NOT NULL DEFAULT 'DISCOVERED'
- `content_hash`: TEXT NOT NULL
- `last_verified_at`: TEXT

### `applications`
Manages the application lifecycle.
- `id`: TEXT PRIMARY KEY (e.g., `app_001`, `app_uuid`)
- `job_id`: TEXT NOT NULL REFERENCES jobs(id)
- `profile_id`: TEXT NOT NULL
- `role_track`: TEXT NOT NULL CHECK (role_track IN ('sap', 'operational'))
- `match_score`: REAL
- `confidence`: REAL
- `status`: TEXT NOT NULL
- `created_at`: TEXT NOT NULL
- `updated_at`: TEXT NOT NULL
- `submitted_at`: TEXT
- `next_action_at`: TEXT
- `assigned_owner`: TEXT

### `documents`
Tracks generated and tailored CVs and cover letters.
- `id`: TEXT PRIMARY KEY
- `application_id`: TEXT NOT NULL REFERENCES applications(id)
- `document_type`: TEXT NOT NULL CHECK (document_type IN ('cv_pdf', 'cv_html', 'cover_letter_pdf', 'cover_letter_txt'))
- `path`: TEXT NOT NULL
- `version`: INTEGER NOT NULL DEFAULT 1
- `content_hash`: TEXT NOT NULL
- `review_status`: TEXT NOT NULL DEFAULT 'PENDING'
- `source_profile_version`: TEXT NOT NULL
- `created_at`: TEXT NOT NULL

### `reviews`
Records independent verification results.
- `id`: TEXT PRIMARY KEY
- `application_id`: TEXT NOT NULL REFERENCES applications(id)
- `document_id`: TEXT REFERENCES documents(id)
- `reviewer`: TEXT NOT NULL (e.g., `agent:Revisor-Gy`, `human:operator`)
- `decision`: TEXT NOT NULL CHECK (decision IN ('PASS', 'FAIL', 'NEEDS_CHANGES'))
- `findings_json`: TEXT NOT NULL
- `reviewed_at`: TEXT NOT NULL

### `approvals`
Cryptographic/Hashed manual approval records.
- `id`: TEXT PRIMARY KEY
- `application_id`: TEXT NOT NULL REFERENCES applications(id)
- `package_hash`: TEXT NOT NULL
- `decision`: TEXT NOT NULL CHECK (decision IN ('APPROVED', 'REJECTED', 'PENDING'))
- `approved_by`: TEXT
- `approved_at`: TEXT
- `invalidated_at`: TEXT

### `email_messages`
Stores Gmail incoming response metadata and classifications.
- `id`: TEXT PRIMARY KEY
- `provider`: TEXT NOT NULL DEFAULT 'gmail'
- `provider_message_id`: TEXT UNIQUE NOT NULL
- `thread_id`: TEXT
- `received_at`: TEXT NOT NULL
- `sender`: TEXT NOT NULL
- `subject`: TEXT NOT NULL
- `classification`: TEXT NOT NULL
- `confidence`: REAL NOT NULL
- `matched_application_id`: TEXT REFERENCES applications(id)
- `evidence_json`: TEXT

### `events`
Immutable audit log of all system transitions and actions.
- `id`: INTEGER PRIMARY KEY AUTOINCREMENT
- `entity_type`: TEXT NOT NULL
- `entity_id`: TEXT NOT NULL
- `event_type`: TEXT NOT NULL
- `actor`: TEXT NOT NULL
- `timestamp`: TEXT NOT NULL
- `correlation_id`: TEXT NOT NULL
- `payload_json`: TEXT

### `tasks`
Task queue for durable execution and background scheduling.
- `id`: TEXT PRIMARY KEY
- `task_type`: TEXT NOT NULL
- `entity_id`: TEXT NOT NULL
- `status`: TEXT NOT NULL CHECK (status IN ('PENDING', 'RUNNING', 'COMPLETED', 'FAILED', 'RETRYING'))
- `attempts`: INTEGER NOT NULL DEFAULT 0
- `idempotency_key`: TEXT UNIQUE NOT NULL
- `scheduled_at`: TEXT NOT NULL
- `started_at`: TEXT
- `completed_at`: TEXT
- `error_code`: TEXT

## 3. Application Lifecycle States
- `DISCOVERED`
- `DEDUPLICATED`
- `QUALIFIED` / `DISQUALIFIED`
- `DOCUMENTS_DRAFTED`
- `REVIEW_PENDING`
- `REVIEW_PASSED` / `REVIEW_FAILED`
- `PENDING_APPROVAL`
- `APPROVED`
- `READY_TO_SUBMIT`
- `APPLIED`
- `ACKNOWLEDGED`
- `SCREENING`
- `INTERVIEW`
- `ASSESSMENT`
- `OFFER`
- `CLOSED_WON` / `CLOSED_LOST`
- Supplementary states: `NEEDS_INFORMATION`, `FOLLOW_UP_DUE`, `EXPIRED`, `WITHDRAWN`, `BLOCKED`.
