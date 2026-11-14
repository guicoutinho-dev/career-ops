import unittest
from unittest.mock import patch
from src.integrations.notion.adapter import NotionAdapter

class TestNotionAdapter(unittest.TestCase):
    def test_format_properties_mapping(self):
        adapter = NotionAdapter(dry_run=True)
        raw_data = {
            "id": "app_006",
            "employer": "George Weston Foods",
            "title": "Senior SAP BTP Integration Developer",
            "role_track": "sap",
            "match_score": 4.8,
            "status": "Evaluated",
            "location": "Sydney, NSW",
            "canonical_url": "https://www.seek.com.au/id/job/95339857"
        }
        props = adapter.format_properties(raw_data)

        self.assertIn("Position", props)
        self.assertEqual(props["Position"]["title"][0]["text"]["content"], "Senior SAP BTP Integration Developer")
        self.assertEqual(props["Company"]["rich_text"][0]["text"]["content"], "George Weston Foods")
        self.assertEqual(props["Track"]["select"]["name"], "SAP Specialist")
        self.assertEqual(props["Match Score"]["number"], 4.8)
        self.assertEqual(props["Application Status"]["status"]["name"], "To Apply")
        self.assertEqual(props["Job URL"]["url"], "https://www.seek.com.au/job/95339857")

    def test_sync_application_dry_run(self):
        adapter = NotionAdapter(dry_run=True)
        success, msg = adapter.sync_application("app_mock_dry_run_01")
        # Since dry run mock doesn't hit DB with actual ID
        self.assertFalse(success)  # Application not found in DB

    @patch.object(NotionAdapter, "_http_request")
    def test_sync_application_mocked_http_success(self, mock_http):
        mock_http.return_value = (200, {"id": "page_notion_12345"})
        adapter = NotionAdapter(token="secret_token_123", database_id="db_123", dry_run=False)

        # Clear notion_page_id for test app_006 in DB if needed or use real mock
        with patch("src.integrations.notion.adapter.get_connection") as mock_conn:
            from unittest.mock import MagicMock
            mock_cursor = MagicMock()
            mock_cursor.fetchone.return_value = {
                "id": "app_test",
                "role_track": "sap",
                "match_score": 4.5,
                "confidence": 0.9,
                "status": "QUALIFIED",
                "created_at": "2026-10-27",
                "updated_at": "2026-10-27",
                "next_action_at": None,
                "employer": "Test Employer",
                "title": "Test Title",
                "location": "Sydney",
                "canonical_url": "https://seek.com.au/job/1",
                "description": "Desc",
                "package_hash": None,
                "approval_decision": None
            }
            mock_cursor_page = MagicMock()
            mock_cursor_page.fetchone.return_value = None  # No existing page
            mock_conn.return_value.cursor.side_effect = [mock_cursor, mock_cursor_page]

            success, msg = adapter.sync_application("app_test")
            self.assertTrue(success)
            self.assertIn("page_notion_12345", msg)

if __name__ == "__main__":
    unittest.main()
