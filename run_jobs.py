"""Background run launcher for dashboard-triggered scraper and agent jobs."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path


PROJECT_DIR = Path(__file__).parent
JOB_DIR = PROJECT_DIR / "runs" / "web_jobs"
RUN_JOBS: dict[str, dict] = {}
RUN_LOCK = threading.Lock()


def _job_id(kind: str) -> str:
    return f"{datetime.now().strftime('%Y-%m-%d_%H%M%S')}_{kind}"


def build_scrape_command(cfg: dict) -> list[str]:
    defaults = cfg.get("defaults", {})
    location = defaults.get("location", "Pennsylvania")
    min_budget = int(defaults.get("budget_min", 75000))
    max_budget = int(defaults.get("budget_max", 250000))
    return [
        sys.executable,
        "scraper.py",
        "--location",
        location,
        "--min-budget",
        str(min_budget),
        "--budget",
        str(max_budget),
        "--output",
        "data_pa_wide.csv",
        "--json",
        "data_pa_wide.json",
    ]


def build_score_command(cfg: dict) -> list[str]:
    defaults = cfg.get("defaults", {})
    location = defaults.get("location", "Pennsylvania")
    min_budget = int(defaults.get("budget_min", 75000))
    max_budget = int(defaults.get("budget_max", 250000))
    return [
        sys.executable,
        "agent.py",
        "--from-json",
        "data_pa_wide.json",
        "--location",
        location,
        "--min-budget",
        str(min_budget),
        "--budget",
        str(max_budget),
        "--no-commit",
    ]


def _active_same_kind(kind: str) -> dict | None:
    for job in RUN_JOBS.values():
        process = job.get("process")
        if job.get("kind") == kind and process and process.poll() is None:
            return public_job(job)
    return None


def _log_tail(path: Path, limit: int = 5000) -> str:
    if not path.exists():
        return ""
    data = path.read_bytes()
    return data[-limit:].decode("utf-8", errors="replace")


def public_job(job: dict) -> dict:
    process = job.get("process")
    return_code = process.poll() if process else job.get("return_code")
    status = "running" if return_code is None else ("completed" if return_code == 0 else "failed")
    return {
        "id": job["id"],
        "kind": job["kind"],
        "status": status,
        "return_code": return_code,
        "started_at": job["started_at"],
        "command": " ".join(job["command"]),
        "log_path": str(job["log_path"]),
        "log_tail": _log_tail(job["log_path"]),
    }


def start_run_job(kind: str, command: list[str]) -> tuple[dict, int]:
    if kind not in {"scrape", "score"}:
        return {"ok": False, "error": f"Unknown job kind: {kind}"}, 400

    JOB_DIR.mkdir(parents=True, exist_ok=True)
    with RUN_LOCK:
        active = _active_same_kind(kind)
        if active:
            return {"ok": False, "error": f"{kind} job already running", "job": active}, 409

        job_id = _job_id(kind)
        log_path = JOB_DIR / f"{job_id}.log"
        log_file = open(log_path, "w", encoding="utf-8")
        env = os.environ.copy()
        env["AUTOBIZ_NO_BROWSER"] = "1"
        process = subprocess.Popen(
            command,
            cwd=PROJECT_DIR,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
        )
        log_file.close()
        job = {
            "id": job_id,
            "kind": kind,
            "command": command,
            "process": process,
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "log_path": log_path,
        }
        RUN_JOBS[job_id] = job
        return {"ok": True, "job": public_job(job)}, 202


def list_run_jobs() -> list[dict]:
    with RUN_LOCK:
        return [public_job(job) for job in sorted(RUN_JOBS.values(), key=lambda item: item["started_at"], reverse=True)]


def save_job_snapshot(path: Path = JOB_DIR / "latest.json") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    jobs = list_run_jobs()
    path.write_text(json.dumps(jobs, indent=2))
