import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.services.explore_jobspy import run_explore_jobspy


class ExploreJobSpyTests(unittest.TestCase):
    def test_jobspy_is_blocked_when_robots_policy_is_enabled(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "sources.yaml"
            config.write_text(
                "sources:\n"
                "  seek_native:\n    enabled: false\n"
                "  jobspy:\n    enabled: true\n    country: australia\n"
                "    sites: [indeed, linkedin]\n    respect_robots: true\n",
                encoding="utf-8",
            )
            filters = {"positive": ["Farm Hand"], "sinceDays": 7, "limitPerAts": 150}
            with patch("src.integrations.jobspy.adapter.JobSpyAdapter.scan_jobs") as scan_jobs:
                result = run_explore_jobspy(filters, config_path=config)

        scan_jobs.assert_not_called()
        self.assertEqual(result["sources"]["jobspy_indeed"]["status"], "blocked")
        self.assertEqual(result["sources"]["jobspy_linkedin"]["status"], "blocked")
        self.assertIn("robots", result["sources"]["jobspy_indeed"]["error"].lower())
        self.assertEqual(result["sources"]["seek_native"]["status"], "disabled")
        self.assertEqual(result["offers"], [])

    def test_disabled_jobspy_does_not_call_adapter(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "sources.yaml"
            config.write_text(
                "sources:\n  seek_native:\n    enabled: false\n"
                "  jobspy:\n    enabled: false\n    sites: [indeed]\n",
                encoding="utf-8",
            )
            with patch("src.integrations.jobspy.adapter.JobSpyAdapter.scan_jobs") as scan_jobs:
                result = run_explore_jobspy({"positive": ["Harvest"]}, config_path=config)

        scan_jobs.assert_not_called()
        self.assertEqual(result["sources"]["jobspy_indeed"]["status"], "disabled")
        self.assertEqual(result["sources"]["seek_native"]["status"], "disabled")


if __name__ == "__main__":
    unittest.main()
