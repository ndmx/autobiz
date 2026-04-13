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

    def test_build_score_command_accepts_uploaded_input(self):
        command = run_jobs.build_score_command(
            {"defaults": {"location": "Pennsylvania", "budget_min": 75000, "budget_max": 250000}},
            "data_uploads/my_listings.json",
        )

        self.assertIn("data_uploads/my_listings.json", command)

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

    def test_recover_jobs_marks_stale_running_job_interrupted(self):
        run_jobs.RUN_JOBS.clear()
        with tempfile.TemporaryDirectory() as tmp:
            job_file = Path(tmp) / "jobs.json"
            log_path = Path(tmp) / "old.log"
            log_path.write_text("started\n")
            job_file.write_text("""[
              {
                "id": "old_scrape",
                "kind": "scrape",
                "status": "running",
                "return_code": null,
                "started_at": "2026-04-11T10:00:00",
                "command": ["python", "scraper.py"],
                "log_path": "%s",
                "artifacts": []
              }
            ]""" % str(log_path))

            loaded = run_jobs.recover_jobs(job_file)
            jobs = run_jobs.list_run_jobs()

        self.assertEqual(loaded, 1)
        self.assertEqual(jobs[0]["status"], "interrupted")
        run_jobs.RUN_JOBS.clear()

    def test_persist_jobs_round_trips_completed_job(self):
        run_jobs.RUN_JOBS.clear()
        with tempfile.TemporaryDirectory() as tmp:
            job_file = Path(tmp) / "jobs.json"
            log_path = Path(tmp) / "done.log"
            log_path.write_text("done\n")
            run_jobs.RUN_JOBS["done_score"] = {
                "id": "done_score",
                "kind": "score",
                "command": ["python", "agent.py"],
                "started_at": "2026-04-11T10:00:00",
                "ended_at": "2026-04-11T10:01:00",
                "return_code": 0,
                "status": "completed",
                "log_path": log_path,
                "artifacts": [{"label": "report", "path": "runs/x/report.txt"}],
            }

            run_jobs.persist_jobs(job_file)
            loaded = run_jobs.load_jobs(job_file)

        self.assertEqual(loaded[0]["status"], "completed")
        self.assertEqual(loaded[0]["artifacts"][0]["label"], "report")
        run_jobs.RUN_JOBS.clear()

    def test_safe_project_path_rejects_external_paths(self):
        self.assertIsNone(run_jobs.safe_project_path("/etc/passwd"))
        self.assertIsNotNone(run_jobs.safe_project_path("README.md"))


if __name__ == "__main__":
    unittest.main()
