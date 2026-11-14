import unittest
from src.services.match_analyst import MatchAnalyst


class TestCategoryScoring(unittest.TestCase):
    def setUp(self):
        self.analyst = MatchAnalyst()

    def test_regional_farm_job_no_longer_auto_rejected(self):
        """Regression: 89/92 genuine farm jobs were disqualified for 'zero skill overlap'."""
        kwargs = dict(job_id="j1", title="Farm Hand", employer="Pure Foods Eggs",
                      description="Full-time farmhand, harvesting and animal care. WHV subclass 462 accepted.", location="Tasmania")
        old = self.analyst.evaluate_job(role_track="operational", **kwargs)
        new = self.analyst.evaluate_job(role_track="operational", category="88 Days Specified Work", **kwargs)
        self.assertEqual(old.recommendation, "AUTO_DISQUALIFY")
        self.assertEqual(new.recommendation, "AUTO_QUALIFY")

    def test_hard_blockers_still_win_over_category(self):
        res = self.analyst.evaluate_job(
            job_id="j2", title="Farm Hand", employer="X", location="NT", role_track="operational",
            description="Must be an Australian citizen. Farm work.", category="88 Days Specified Work")
        self.assertEqual(res.recommendation, "AUTO_DISQUALIFY")
        self.assertTrue(res.hard_blockers)

    def test_barista_and_office_categories(self):
        b = self.analyst.evaluate_job(job_id="j3", title="Barista", employer="Cafe X", location="Sydney",
                                      description="Espresso machine experience. WHV subclass 462 accepted.", role_track="operational",
                                      category="Hospitality / Barista")
        o = self.analyst.evaluate_job(job_id="j4", title="Receptionist", employer="Corp", location="Sydney",
                                      description="Reception and data entry with Microsoft Office. WHV subclass 462 accepted.",
                                      role_track="operational", category="Office Support")
        self.assertEqual(b.recommendation, "AUTO_QUALIFY")
        self.assertEqual(o.recommendation, "AUTO_QUALIFY")

    def test_unrelated_job_still_rejected_with_category(self):
        res = self.analyst.evaluate_job(job_id="j5", title="Forklift Driver", employer="X", location="Sydney",
                                        description="Drive forklifts", role_track="operational",
                                        category="Office Support")
        self.assertEqual(res.recommendation, "AUTO_DISQUALIFY")


if __name__ == "__main__":
    unittest.main()
