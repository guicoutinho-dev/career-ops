import unittest
import tempfile
import sqlite3
from unittest.mock import patch
from pathlib import Path
from src.database.migrations import apply_migrations
from src.integrations.career_ops.sync import sync_applications_md_to_sqlite
from src.database.connection import get_connection

class TestCareerOpsSync(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "sync_test.db"
        apply_migrations(self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_sync_imports_tracker_fixture(self):
        tracker = Path(self.temp_dir.name) / "applications.md"
        tracker.write_text(
            "# Applications Tracker\n\n"
            "| # | Date | Company | Role | Score | Status | PDF | Report | Notes |\n"
            "|---|---|---|---|---|---|---|---|---|\n"
            "| 003 | 2026-10-27 | Example Co | Engineer | 4.7 | Applied | ⏳ | "
            "[003](reports/003-example.md) | url https://example.test/003 |\n"
            "| 004 | 2026-10-27 | Example Org | Analyst | 4.6 | Evaluated | ⏳ | "
            "[004](reports/004-example.md) | url https://example.test/004 |\n",
            encoding="utf-8",
        )
        synced_count = sync_applications_md_to_sqlite(self.db_path, tracker)
        self.assertEqual(synced_count, 2)

        conn = get_connection(self.db_path)
        cursor = conn.cursor()
        
        # Verify 003 and 004
        cursor.execute("SELECT id, status, match_score FROM applications WHERE id IN ('app_003', 'app_004');")
        rows = {row["id"]: row for row in cursor.fetchall()}
        conn.close()

        self.assertIn("app_003", rows)
        self.assertIn("app_004", rows)
        self.assertEqual(rows["app_003"]["status"], "Applied")
        self.assertEqual(rows["app_004"]["status"], "Evaluated")
        self.assertEqual(rows["app_003"]["match_score"], 4.7)
        self.assertEqual(rows["app_004"]["match_score"], 4.6)

    @patch("src.integrations.career_ops.sync.parse_applications_md")
    def test_legacy_import_does_not_overwrite_existing_sqlite_application(self, mock_parse):
        conn = get_connection(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            """INSERT INTO jobs
               (id, source, canonical_url, employer, title, location, description,
                discovered_at, content_hash)
               VALUES ('job_003', 'career_ops_legacy', 'urn:career-ops:job:003',
                       'Example Co', 'Engineer', 'Australia', 'Description',
                       '2026-10-27T00:00:00Z', 'hash_003')"""
        )
        cursor.execute(
            """INSERT INTO applications
               (id, job_id, profile_id, role_track, match_score, confidence, status,
                created_at, updated_at, assigned_owner)
               VALUES ('app_003', 'job_003', 'default_profile', 'sap', 4.9, 0.95,
                       'INTERVIEW', '2026-10-27T00:00:00Z', '2026-10-28T00:00:00Z',
                       'human:operator')"""
        )
        conn.commit()
        conn.close()

        mock_parse.return_value = [{
            "num": "003", "date": "2026-10-27", "company": "Example Co",
            "role": "Engineer", "score": "4.7", "status": "Applied",
            "pdf": "⏳", "report": "reports/003-example.md", "notes": "legacy row",
        }]
        sync_applications_md_to_sqlite(self.db_path)

        conn = get_connection(self.db_path)
        row = conn.execute(
            "SELECT status, match_score FROM applications WHERE id = 'app_003'"
        ).fetchone()
        conn.close()

        self.assertEqual(row["status"], "INTERVIEW")
        self.assertEqual(row["match_score"], 4.9)

if __name__ == "__main__":
    unittest.main()
