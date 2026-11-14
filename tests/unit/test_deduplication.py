import unittest
import sqlite3
from src.database.connection import get_connection
from src.database.migrations import apply_migrations
from src.models.job import JobCandidate
from src.services.deduplicator import DeduplicationService
from src.services.normalizer import compute_content_hash

class TestDeduplication(unittest.TestCase):
    def setUp(self):
        # In-memory SQLite with schema
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        with open("migrations/001_initial_schema.sql", "r", encoding="utf-8") as f:
            self.conn.executescript(f.read())
        self.dedup = DeduplicationService(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_deduplication_exact_url(self):
        candidate_1 = JobCandidate(
            id="job_1",
            source="seek",
            canonical_url="https://www.seek.com.au/job/100",
            employer="Company A",
            title="SAP Developer",
            location="Sydney, NSW",
            description="ABAP role",
            content_hash=compute_content_hash("Company A SAP Developer ABAP role")
        )
        # Insert first
        self.conn.execute("""
            INSERT INTO jobs (id, source, canonical_url, employer, title, location, description, discovered_at, content_hash)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
        """, (
            candidate_1.id, candidate_1.source, candidate_1.canonical_url, candidate_1.employer,
            candidate_1.title, candidate_1.location, candidate_1.description, candidate_1.discovered_at,
            candidate_1.content_hash
        ))
        self.conn.commit()

        # Check duplicate
        is_dup, reason = self.dedup.check_duplicate(candidate_1)
        self.assertTrue(is_dup)
        self.assertTrue(reason.startswith("exact_url_match"))

    def test_deduplication_same_employer_and_title(self):
        candidate_1 = JobCandidate(
            id="job_1",
            source="seek",
            canonical_url="https://www.seek.com.au/job/100",
            employer="Acme Corp",
            title="SAP Specialist",
            location="Brisbane, QLD",
            description="Role description 1",
            content_hash=compute_content_hash("Desc 1")
        )
        self.conn.execute("""
            INSERT INTO jobs (id, source, canonical_url, employer, title, location, description, discovered_at, content_hash)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
        """, (
            candidate_1.id, candidate_1.source, candidate_1.canonical_url, candidate_1.employer,
            candidate_1.title, candidate_1.location, candidate_1.description, candidate_1.discovered_at,
            candidate_1.content_hash
        ))
        self.conn.commit()

        # Another listing with different URL and hash but same employer & title
        candidate_2 = JobCandidate(
            id="job_2",
            source="jobspy_indeed",
            canonical_url="https://au.indeed.com/viewjob?jk=200",
            employer="acme corp",
            title="sap specialist",
            location="Brisbane, QLD",
            description="Role description 2",
            content_hash=compute_content_hash("Desc 2")
        )
        is_dup, reason = self.dedup.check_duplicate(candidate_2)
        self.assertTrue(is_dup)
        self.assertTrue(reason.startswith("employer_title_match"))

if __name__ == "__main__":
    unittest.main()
