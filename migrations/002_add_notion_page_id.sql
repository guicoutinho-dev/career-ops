-- Migration: 002_add_notion_page_id.sql
-- Description: Add notion_page_id to applications table for idempotent Notion panel sync

ALTER TABLE applications ADD COLUMN notion_page_id TEXT;
CREATE INDEX IF NOT EXISTS idx_applications_notion_page_id ON applications(notion_page_id);
