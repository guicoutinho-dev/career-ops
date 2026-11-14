import unittest
from unittest.mock import patch

import pandas as pd

from src.integrations.jobspy.adapter import JobSpyAdapter


class TestJobSpyAdapterHealth(unittest.TestCase):
    @patch("jobspy.scrape_jobs")
    def test_reports_per_site_counts_and_errors(self, mock_scrape):
        jobs = pd.DataFrame(
            [
                {
                    "id": "job-1",
                    "site": "indeed",
                    "job_url": "https://au.indeed.com/viewjob?jk=1",
                    "title": "Farm Hand",
                    "company": "Farm One",
                    "location": "Melbourne VIC",
                    "description": "Seasonal farm work",
                    "date_posted": "2026-10-30",
                }
            ]
        )
        jobs.attrs["errors"] = {"linkedin": "HTTP 429 blocked"}
        mock_scrape.return_value = jobs

        result = JobSpyAdapter(rate_limit_delay=0).scan_jobs(
            search_term="Farm Hand OR farmhand",
            location="Australia",
            sites=["indeed", "linkedin"],
        )

        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(result.sources["indeed"].status, "searched")
        self.assertEqual(result.sources["indeed"].matches, 1)
        self.assertEqual(result.sources["linkedin"].status, "blocked")
        self.assertEqual(result.sources["linkedin"].matches, 0)
        self.assertIn("429", result.sources["linkedin"].error)

    @patch("jobspy.scrape_jobs", side_effect=RuntimeError("network unavailable"))
    def test_reports_exceptions_as_failed_not_zero_results(self, _mock_scrape):
        result = JobSpyAdapter(rate_limit_delay=0).scan_jobs(
            search_term="Harvest", location="Australia", sites=["indeed"]
        )

        self.assertEqual(result.candidates, [])
        self.assertEqual(result.sources["indeed"].status, "failed")
        self.assertIn("network unavailable", result.sources["indeed"].error)


if __name__ == "__main__":
    unittest.main()
