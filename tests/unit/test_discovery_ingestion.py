import tempfile
import unittest
from pathlib import Path

from src.database.connection import get_connection
from src.database.migrations import apply_migrations
from src.models.job import JobCandidate
from src.services.discovery import ingest_discovered_jobs
from src.services.normalizer import compute_content_hash


class TestDiscoveredJobIngestion(unittest.TestCase):
    def test_inserts_new_candidate_and_reports_duplicate_without_overwriting(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "applications.db"
            apply_migrations(db_path)
            candidate = JobCandidate(
                id="ats_123",
                source="greenhouse-full",
                canonical_url="https://jobs.example/farm-hand",
                employer="Regional Growers",
                title="Farm Hand",
                location="Bundaberg, QLD",
                description="Seasonal orchard work.",
                publication_date="2026-10-30",
                content_hash=compute_content_hash("Regional Growers Farm Hand Seasonal orchard work."),
            )

            result = ingest_discovered_jobs([candidate, candidate], db_path=db_path)

            self.assertEqual(result, {"inserted": 1, "duplicates": 1})
            conn = get_connection(db_path)
            rows = conn.execute("SELECT id, status, source FROM jobs").fetchall()
            conn.close()
            self.assertEqual(len(rows), 1)
            self.assertEqual(tuple(rows[0]), ("ats_123", "DISCOVERED", "greenhouse-full"))


if __name__ == "__main__":
    unittest.main()
