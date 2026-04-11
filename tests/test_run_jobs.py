import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import run_jobs


class RunJobsTests(unittest.TestCase):
    def test_build_scrape_command_uses_defaults(self):
        command = run_jobs.build_scrape_command({
            "defaults": {"location": "Pennsylvania", "budget_min": 75000, "budget_max": 250000}
        })

        self.assertEqual(command[0], sys.executable)
        self.assertIn("scraper.py", command)
        self.assertIn("data_pa_wide.json", command)

    def test_build_score_command_uses_scraped_json(self):
        command = run_jobs.build_score_command({
            "defaults": {"location": "Pennsylvania", "budget_min": 75000, "budget_max": 250000}
        })

        self.assertIn("agent.py", command)
        self.assertIn("--from-json", command)
        self.assertIn("data_pa_wide.json", command)

    @patch("run_jobs.subprocess.Popen")
    def test_start_run_job_tracks_background_process(self, popen):
        process = Mock()
        process.poll.return_value = None
        popen.return_value = process
        run_jobs.RUN_JOBS.clear()
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(run_jobs, "JOB_DIR", Path(tmp)):
                payload, status = run_jobs.start_run_job("scrape", [sys.executable, "scraper.py"])

        self.assertEqual(status, 202)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["job"]["status"], "running")
        self.assertEqual(len(run_jobs.RUN_JOBS), 1)
        run_jobs.RUN_JOBS.clear()


if __name__ == "__main__":
    unittest.main()
