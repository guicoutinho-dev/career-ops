import unittest
import tempfile
import sqlite3
from pathlib import Path
from src.database.connection import get_connection
from src.database.migrations import apply_migrations

class TestSqliteMigrations(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_applications.db"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_apply_migrations_fresh_db(self):
        applied = apply_migrations(self.db_path)
        self.assertIn("001_initial_schema.sql", applied)

        # Check table creation
        conn = get_connection(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;")
        tables = [row[0] for row in cursor.fetchall()]
        conn.close()

        expected_tables = ["applications", "approvals", "documents", "email_messages", "events", "jobs", "reviews", "schema_migrations", "tasks"]
        for t in expected_tables:
            self.assertIn(t, tables)

    def test_apply_migrations_idempotency(self):
        # First run
        apply_migrations(self.db_path)
        # Second run should apply 0 new files
        applied_second = apply_migrations(self.db_path)
        self.assertEqual(len(applied_second), 0)

if __name__ == "__main__":
    unittest.main()
