import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from src.integrations.ats_full.adapter import ATSScanResult
from src.models.job import JobCandidate
from src.scheduler.daily_runner import DailyRunner
from src.services.normalizer import compute_content_hash


class FakeATSAdapter:
    def __init__(self, result):
        self.result = result
        self.options = None

    def scan_profiles(self, **options):
        self.options = options
        return self.result


class TestDailyRunnerDiscovery(unittest.TestCase):
    def test_daily_cycle_scans_public_ats_imports_results_and_reports_blocked_sources(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "applications.db"
            report_dir = root / "reports"
            config_dir = root / "config"
            config_dir.mkdir()
            (config_dir / "sources.yaml").write_text(
                yaml.safe_dump(
                    {
                        "sources": {
                            "ats_aggregators": {
                                "enabled": True,
                                "providers": ["greenhouse", "lever", "ashby", "workday"],
                                "since_days": 7,
                                "limit_per_ats": 150,
                            },
                            "seek_native": {"enabled": False},
                            "jobspy": {"enabled": True},
                        }
                    },
                    sort_keys=False,
                ),
                encoding="utf-8",
            )
            (config_dir / "search_profiles.yaml").write_text(
                yaml.safe_dump(
                    {
                        "profiles": {
                            "sap_primary": {
                                "role_track": "sap",
                                "keywords": {"positive": ["SAP ABAP"]},
                                "geography": {"locations": ["Sydney NSW"]},
                            },
                            "farm_agriculture": {
                                "role_track": "operational",
                                "keywords": {"positive": ["Farm Hand", "Harvest"]},
                                "geography": {"country": "Australia", "locations": []},
                            },
                        }
                    },
                    sort_keys=False,
                ),
                encoding="utf-8",
            )
            candidate = JobCandidate(
                id="ats_farm_1",
                source="greenhouse-full",
                canonical_url="https://jobs.example/farm-hand",
                employer="Regional Growers",
                title="Farm Hand",
                location="Bundaberg, QLD",
                description="Seasonal orchard and harvest work.",
                publication_date="2026-10-30",
                content_hash=compute_content_hash("Regional Growers Farm Hand Seasonal orchard and harvest work."),
            )
            adapter = FakeATSAdapter(
                ATSScanResult(
                    candidates=[candidate],
                    summary={
                        "sources": ["greenhouse", "lever", "ashby", "workday"],
                        "companiesAvailable": 800,
                        "companiesScanned": 600,
                        "capHit": True,
                        "datasetStatus": {"greenhouse": "ok", "lever": "ok", "ashby": "ok", "workday": "ok"},
                        "sourceStats": {
                            "greenhouse": {
                                "datasetStatus": "ok",
                                "companiesAvailable": 12,
                                "companiesScanned": 10,
                                "unreachableBoards": 1,
                                "matches": 1,
                                "postingsDroppedNoDate": 2,
                            }
                        },
                        "postingsKept": 1,
                        "postingsDroppedNoDate": 0,
                        "unreachableBoards": 0,
                    },
                )
            )

            runner = DailyRunner(
                db_path=db_path,
                discovery_adapter=adapter,
                report_dir=report_dir,
                project_root=root,
            )
            with patch("src.scheduler.daily_runner.sync_pipeline_md_to_sqlite", return_value=0), \
                 patch("src.scheduler.daily_runner.sync_applications_md_to_sqlite", return_value=0), \
                 patch("src.scheduler.daily_runner.create_backup", return_value=(True, "backup-test.db")):
                summary = runner.run_daily_cycle(eval_limit=10)

            self.assertEqual(adapter.options["profiles"]["farm_agriculture"]["keywords"]["positive"][0], "Farm Hand")
            self.assertEqual(adapter.options["profiles"]["sap_primary"]["geography"]["locations"][0], "Sydney NSW")
            self.assertEqual(adapter.options["profiles"]["farm_agriculture"]["geography"]["locations"], [])
            self.assertEqual(summary["new_jobs_from_ats"], 1)
            self.assertEqual(summary["discovery_sources"]["seek_native"]["status"], "disabled")
            self.assertEqual(summary["discovery_sources"]["jobspy"]["status"], "blocked")
            self.assertEqual(summary["discovery_sources"]["ats:greenhouse"]["matches"], 1)
            self.assertNotIn("SEEK Australia (API)", summary["sources_attempted"])
            self.assertTrue(Path(summary["report_path"]).exists())
            report = Path(summary["report_path"]).read_text(encoding="utf-8")
            self.assertIn("ats:greenhouse:** ok", report)
            self.assertIn("10/12 companies, 1 matches, 1 unreachable, 2 undated skipped", report)


if __name__ == "__main__":
    unittest.main()
