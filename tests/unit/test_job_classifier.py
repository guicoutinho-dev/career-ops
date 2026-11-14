import unittest
from src.services.job_classifier import (
    classify_job, priority_from_score,
    CATEGORY_SAP, CATEGORY_88D, CATEGORY_OFFICE, CATEGORY_HOSP, CATEGORY_OTHER,
    VISA_88D_LIKELY, VISA_88D_VERIFY, VISA_OPEN,
)


class TestJobClassifier(unittest.TestCase):
    def test_sap_track(self):
        self.assertEqual(classify_job("Senior SAP ABAP Developer", "", "Canberra ACT", "sap"), (CATEGORY_SAP, VISA_OPEN))

    def test_regional_farm_is_likely_88d(self):
        self.assertEqual(classify_job("Farm Hand", "", "Griffith, NSW"), (CATEGORY_88D, VISA_88D_LIKELY))

    def test_metro_farm_requires_verification(self):
        self.assertEqual(classify_job("Fruit Packer Packhouse", "", "Brisbane, QLD"), (CATEGORY_88D, VISA_88D_VERIFY))

    def test_warehouse_picker_is_not_88d(self):
        """Regression: 'Warehouse Pickers Eagle Farm' must never be tagged as 88-day work."""
        cat, fit = classify_job("Warehouse Pickers Eagle Farm!! PM SHIFT", "pick and pack", "Brisbane, QLD")
        self.assertNotEqual(cat, CATEGORY_88D)
        self.assertEqual(fit, VISA_OPEN)

    def test_other_categories(self):
        self.assertEqual(classify_job("Barista", "", "Sydney, NSW")[0], CATEGORY_HOSP)
        self.assertEqual(classify_job("Receptionist", "", "Sydney, NSW")[0], CATEGORY_OFFICE)
        self.assertEqual(classify_job("Forklift Driver", "", "Sydney, NSW")[0], CATEGORY_OTHER)

    def test_priority(self):
        self.assertEqual(priority_from_score(4.2), "🔥 Hot")
        self.assertEqual(priority_from_score(3.9), "⭐ Good")
        self.assertEqual(priority_from_score(3.8), "Standard")
        self.assertEqual(priority_from_score(None), "Standard")


if __name__ == "__main__":
    unittest.main()
