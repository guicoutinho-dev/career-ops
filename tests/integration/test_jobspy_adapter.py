import unittest
from unittest.mock import patch, MagicMock
import pandas as pd
from src.integrations.jobspy.adapter import JobSpyAdapter

class TestJobSpyAdapter(unittest.TestCase):
    @patch("jobspy.scrape_jobs")
    def test_search_jobs_with_mocked_results(self, mock_scrape):
        # Mock dataframe returned by jobspy
        mock_df = pd.DataFrame([
            {
                "id": "job_mock_1",
                "site": "indeed",
                "job_url": "https://au.indeed.com/viewjob?jk=123456789&utm_source=feed",
                "title": "SAP ABAP Specialist",
                "company": "Enterprise Cloud AU",
                "location": "Sydney NSW",
                "description": "SAP ABAP and BTP development opportunity.",
                "date_posted": "2026-10-26"
            }
        ])
        mock_scrape.return_value = mock_df

        adapter = JobSpyAdapter(country="australia", rate_limit_delay=0.0)
        candidates = adapter.search_jobs("SAP ABAP", location="Sydney", results_wanted=1)

        self.assertEqual(len(candidates), 1)
        cand = candidates[0]
        self.assertEqual(cand.employer, "Enterprise Cloud AU")
        self.assertEqual(cand.title, "SAP ABAP Specialist")
        self.assertEqual(cand.location, "Sydney, NSW")
        self.assertEqual(cand.canonical_url, "https://au.indeed.com/viewjob?jk=123456789")
        self.assertEqual(cand.source, "jobspy_indeed")

if __name__ == "__main__":
    unittest.main()
