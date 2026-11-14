import unittest
import tempfile
from pathlib import Path
from src.services.form_preparer import ApplicationPreparer

class TestFormPreparer(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.profile_path = Path(self.temp_dir.name) / "profile.yml"
        self.profile_path.write_text(
            "candidate:\n  full_name: Test Candidate\n  email: test@example.invalid\n"
            "  phone: '+1 555 0100'\n  linkedin: https://example.invalid/profile\n"
            "  requires_sponsorship: 'Yes'\n  availability: 'Two weeks'\n"
            "location:\n  city: Test City\n  visa_status: 'Test visa status'\n"
            "  flexibility: 'Relocation possible'\n"
            "compensation:\n  target_range: 'Test compensation range'\n",
            encoding="utf-8",
        )
        self.preparer = ApplicationPreparer(profile_path=self.profile_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_prepare_answers_consistency(self):
        answers = self.preparer.prepare_answers("app_006")
        self.assertEqual(answers.candidate_name, "Test Candidate")
        self.assertEqual(answers.email, "test@example.invalid")
        self.assertEqual(answers.work_authorization_status, "Test visa status")
        self.assertEqual(answers.requires_sponsorship, "Yes")
        self.assertEqual(answers.notice_period_availability, "Two weeks")
        self.assertEqual(answers.salary_expectation, "Test compensation range")
        self.assertEqual(answers.location, "Test City")
        self.assertIn("first_name", answers.answers_by_field)
        self.assertEqual(answers.answers_by_field["first_name"], "Test")

if __name__ == "__main__":
    unittest.main()
