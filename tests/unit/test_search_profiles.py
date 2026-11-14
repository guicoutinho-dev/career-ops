import tempfile
import unittest
from pathlib import Path

import yaml

from src.services.search_profiles import collect_search_terms, load_search_profiles


class TestSearchProfiles(unittest.TestCase):
    def test_project_profiles_include_the_requested_farm_and_hospitality_track(self):
        profiles = load_search_profiles(Path("config/search_profiles.yaml"))

        self.assertEqual(profiles["farm_agriculture"]["role_track"], "operational")
        self.assertEqual(
            profiles["farm_agriculture"]["keywords"]["positive"],
            [
                "Farm Hand",
                "Farmhand",
                "Agriculture",
                "Horticulture",
                "Solar Farm",
                "Solar",
                "Harvest",
                "Fruit Pick",
                "Vineyard",
                "Hospitality",
            ],
        )

    def test_loads_enabled_profiles_and_deduplicates_terms_case_insensitively(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "search_profiles.yaml"
            config_path.write_text(
                yaml.safe_dump(
                    {
                        "profiles": {
                            "farm": {
                                "enabled": True,
                                "role_track": "operational",
                                "keywords": {"positive": ["Farm Hand", "Harvest"]},
                            },
                            "paused": {
                                "enabled": False,
                                "role_track": "operational",
                                "keywords": {"positive": ["Ignored"]},
                            },
                            "hospitality": {
                                "role_track": "operational",
                                "keywords": {"positive": ["harvest", "Hospitality"]},
                            },
                        }
                    },
                    sort_keys=False,
                ),
                encoding="utf-8",
            )

            profiles = load_search_profiles(config_path)

            self.assertEqual(set(profiles), {"farm", "hospitality"})
            self.assertEqual(
                collect_search_terms(profiles),
                ["Farm Hand", "Harvest", "Hospitality"],
            )

    def test_rejects_malformed_profile_without_keywords(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "search_profiles.yaml"
            config_path.write_text("profiles:\n  broken:\n    role_track: operational\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "broken.*positive"):
                load_search_profiles(config_path)

    def test_normalizes_profile_locations_for_scanning(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "search_profiles.yaml"
            config_path.write_text(
                yaml.safe_dump(
                    {
                        "profiles": {
                            "farm": {
                                "role_track": "operational",
                                "keywords": {"positive": ["Farm Hand"]},
                                "geography": {"country": "Australia", "locations": ["  Bundaberg QLD ", "", None]},
                            }
                        }
                    },
                    sort_keys=False,
                ),
                encoding="utf-8",
            )

            profiles = load_search_profiles(config_path)

            self.assertEqual(profiles["farm"]["geography"]["locations"], ["Bundaberg QLD"])


if __name__ == "__main__":
    unittest.main()
