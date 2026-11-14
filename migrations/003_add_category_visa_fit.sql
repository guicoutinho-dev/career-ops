-- Migration: 003_add_category_visa_fit.sql
-- Description: Persist job category + visa-fit classification and Notion document sync state.

ALTER TABLE applications ADD COLUMN category TEXT;
ALTER TABLE applications ADD COLUMN visa_fit TEXT;
ALTER TABLE applications ADD COLUMN notion_docs_hash TEXT;

CREATE INDEX IF NOT EXISTS idx_applications_category ON applications(category);
