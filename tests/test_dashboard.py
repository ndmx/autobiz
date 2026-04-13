import unittest
import tempfile
from io import BytesIO
from unittest.mock import patch
from pathlib import Path

from app import app
import app as app_module


class DashboardTests(unittest.TestCase):
    def test_dashboard_route_renders_visual_board(self):
        client = app.test_client()
        response = client.get("/dashboard")

        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("Philadelphia-first acquisition board", body)
        self.assertIn("Ranked by distance from Philadelphia, then score.", body)
        self.assertIn("Start New Scrape", body)
        self.assertIn("Score Selected Dataset", body)
        self.assertIn("Upload Dataset", body)
        self.assertIn("<table>", body)

    def test_start_scrape_route_returns_background_job(self):
        client = app.test_client()
        with patch("app.start_run_job", return_value=({"ok": True, "job": {"status": "running"}}, 202)):
            response = client.post("/jobs/start-scrape")

        self.assertEqual(response.status_code, 202)
        self.assertTrue(response.get_json()["ok"])

    def test_upload_data_accepts_csv(self):
        client = app.test_client()
        with tempfile.TemporaryDirectory() as tmp:
            upload_dir = Path(tmp)
            with patch.object(app_module, "UPLOAD_DIR", upload_dir):
                response = client.post(
                    "/data/upload",
                    data={"data_file": (BytesIO(b"business_name,location\nRoute,Philadelphia\n"), "routes.csv")},
                    content_type="multipart/form-data",
                )

            self.assertEqual(response.status_code, 302)
            self.assertTrue((upload_dir / "routes.csv").exists())

    def test_job_status_route_returns_jobs(self):
        client = app.test_client()
        with patch("app.list_run_jobs", return_value=[{"id": "job", "status": "running"}]):
            response = client.get("/jobs/status")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["jobs"][0]["status"], "running")

    def test_job_log_route_returns_log_tail(self):
        client = app.test_client()
        with patch("app.get_run_job", return_value={"id": "job", "log_tail": "hello log"}):
            response = client.get("/jobs/log/job")

        self.assertEqual(response.status_code, 200)
        self.assertIn("hello log", response.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
