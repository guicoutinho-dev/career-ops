import unittest
from src.services.normalizer import normalize_url, normalize_location, compute_content_hash

class TestNormalization(unittest.TestCase):
    def test_normalize_url_strips_tracking(self):
        raw = "https://www.seek.com.au/job/12345?utm_source=google&utm_medium=cpc&ref=seek_au#apply"
        expected = "https://www.seek.com.au/job/12345"
        self.assertEqual(normalize_url(raw), expected)

    def test_normalize_location_standardization(self):
        self.assertEqual(normalize_location("Melbourne VIC"), "Melbourne, VIC")
        self.assertEqual(normalize_location("Sydney New South Wales"), "Sydney, NSW")
        self.assertEqual(normalize_location("Brisbane Queensland"), "Brisbane, QLD")

    def test_compute_content_hash_deterministic(self):
        text_a = "  SAP ABAP   Developer at Deloitte   "
        text_b = "SAP abap developer at deloitte"
        self.assertEqual(compute_content_hash(text_a), compute_content_hash(text_b))

if __name__ == "__main__":
    unittest.main()
