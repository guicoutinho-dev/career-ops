import unittest
import tempfile
import sqlite3
import json
import io
from contextlib import redirect_stdout
from unittest.mock import patch
from pathlib import Path
from src.database.migrations import apply_migrations
from src.database.connection import get_connection
from src.services.approval_service import ApprovalService
from src.services.event_logger import EventLogger

class TestApprovalService(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_appr.db"
        self.applications_dir = Path(self.temp_dir.name) / "applications"
        package_dir = self.applications_dir / "app_99"
        package_dir.mkdir(parents=True)
        (package_dir / "cv-test.pdf").write_bytes(b"synthetic cv")
        (package_dir / "cover-letter-test.pdf").write_bytes(b"synthetic letter")
        (package_dir / "answers.json").write_text(json.dumps({
            "application_id": "app_99",
            "answers_by_field": {
                "first_name": "Test", "last_name": "Candidate", "email": "test@example.invalid",
                "phone": "", "linkedin": "", "work_rights": "Test authorization",
                "sponsorship_required": "Test answer", "availability": "", "salary_expectation": "", "relocation": "",
            },
            "work_authorization_status": "Test authorization",
            "requires_sponsorship": "Test answer",
        }), encoding="utf-8")
        apply_migrations(self.db_path)

        # Seed job and application
        conn = get_connection(self.db_path)
        conn.execute("""
            INSERT INTO jobs (id, source, canonical_url, employer, title, location, description, discovered_at, content_hash)
            VALUES ('job_99', 'seek', 'https://seek.com.au/job/99', 'TestCo', 'SAP Lead', 'Sydney, NSW', 'Test Desc', '2026-10-26T00:00:00Z', 'hash_abc');
        """)
        conn.execute("""
            INSERT INTO applications (id, job_id, profile_id, role_track, match_score, confidence, status, created_at, updated_at)
            VALUES ('app_99', 'job_99', 'prof_1', 'sap', 4.5, 0.9, 'Evaluated', '2026-10-26T00:00:00Z', '2026-10-26T00:00:00Z');
        """)
        conn.commit()
        conn.close()

        self.service = ApprovalService(self.db_path, applications_dir=self.applications_dir)

    def tearDown(self):
        self.temp_dir.cleanup()

    def approve_interactively(self, application_id, package_hash, operator_name="test_operator", phrase=None):
        expected = f"APPROVE {application_id} {package_hash}"
        with patch("sys.stdin.isatty", return_value=True), patch("sys.stdout.isatty", return_value=True), patch(
            "builtins.input", return_value=phrase if phrase is not None else expected
        ):
            return self.service.issue_approval(application_id, package_hash, operator_name)

    def test_build_package_and_issue_approval(self):
        pkg = self.service.build_package("app_99")
        self.assertIsNotNone(pkg)
        self.assertEqual(pkg.application_id, "app_99")
        self.assertEqual(pkg.employer, "TestCo")

        # Issue approval
        package_hash = self.service.compute_package_hash(pkg)
        success, pkg_hash = self.approve_interactively("app_99", package_hash)
        self.assertTrue(success)
        self.assertEqual(len(pkg_hash), 64)

        # Check validity
        is_valid, reason = self.service.verify_approval_validity("app_99", pkg_hash)
        self.assertTrue(is_valid)
        self.assertEqual(reason, "VALID")

    def test_build_package_rejects_missing_review_documents(self):
        """Approval cannot be issued when the candidate has no reviewable CV/cover letter."""
        (self.applications_dir / "app_99" / "cv-test.pdf").unlink()
        self.assertIsNone(self.service.build_package("app_99"))

    def test_issue_approval_requires_exact_reviewed_package_hash(self):
        """A status change alone cannot issue approval; the reviewed hash must match."""
        ok, reason = self.service.issue_approval("app_99")
        self.assertFalse(ok)
        self.assertEqual(reason, "INTERACTIVE_TERMINAL_REQUIRED")

        pkg = self.service.build_package("app_99")
        self.assertIsNotNone(pkg)
        expected = self.service.compute_package_hash(pkg)
        ok, reason = self.approve_interactively("app_99", "0" * 64)
        self.assertFalse(ok)
        self.assertEqual(reason, "EXPLICIT_PACKAGE_CONFIRMATION_REQUIRED")
        ok, reason = self.approve_interactively("app_99", expected, phrase="wrong phrase")
        self.assertFalse(ok)
        self.assertEqual(reason, "EXPLICIT_OPERATOR_CONFIRMATION_REQUIRED")
        ok, token = self.approve_interactively("app_99", expected)
        self.assertTrue(ok)
        self.assertEqual(token, expected)

    def test_service_approval_requires_interactive_terminal(self):
        pkg = self.service.build_package("app_99")
        self.assertIsNotNone(pkg)
        package_hash = self.service.compute_package_hash(pkg)
        output = io.StringIO()
        with patch("sys.stdin.isatty", return_value=False):
            with redirect_stdout(output):
                ok, reason = self.service.issue_approval("app_99", package_hash, "test_operator")
        self.assertFalse(ok)
        self.assertEqual(reason, "INTERACTIVE_TERMINAL_REQUIRED")
        self.assertNotIn("test@example.invalid", output.getvalue())
        self.assertNotIn(str(self.applications_dir), output.getvalue())

    def test_service_approval_refuses_captured_stdout(self):
        pkg = self.service.build_package("app_99")
        self.assertIsNotNone(pkg)
        package_hash = self.service.compute_package_hash(pkg)
        output = io.StringIO()
        with patch("sys.stdin.isatty", return_value=True), redirect_stdout(output):
            ok, reason = self.service.issue_approval("app_99", package_hash, "test_operator")
        self.assertFalse(ok)
        self.assertEqual(reason, "INTERACTIVE_TERMINAL_REQUIRED")
        self.assertNotIn("test@example.invalid", output.getvalue())
        self.assertNotIn(str(self.applications_dir), output.getvalue())

    def test_package_rejects_missing_required_answers(self):
        answers_path = self.applications_dir / "app_99" / "answers.json"
        answers_path.write_text(json.dumps({"application_id": "app_99"}), encoding="utf-8")
        self.assertIsNone(self.service.build_package("app_99"))

        answers = {
            "application_id": "app_99",
            "answers_by_field": {
                field: ("Test answer" if field in self.service.REQUIRED_ANSWER_FIELDS else "")
                for field in self.service.ANSWER_FIELDS
            },
        }
        answers["answers_by_field"]["email"] = "test@example.invalid"
        answers_path.write_text(json.dumps(answers), encoding="utf-8")
        self.assertIsNotNone(self.service.build_package("app_99"))

        for field in self.service.REQUIRED_ANSWER_FIELDS:
            answers["answers_by_field"][field] = "  "
            answers_path.write_text(json.dumps(answers), encoding="utf-8")
            self.assertIsNone(self.service.build_package("app_99"), field)
            answers["answers_by_field"][field] = (
                "test@example.invalid" if field == "email" else "Test answer"
            )

        answers["answers_by_field"]["email"] = "not-an-email"
        answers_path.write_text(json.dumps(answers), encoding="utf-8")
        self.assertIsNone(self.service.build_package("app_99"))

    def test_package_rejects_path_traversal_application_id(self):
        with self.assertRaises(ValueError):
            self.service.build_package("../outside")

    def test_package_rejects_multiple_review_artifacts(self):
        (self.applications_dir / "app_99" / "cv-second.pdf").write_bytes(b"second cv")
        self.assertIsNone(self.service.build_package("app_99"))

    def test_package_hash_binds_artifact_filenames(self):
        before = self.service.build_package("app_99")
        self.assertIsNotNone(before)
        before_hash = self.service.compute_package_hash(before)
        old_path = self.applications_dir / "app_99" / "cv-test.pdf"
        old_path.rename(self.applications_dir / "app_99" / "cv-renamed.pdf")
        after = self.service.build_package("app_99")
        self.assertIsNotNone(after)
        self.assertNotEqual(before_hash, self.service.compute_package_hash(after))

    def test_issue_rejects_ineligible_application_status(self):
        conn = get_connection(self.db_path)
        conn.execute("UPDATE applications SET status = 'DISQUALIFIED' WHERE id = 'app_99';")
        conn.commit()
        conn.close()
        pkg = self.service.build_package("app_99")
        self.assertIsNotNone(pkg)
        package_hash = self.service.compute_package_hash(pkg)
        ok, reason = self.approve_interactively("app_99", package_hash)
        self.assertFalse(ok)
        self.assertEqual(reason, "APPLICATION_STATUS_NOT_ELIGIBLE")

    def test_artifact_change_during_approval_is_rejected(self):
        pkg = self.service.build_package("app_99")
        self.assertIsNotNone(pkg)
        package_hash = self.service.compute_package_hash(pkg)
        original_build = self.service.build_package
        calls = 0

        def change_before_recheck(application_id):
            nonlocal calls
            calls += 1
            if calls == 2:
                (self.applications_dir / "app_99" / "cv-test.pdf").write_bytes(b"changed during approval")
            return original_build(application_id)

        with patch.object(self.service, "build_package", side_effect=change_before_recheck):
            ok, reason = self.approve_interactively("app_99", package_hash)
        self.assertFalse(ok)
        self.assertEqual(reason, "PACKAGE_CHANGED_DURING_APPROVAL")
        conn = get_connection(self.db_path)
        count = conn.execute("SELECT COUNT(*) FROM approvals WHERE application_id = 'app_99';").fetchone()[0]
        conn.close()
        self.assertEqual(count, 0)

    def test_audit_failure_rolls_back_approval_and_status(self):
        pkg = self.service.build_package("app_99")
        self.assertIsNotNone(pkg)
        package_hash = self.service.compute_package_hash(pkg)
        original_log_event = EventLogger.log_event

        def insert_then_fail(logger, *args, **kwargs):
            original_log_event(logger, *args, **kwargs)
            raise RuntimeError("synthetic audit failure")

        with patch("src.services.approval_service.EventLogger.log_event", autospec=True, side_effect=insert_then_fail):
            with self.assertRaisesRegex(RuntimeError, "synthetic audit failure"):
                self.approve_interactively("app_99", package_hash)

        conn = get_connection(self.db_path)
        approval_count = conn.execute(
            "SELECT COUNT(*) FROM approvals WHERE application_id = 'app_99';"
        ).fetchone()[0]
        event_count = conn.execute(
            "SELECT COUNT(*) FROM events WHERE entity_id = 'app_99' AND event_type = 'APPROVAL_ISSUED';"
        ).fetchone()[0]
        status = conn.execute("SELECT status FROM applications WHERE id = 'app_99';").fetchone()[0]
        conn.close()
        self.assertEqual(approval_count, 0)
        self.assertEqual(event_count, 0)
        self.assertEqual(status, "Evaluated")

    def test_answer_changes_invalidate_approval_package(self):
        pkg = self.service.build_package("app_99")
        self.assertIsNotNone(pkg)
        expected = self.service.compute_package_hash(pkg)
        self.approve_interactively("app_99", expected)
        answers_path = self.applications_dir / "app_99" / "answers.json"
        answers = json.loads(answers_path.read_text(encoding="utf-8"))
        answers["answers_by_field"]["work_rights"] = "Changed after review"
        answers_path.write_text(json.dumps(answers), encoding="utf-8")
        valid, reason = self.service.verify_approval_validity("app_99", expected)
        self.assertFalse(valid)
        self.assertEqual(reason, "PACKAGE_HASH_MISMATCH_INVALIDATED")

    def test_approval_invalidation_on_data_tamper(self):
        package_hash = self.service.compute_package_hash(self.service.build_package("app_99"))
        self.approve_interactively("app_99", package_hash)

        # Tamper with job description hash in database
        conn = get_connection(self.db_path)
        conn.execute("UPDATE jobs SET content_hash = 'tampered_hash_xyz' WHERE id = 'job_99';")
        conn.commit()
        conn.close()

        # Validity check should detect mismatch and invalidate!
        is_valid, reason = self.service.verify_approval_validity("app_99", package_hash)
        self.assertFalse(is_valid)
        self.assertEqual(reason, "PACKAGE_HASH_MISMATCH_INVALIDATED")

        # Application status should be reverted to NEEDS_REVIEW
        conn = get_connection(self.db_path)
        status = conn.execute("SELECT status FROM applications WHERE id = 'app_99';").fetchone()[0]
        conn.close()
        self.assertEqual(status, "NEEDS_REVIEW")

if __name__ == "__main__":
    unittest.main()
