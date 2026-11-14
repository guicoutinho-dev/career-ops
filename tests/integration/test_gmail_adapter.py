"""
Unit and Integration tests for Gmail Adapter and Inbound Classifier.
Validates:
1. Message classification (Acknowledgment, Rejection, Interview, Screening, Offer, Assessment).
2. Multi-signal matching (Employer name, sender domain, role title).
3. SQLite checkpointing and idempotency (already_processed detection).
4. Audit event logging on message receipt.
"""

import unittest
import tempfile
import sqlite3
from pathlib import Path

from src.database.migrations import apply_migrations
from src.integrations.gmail.adapter import GmailAdapter
from src.services.email_classifier import classify_email


class TestGmailIntegration(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "test_applications.db"
        apply_migrations(self.db_path)

        # Seed sample job and application
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("""
            INSERT INTO jobs (id, source, canonical_url, employer, title, location, description, discovered_at, content_hash)
            VALUES ('job_test_01', 'seek', 'https://seek.com.au/job/111', 'Deloitte Australia', 'Senior SAP Consultant', 'Sydney, NSW', 'SAP ABAP S/4HANA', '2026-10-27T00:00:00Z', 'hash_01');
        """)
        conn.execute("""
            INSERT INTO applications (id, job_id, profile_id, role_track, match_score, confidence, status, created_at, updated_at)
            VALUES ('app_test_01', 'job_test_01', 'sap_specialist', 'sap', 4.8, 0.95, 'APPLIED', '2026-10-27T00:00:00Z', '2026-10-27T00:00:00Z');
        """)
        conn.commit()
        conn.close()

        self.adapter = GmailAdapter(db_path=str(self.db_path))

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_email_classification_types(self):
        """Verifies accurate classification of different recruitment email texts."""
        # 1. Interview
        cat, conf, _ = classify_email("Invitation to Interview", "We would like to invite you for a first round interview via Zoom.")
        self.assertEqual(cat, "INTERVIEW")
        self.assertGreaterEqual(conf, 0.80)

        # 2. Rejection
        cat, conf, _ = classify_email("Update on your application", "Unfortunately we have decided to pursue other candidates at this time.")
        self.assertEqual(cat, "REJECTION")
        self.assertGreaterEqual(conf, 0.80)

        # 3. Acknowledgment
        cat, conf, _ = classify_email("Application Received", "Thank you for applying. We have received your application.")
        self.assertEqual(cat, "ACKNOWLEDGMENT")

        # 4. Assessment
        cat, conf, _ = classify_email("Coding Challenge Next Steps", "Please complete the technical test on Hackerrank within 48 hours.")
        self.assertEqual(cat, "ASSESSMENT")

        # 5. Offer
        cat, conf, _ = classify_email("Offer of Employment", "We are pleased to offer you the role of Senior SAP Consultant.")
        self.assertEqual(cat, "OFFER")

    def test_message_processing_and_matching(self):
        """Verifies that an incoming email matches Deloitte and creates DB entries + audit events."""
        result = self.adapter.process_raw_message(
            provider_message_id="gmail_msg_deloitte_01",
            sender="talent@deloitte.com.au",
            subject="Interview with Deloitte Australia - Senior SAP Consultant",
            body="Hi Guilherme, We would like to schedule a first round interview for the Senior SAP Consultant role.",
            received_at="2026-10-27T12:00:00Z"
        )

        self.assertEqual(result["status"], "processed")
        self.assertEqual(result["classification"], "INTERVIEW")
        self.assertEqual(result["matched_application_id"], "app_test_01")
        self.assertGreaterEqual(result["matching_confidence"], 0.70)

        # Test Idempotency (repeat message ID)
        repeat_result = self.adapter.process_raw_message(
            provider_message_id="gmail_msg_deloitte_01",
            sender="talent@deloitte.com.au",
            subject="Interview with Deloitte Australia",
            body="duplicate",
            received_at="2026-10-27T12:00:00Z"
        )
        self.assertEqual(repeat_result["status"], "already_processed")

        # Verify SQLite event log
        conn = sqlite3.connect(str(self.db_path))
        cur = conn.cursor()
        cur.execute("SELECT event_type, actor FROM events WHERE entity_id = 'app_test_01'")
        event_row = cur.fetchone()
        conn.close()

        self.assertIsNotNone(event_row)
        self.assertEqual(event_row[0], "email_interview")
        self.assertEqual(event_row[1], "gmail_adapter")


if __name__ == "__main__":
    unittest.main()
