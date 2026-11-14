import json
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml
from unittest.mock import patch

from src.integrations.ats_full.adapter import ATSScanResult, ATSAggregatorAdapter


class TestATSAggregatorAdapter(unittest.TestCase):
    def test_scan_profiles_preserves_profile_locations_and_respects_shared_company_budget(self):
        adapter = ATSAggregatorAdapter(project_root=".")
        profiles = {
            "sap": {
                "keywords": {"positive": ["SAP ABAP"], "negative": ["Junior"]},
                "geography": {"locations": ["Sydney NSW", "Melbourne VIC"]},
            },
            "operational": {
                "keywords": {"positive": ["Hospitality"]},
                "geography": {"locations": ["Brisbane QLD"]},
            },
            "farm": {
                "keywords": {"positive": ["Farm Hand", "Harvest"]},
                "geography": {"country": "Australia", "locations": []},
            },
        }
        def result_for(limit):
            return ATSScanResult(
                candidates=[],
                summary={
                    "sources": ["greenhouse"],
                    "companiesAvailable": 100,
                    "companiesScanned": limit,
                    "capHit": False,
                    "datasetStatus": {"greenhouse": "ok"},
                    "sourceStats": {
                        "greenhouse": {
                            "datasetStatus": "ok",
                            "companiesAvailable": 100,
                            "companiesScanned": limit,
                            "unreachableBoards": 1,
                            "matches": 2,
                            "postingsDroppedNoDate": 3,
                        }
                    },
                    "postingsKept": 2,
                    "postingsDroppedNoDate": 3,
                    "unreachableBoards": 1,
                    "offers": [],
                },
            )

        with patch.object(adapter, "scan", side_effect=lambda **kwargs: result_for(kwargs["limit_per_ats"])) as scan:
            result = adapter.scan_profiles(
                profiles,
                ats=["greenhouse"],
                since_days=7,
                limit_per_ats=11,
            )

        self.assertEqual([call.kwargs["limit_per_ats"] for call in scan.call_args_list], [4, 4, 3])
        self.assertEqual(scan.call_args_list[0].kwargs["positive"], ["SAP ABAP"])
        self.assertEqual(scan.call_args_list[0].kwargs["negative"], ["Junior"])
        self.assertEqual(scan.call_args_list[0].kwargs["allow_locations"], ["Sydney NSW", "Melbourne VIC"])
        self.assertEqual(scan.call_args_list[1].kwargs["allow_locations"], ["Brisbane QLD"])
        self.assertEqual(scan.call_args_list[2].kwargs["allow_locations"], [])
        self.assertEqual(result.summary["companiesScanned"], 11)
        self.assertEqual(result.summary["sourceStats"]["greenhouse"]["companiesAvailable"], 100)
        self.assertEqual(result.summary["sourceStats"]["greenhouse"]["unreachableBoards"], 3)
        self.assertEqual(result.summary["sourceStats"]["greenhouse"]["matches"], 6)

    def test_runs_safe_shuffled_json_scan_and_normalizes_candidates(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            scanner = root / "scan-ats-full.mjs"
            scanner.write_text("// fixture scanner", encoding="utf-8")
            observed = {}

            def fake_run(command, *, cwd, env, capture_output, text, timeout, check):
                observed["command"] = command
                observed["cwd"] = cwd
                observed["portals_path"] = env["CAREER_OPS_PORTALS"]
                observed["portals"] = yaml.safe_load(Path(env["CAREER_OPS_PORTALS"]).read_text(encoding="utf-8"))
                return subprocess.CompletedProcess(
                    command,
                    0,
                    json.dumps(
                        {
                            "sources": ["greenhouse", "lever"],
                            "companiesAvailable": 900,
                            "companiesScanned": 300,
                            "capHit": True,
                            "datasetStatus": {"greenhouse": "ok", "lever": "ok"},
                            "postingsKept": 1,
                            "postingsDroppedNoDate": 0,
                            "unreachableBoards": 2,
                            "offers": [
                                {
                                    "company": "Regional Growers",
                                    "title": "Farm Hand",
                                    "url": "https://jobs.example/farm-hand?utm_source=scan",
                                    "location": "Bundaberg QLD",
                                    "postedAt": "2026-10-30",
                                    "source": "greenhouse-full",
                                    "description": "Seasonal orchard and harvest work.",
                                }
                            ],
                        }
                    ),
                    "",
                )

            adapter = ATSAggregatorAdapter(project_root=root, runner=fake_run)
            result = adapter.scan(
                positive=["Farm Hand", "Harvest"],
                allow_locations=["Sydney NSW"],
                block_locations=["Canberra ACT"],
                ats=["greenhouse", "lever"],
                since_days=14,
                limit_per_ats=150,
            )

            self.assertEqual(observed["cwd"], root)
            self.assertIn("--dry-run", observed["command"])
            self.assertIn("--json", observed["command"])
            self.assertIn("--shuffle", observed["command"])
            self.assertEqual(observed["portals"]["title_filter"]["positive"], ["Farm Hand", "Harvest"])
            self.assertEqual(observed["portals"]["location_filter"], {"allow": ["Sydney NSW"], "block": ["Canberra ACT"]})
            self.assertFalse(Path(observed["portals_path"]).exists())
            self.assertTrue(result.summary["capHit"])
            self.assertEqual(result.summary["companiesScanned"], 300)
            self.assertEqual(len(result.candidates), 1)
            candidate = result.candidates[0]
            self.assertEqual(candidate.source, "greenhouse-full")
            self.assertEqual(candidate.description, "Seasonal orchard and harvest work.")
            self.assertEqual(candidate.canonical_url, "https://jobs.example/farm-hand")

    def test_nonzero_scanner_exit_is_not_reported_as_an_empty_scan(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "scan-ats-full.mjs").write_text("// fixture scanner", encoding="utf-8")

            def failed_run(command, **kwargs):
                return subprocess.CompletedProcess(command, 1, "", "scanner unavailable")

            adapter = ATSAggregatorAdapter(project_root=root, runner=failed_run)
            with self.assertRaisesRegex(RuntimeError, "scanner unavailable"):
                adapter.scan(positive=["Farm Hand"], ats=["greenhouse"])


if __name__ == "__main__":
    unittest.main()
