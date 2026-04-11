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
JOB_FILE = JOB_DIR / "jobs.json"
RUN_JOBS: dict[str, dict] = {}
RUN_LOCK = threading.RLock()
MONITOR_STARTED = False


def _job_id(kind: str) -> str:
    return f"{datetime.now().strftime('%Y-%m-%d_%H%M%S')}_{kind}"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _rel(path: Path | str) -> str:
    try:
        return Path(path).relative_to(PROJECT_DIR).as_posix()
    except ValueError:
        return str(path)


def _abs(path: Path | str) -> Path:
    path_obj = Path(path)
    return path_obj if path_obj.is_absolute() else PROJECT_DIR / path_obj


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
    persisted_status = job.get("status")
    return_code = process.poll() if process else job.get("return_code")
    if process and return_code is None:
        status = "running"
    elif persisted_status in {"interrupted", "completed", "failed"}:
        status = persisted_status
    elif return_code is None:
        status = "unknown"
    else:
        status = "completed" if return_code == 0 else "failed"
    return {
        "id": job["id"],
        "kind": job["kind"],
        "status": status,
        "return_code": return_code,
        "started_at": job["started_at"],
        "ended_at": job.get("ended_at", ""),
        "command": " ".join(job["command"]),
        "log_path": _rel(job["log_path"]),
        "log_tail": _log_tail(_abs(job["log_path"])),
        "artifacts": job.get("artifacts", []),
    }


def serialize_job(job: dict) -> dict:
    public = public_job(job)
    public.pop("log_tail", None)
    public["command"] = list(job.get("command", []))
    return public


def persist_jobs(path: Path | None = None) -> None:
    path = path or JOB_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    jobs = [serialize_job(job) for job in sorted(RUN_JOBS.values(), key=lambda item: item["started_at"], reverse=True)]
    path.write_text(json.dumps(jobs, indent=2))


def load_jobs(path: Path | None = None) -> list[dict]:
    path = path or JOB_FILE
    if not path.exists():
        return []
    try:
        jobs = json.loads(path.read_text())
    except Exception:
        return []
    return jobs if isinstance(jobs, list) else []


def recover_jobs(path: Path | None = None) -> int:
    path = path or JOB_FILE
    loaded = 0
    with RUN_LOCK:
        RUN_JOBS.clear()
        for record in load_jobs(path):
            if not isinstance(record, dict) or not record.get("id"):
                continue
            status = record.get("status")
            if status == "running":
                status = "interrupted"
                record["ended_at"] = record.get("ended_at") or _now()
                record["return_code"] = record.get("return_code")
            job = {
                "id": record["id"],
                "kind": record.get("kind", "unknown"),
                "command": record.get("command", []),
                "started_at": record.get("started_at", ""),
                "ended_at": record.get("ended_at", ""),
                "return_code": record.get("return_code"),
                "status": status or "unknown",
                "log_path": _abs(record.get("log_path", JOB_DIR / f"{record['id']}.log")),
                "artifacts": record.get("artifacts", []),
            }
            RUN_JOBS[job["id"]] = job
            loaded += 1
        if loaded:
            persist_jobs(path)
    return loaded


def scrape_artifacts() -> list[dict]:
    paths = [PROJECT_DIR / "data_pa_wide.csv", PROJECT_DIR / "data_pa_wide.json"]
    return [{"label": path.name, "path": _rel(path)} for path in paths if path.exists()]


def latest_run_artifacts(started_at: str = "") -> list[dict]:
    runs_dir = PROJECT_DIR / "runs"
    if not runs_dir.exists():
        return []
    run_dirs = sorted(
        [path for path in runs_dir.iterdir() if path.is_dir() and path.name != "web_jobs"],
        key=lambda path: path.name,
        reverse=True,
    )
    for run_dir in run_dirs:
        artifacts = []
        for filename in ("dashboard.html", "report.txt", "scored.json", "discovered.json", "meta.json"):
            path = run_dir / filename
            if path.exists():
                artifacts.append({"label": f"{run_dir.name}/{filename}", "path": _rel(path)})
        if artifacts:
            return artifacts
    return []


def artifacts_for_job(job: dict) -> list[dict]:
    if job.get("kind") == "scrape":
        return scrape_artifacts()
    if job.get("kind") == "score":
        return latest_run_artifacts(job.get("started_at", ""))
    return []


def finish_job(job: dict, return_code: int) -> None:
    job["return_code"] = return_code
    job["status"] = "completed" if return_code == 0 else "failed"
    job["ended_at"] = _now()
    job["artifacts"] = artifacts_for_job(job)


def _monitor_loop(interval: float = 2.0) -> None:
    while True:
        with RUN_LOCK:
            changed = False
            for job in RUN_JOBS.values():
                process = job.get("process")
                if not process or job.get("status") != "running":
                    continue
                return_code = process.poll()
                if return_code is not None:
                    finish_job(job, return_code)
                    changed = True
            if changed:
                persist_jobs()
        threading.Event().wait(interval)


def initialize_job_system(path: Path | None = None) -> int:
    global MONITOR_STARTED
    loaded = recover_jobs(path)
    if not MONITOR_STARTED:
        thread = threading.Thread(target=_monitor_loop, daemon=True)
        thread.start()
        MONITOR_STARTED = True
    return loaded


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
            "started_at": _now(),
            "ended_at": "",
            "log_path": log_path,
            "return_code": None,
            "status": "running",
            "artifacts": [],
        }
        RUN_JOBS[job_id] = job
        persist_jobs()
        return {"ok": True, "job": public_job(job)}, 202


def list_run_jobs() -> list[dict]:
    with RUN_LOCK:
        changed = False
        for job in RUN_JOBS.values():
            process = job.get("process")
            if process and job.get("status") == "running":
                return_code = process.poll()
                if return_code is not None:
                    finish_job(job, return_code)
                    changed = True
        if changed:
            persist_jobs()
        return [public_job(job) for job in sorted(RUN_JOBS.values(), key=lambda item: item["started_at"], reverse=True)]


def get_run_job(job_id: str) -> dict | None:
    with RUN_LOCK:
        job = RUN_JOBS.get(job_id)
        return public_job(job) if job else None


def safe_project_path(path_value: str) -> Path | None:
    try:
        path = _abs(path_value).resolve()
        project = PROJECT_DIR.resolve()
        if path == project or project in path.parents:
            return path
    except Exception:
        return None
    return None


def save_job_snapshot(path: Path = JOB_DIR / "latest.json") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    jobs = list_run_jobs()
    path.write_text(json.dumps(jobs, indent=2))
