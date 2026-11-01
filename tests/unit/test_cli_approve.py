import json
import unittest
import tempfile
import io
from contextlib import redirect_stdout
from unittest.mock import patch
from pathlib import Path
from src.database.migrations import apply_migrations
from src.database.connection import get_connection
from src.cli.approve import approve_application
from src.services.approval_service import ApprovalService

class TestCliApprove(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "approve_test.db"
        self.applications_dir = Path(self.temp_dir.name) / "applications"
        package_dir = self.applications_dir / "app_test"
        package_dir.mkdir(parents=True)
        (package_dir / "cv-test.pdf").write_bytes(b"synthetic cv")
        (package_dir / "cover-letter-test.pdf").write_bytes(b"synthetic cover")
        answer_fields = {
            "first_name": "Test", "last_name": "Candidate", "email": "test@example.invalid",
            "phone": "", "linkedin": "", "work_rights": "Test authorization",
            "sponsorship_required": "Test answer", "availability": "", "salary_expectation": "", "relocation": "",
        }
        (package_dir / "answers.json").write_text(
            json.dumps({"application_id": "app_test", "answers_by_field": answer_fields}), encoding="utf-8"
        )
        apply_migrations(self.db_path)

        # Insert test job and application
        conn = get_connection(self.db_path)
        conn.execute("""
            INSERT INTO jobs (id, source, canonical_url, employer, title, location, description, discovered_at, content_hash)
            VALUES ('job_test', 'seek', 'https://seek.com.au/job/test', 'Acme', 'SAP Dev', 'Sydney, NSW', 'Test Desc', '2026-10-26T00:00:00Z', 'hash123');
        """)
        conn.execute("""
            INSERT INTO applications (id, job_id, profile_id, role_track, status, created_at, updated_at)
            VALUES ('app_test', 'job_test', 'prof_1', 'sap', 'PENDING_APPROVAL', '2026-10-26T00:00:00Z', '2026-10-26T00:00:00Z');
        """)
        conn.commit()
        conn.close()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_approve_application(self):
        service = ApprovalService(self.db_path, applications_dir=self.applications_dir)
        package = service.build_package("app_test")
        if package is None:
            self.fail("expected a complete review package")
        package_hash = service.compute_package_hash(package)
        preview_approved = approve_application(
            "app_test", None, "test_operator", str(self.db_path), str(self.applications_dir)
        )
        self.assertFalse(preview_approved)
        self.assertFalse(approve_application(
            "app_test", package_hash, "test_operator", str(self.db_path), str(self.applications_dir)
        ))
        with patch("src.cli.approve.sys.stdin.isatty", return_value=True), patch(
            "src.cli.approve.sys.stdout.isatty", return_value=True
        ), patch(
            "builtins.input", return_value=f"APPROVE app_test {package_hash}"
        ):
            success = approve_application(
                "app_test", package_hash, "test_operator", str(self.db_path), str(self.applications_dir)
            )
        self.assertTrue(success)

        conn = get_connection(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT status FROM applications WHERE id = 'app_test';")
        self.assertEqual(cursor.fetchone()[0], "APPROVED")
        cursor.execute("SELECT approved_by, decision FROM approvals WHERE application_id = 'app_test';")
        row = cursor.fetchone()
        self.assertEqual(row["approved_by"], "test_operator")
        self.assertEqual(row["decision"], "APPROVED")
        conn.close()

    def test_noninteractive_preview_does_not_print_candidate_answers(self):
        output = io.StringIO()
        with patch("sys.stdin.isatty", return_value=False), redirect_stdout(output):
            approved = approve_application(
                "app_test", None, "test_operator", str(self.db_path), str(self.applications_dir)
            )
        self.assertFalse(approved)
        self.assertNotIn("test@example.invalid", output.getvalue())
        self.assertNotIn(str(self.applications_dir), output.getvalue())

    def test_captured_stdout_preview_does_not_print_candidate_answers(self):
        output = io.StringIO()
        with patch("sys.stdin.isatty", return_value=True), redirect_stdout(output):
            approved = approve_application(
                "app_test", None, "test_operator", str(self.db_path), str(self.applications_dir)
            )
        self.assertFalse(approved)
        self.assertNotIn("test@example.invalid", output.getvalue())
        self.assertNotIn(str(self.applications_dir), output.getvalue())

if __name__ == "__main__":
    unittest.main()
