import unittest
import tempfile
from unittest.mock import patch
import sqlite3
import json
from pathlib import Path
from src.database.migrations import apply_migrations
from src.database.connection import get_connection
from src.services.form_preparer import ApplicationPreparer
from src.services.approval_service import ApprovalService

class TestSubmissionBarrier(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_barrier.db"
        self.applications_dir = Path(self.temp_dir.name) / "applications"
        self.profile_path = Path(self.temp_dir.name) / "profile.yml"
        self.profile_path.write_text(
            "candidate:\n  full_name: Test Candidate\n  email: test@example.invalid\n"
            "  requires_sponsorship: 'Test answer'\n"
            "location:\n  visa_status: Test authorization\n",
            encoding="utf-8",
        )
        apply_migrations(self.db_path)

        conn = get_connection(self.db_path)
        conn.execute("""
            INSERT INTO jobs (id, source, canonical_url, employer, title, location, description, discovered_at, content_hash)
            VALUES ('job_barrier', 'seek', 'https://seek.com.au/job/barrier', 'SafeCorp', 'SAP Architect', 'Sydney, NSW', 'Desc', '2026-10-26T00:00:00Z', 'hash_safe');
        """)
        conn.execute("""
            INSERT INTO applications (id, job_id, profile_id, role_track, match_score, confidence, status, created_at, updated_at)
            VALUES ('app_barrier', 'job_barrier', 'prof_1', 'sap', 4.8, 0.9, 'Evaluated', '2026-10-26T00:00:00Z', '2026-10-26T00:00:00Z');
        """)
        conn.commit()
        conn.close()

        self.preparer = ApplicationPreparer(self.db_path, self.profile_path, self.applications_dir)
        self.approval_service = ApprovalService(self.db_path, applications_dir=self.applications_dir)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_submission_blocked_without_approval(self):
        """Unapproved applications must strictly raise PermissionError when attempting submission."""
        with self.assertRaises(PermissionError) as ctx:
            self.preparer.attempt_submission("app_barrier")
        self.assertIn("SUBMISSION BLOCKED", str(ctx.exception))

    def test_submission_permitted_after_valid_approval(self):
        """Approved application with valid cryptographic token passes the safety gate."""
        # 1. Issue approval
        self.preparer.assemble_submission_package("app_barrier")
        app_dir = self.applications_dir / "app_barrier"
        (app_dir / "cv-test.pdf").write_bytes(b"synthetic cv")
        (app_dir / "cover-letter-test.pdf").write_bytes(b"synthetic cover")
        pkg = self.approval_service.build_package("app_barrier")
        self.assertIsNotNone(pkg)
        expected_hash = self.approval_service.compute_package_hash(pkg)
        with patch("sys.stdin.isatty", return_value=True), patch("sys.stdout.isatty", return_value=True), patch(
            "builtins.input", return_value=f"APPROVE app_barrier {expected_hash}"
        ):
            success, token = self.approval_service.issue_approval(
                "app_barrier", expected_hash, "test_operator"
            )
        self.assertTrue(success)

        # 2. Attempt submission gate
        with self.assertRaises(PermissionError):
            self.preparer.attempt_submission("app_barrier")
        allowed, msg = self.preparer.attempt_submission("app_barrier", token)
        self.assertTrue(allowed)
        self.assertIn("GATE PASSED", msg)

    def test_assemble_submission_package_creates_checklist(self):
        """Assembling package writes checklist.md and answers.json."""
        res = self.preparer.assemble_submission_package("app_barrier")
        self.assertTrue(Path(res["checklist_path"]).exists())
        self.assertTrue(Path(res["answers_path"]).exists())
        checklist_text = Path(res["checklist_path"]).read_text(encoding="utf-8")
        self.assertIn("Pre-Submission Checklist", checklist_text)
        self.assertIn("Test authorization", checklist_text)

if __name__ == "__main__":
    unittest.main()
