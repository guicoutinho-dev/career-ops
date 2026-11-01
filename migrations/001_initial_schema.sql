-- Migration: 001_initial_schema.sql
-- Description: Authoritative SQLite schema for Australia Job Hunter

PRAGMA foreign_keys = ON;

-- 1. Jobs Table
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    external_id TEXT,
    canonical_url TEXT UNIQUE NOT NULL,
    employer TEXT NOT NULL,
    title TEXT NOT NULL,
    location TEXT NOT NULL,
    description TEXT NOT NULL,
    publication_date TEXT,
    discovered_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'DISCOVERED',
    content_hash TEXT NOT NULL,
    last_verified_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_jobs_canonical_url ON jobs(canonical_url);
CREATE INDEX IF NOT EXISTS idx_jobs_employer ON jobs(employer);
CREATE INDEX IF NOT EXISTS idx_jobs_discovered_at ON jobs(discovered_at);
CREATE INDEX IF NOT EXISTS idx_jobs_content_hash ON jobs(content_hash);

-- 2. Applications Table
CREATE TABLE IF NOT EXISTS applications (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE RESTRICT,
    profile_id TEXT NOT NULL,
    role_track TEXT NOT NULL CHECK (role_track IN ('sap', 'operational')),
    match_score REAL,
    confidence REAL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    submitted_at TEXT,
    next_action_at TEXT,
    assigned_owner TEXT
);

CREATE INDEX IF NOT EXISTS idx_applications_job_id ON applications(job_id);
CREATE INDEX IF NOT EXISTS idx_applications_status ON applications(status);
CREATE INDEX IF NOT EXISTS idx_applications_role_track ON applications(role_track);

-- 3. Documents Table
CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    application_id TEXT NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
    document_type TEXT NOT NULL CHECK (document_type IN ('cv_pdf', 'cv_html', 'cover_letter_pdf', 'cover_letter_txt')),
    path TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    content_hash TEXT NOT NULL,
    review_status TEXT NOT NULL DEFAULT 'PENDING',
    source_profile_version TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_documents_app_id ON documents(application_id);

-- 4. Reviews Table
CREATE TABLE IF NOT EXISTS reviews (
    id TEXT PRIMARY KEY,
    application_id TEXT NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
    document_id TEXT REFERENCES documents(id) ON DELETE SET NULL,
    reviewer TEXT NOT NULL,
    decision TEXT NOT NULL CHECK (decision IN ('PASS', 'FAIL', 'NEEDS_CHANGES')),
    findings_json TEXT NOT NULL,
    reviewed_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_reviews_app_id ON reviews(application_id);

-- 5. Approvals Table
CREATE TABLE IF NOT EXISTS approvals (
    id TEXT PRIMARY KEY,
    application_id TEXT NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
    package_hash TEXT NOT NULL,
    decision TEXT NOT NULL CHECK (decision IN ('APPROVED', 'REJECTED', 'PENDING')),
    approved_by TEXT,
    approved_at TEXT,
    invalidated_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_approvals_app_id ON approvals(application_id);
CREATE INDEX IF NOT EXISTS idx_approvals_package_hash ON approvals(package_hash);

-- 6. Email Messages Table
CREATE TABLE IF NOT EXISTS email_messages (
    id TEXT PRIMARY KEY,
    provider TEXT NOT NULL DEFAULT 'gmail',
    provider_message_id TEXT UNIQUE NOT NULL,
    thread_id TEXT,
    received_at TEXT NOT NULL,
    sender TEXT NOT NULL,
    subject TEXT NOT NULL,
    classification TEXT NOT NULL,
    confidence REAL NOT NULL,
    matched_application_id TEXT REFERENCES applications(id) ON DELETE SET NULL,
    evidence_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_emails_matched_app ON email_messages(matched_application_id);

-- 7. Audit Events Table
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    actor TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    correlation_id TEXT NOT NULL,
    payload_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_events_entity ON events(entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp);

-- 8. Durable Tasks Table
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    task_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('PENDING', 'RUNNING', 'COMPLETED', 'FAILED', 'RETRYING')),
    attempts INTEGER NOT NULL DEFAULT 0,
    idempotency_key TEXT UNIQUE NOT NULL,
    scheduled_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    error_code TEXT
);

CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_idempotency ON tasks(idempotency_key);

-- 9. Schema Version Tracking
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    migration_name TEXT NOT NULL,
    applied_at TEXT NOT NULL
);
