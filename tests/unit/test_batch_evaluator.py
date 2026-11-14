import datetime
import tempfile
import unittest
from pathlib import Path

from src.database.connection import get_connection
from src.database.migrations import apply_migrations
from src.services.batch_evaluator import evaluate_pending_jobs
from src.services.normalizer import compute_content_hash


class TestBatchEvaluatorCategoryRouting(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "applications.db"
        apply_migrations(self.db_path)
        self._insert_job(
            "farm-job",
            "Fruit Picker",
            "Harvest and pack fruit at a regional orchard. Seasonal work.",
            "Bundaberg QLD",
        )
        self._insert_job("sap-job", "SAP ABAP Developer", "ABAP and S/4HANA development.", "Remote Australia")
        self._insert_job("thin-job", "Farm Hand", "", "Regional Queensland")

    def tearDown(self):
        self.temp_dir.cleanup()

    def _insert_job(self, job_id, title, description, location):
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        conn = get_connection(self.db_path)
        conn.execute(
            """INSERT INTO jobs (
                id, source, canonical_url, employer, title, location, description,
                discovered_at, publication_date, status, content_hash
            ) VALUES (?, 'fixture', ?, 'Example Employer', ?, ?, ?, ?, '2026-10-30', 'DISCOVERED', ?);""",
            (
                job_id,
                f"https://jobs.example/{job_id}",
                title,
                location,
                description,
                now,
                compute_content_hash(f"Example Employer {title} {description}"),
            ),
        )
        conn.commit()
        conn.close()

    def test_evaluation_routes_farm_jobs_through_category_and_persists_classification(self):
        results = evaluate_pending_jobs(limit=10, db_path=self.db_path)

        farm = next(result for result in results if result.job_id == "farm-job")
        self.assertEqual(farm.role_track, "operational")
        self.assertIn("Fruit picking / packing", farm.strengths)

        conn = get_connection(self.db_path)
        row = conn.execute(
            "SELECT role_track, category, visa_fit FROM applications WHERE job_id = 'farm-job'"
        ).fetchone()
        conn.close()
        self.assertEqual(tuple(row), ("operational", "88 Days Specified Work", "88d_likely"))

    def test_sap_jobs_keep_the_sap_track(self):
        results = evaluate_pending_jobs(limit=10, db_path=self.db_path)

        sap = next(result for result in results if result.job_id == "sap-job")
        self.assertEqual(sap.role_track, "sap")

    def test_jobs_without_descriptions_are_held_for_human_review(self):
        evaluate_pending_jobs(limit=10, db_path=self.db_path)

        conn = get_connection(self.db_path)
        row = conn.execute(
            "SELECT status, category, visa_fit FROM applications WHERE job_id = 'thin-job'"
        ).fetchone()
        conn.close()
        self.assertEqual(tuple(row), ("NEEDS_REVIEW", "88 Days Specified Work", "88d_likely"))


if __name__ == "__main__":
    unittest.main()
