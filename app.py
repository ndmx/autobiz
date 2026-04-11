"""
app.py — autobiz Settings Web UI

Run with:
    uv run app.py

Opens the dashboard at: http://localhost:7860/dashboard
"""

import os
import threading
import webbrowser

from flask import Flask, Response, jsonify, redirect, render_template, request, send_file, url_for

import config as cfg
from dashboard_data import dashboard_context
from run_jobs import (
    build_score_command,
    build_scrape_command,
    get_run_job,
    initialize_job_system,
    list_run_jobs,
    safe_project_path,
    start_run_job,
)

app = Flask(__name__)
initialize_job_system()

BROWSER_DISABLED_VALUES = {"1", "true", "yes", "on"}


# ---------------------------------------------------------------------------
# Run helpers
# ---------------------------------------------------------------------------

def dashboard_url(port: int) -> str:
    return f"http://localhost:{port}/dashboard"


def should_auto_open_browser() -> bool:
    return os.environ.get("AUTOBIZ_NO_BROWSER", "").strip().lower() not in BROWSER_DISABLED_VALUES


def open_browser_later(url: str, delay: float = 1.0) -> None:
    timer = threading.Timer(delay, lambda: webbrowser.open(url, new=2))
    timer.daemon = True
    timer.start()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return redirect(url_for("dashboard"))


@app.route("/dashboard", methods=["GET"])
def dashboard():
    context = dashboard_context(request.args.get("source", ""))
    context["jobs"] = list_run_jobs()
    return render_template("dashboard.html", **context)


@app.route("/jobs/start-scrape", methods=["POST"])
def start_scrape():
    app_cfg = cfg.load_config()
    payload, status_code = start_run_job("scrape", build_scrape_command(app_cfg))
    return jsonify(payload), status_code


@app.route("/jobs/start-score", methods=["POST"])
def start_score():
    if not (cfg.PROJECT_DIR / "data_pa_wide.json").exists():
        return jsonify({"ok": False, "error": "data_pa_wide.json is missing. Run scrape first."}), 400
    app_cfg = cfg.load_config()
    payload, status_code = start_run_job("score", build_score_command(app_cfg))
    return jsonify(payload), status_code


@app.route("/jobs/status", methods=["GET"])
def job_status():
    return jsonify({"ok": True, "jobs": list_run_jobs()})


@app.route("/jobs/log/<job_id>", methods=["GET"])
def job_log(job_id):
    job = get_run_job(job_id)
    if not job:
        return Response("Job not found", status=404, mimetype="text/plain")
    return Response(job.get("log_tail", ""), mimetype="text/plain")


@app.route("/jobs/artifact", methods=["GET"])
def job_artifact():
    path = safe_project_path(request.args.get("path", ""))
    if not path or not path.exists() or not path.is_file():
        return Response("Artifact not found", status=404, mimetype="text/plain")
    return send_file(path)


@app.route("/settings", methods=["GET"])
def settings():
    current = cfg.load_config()

    # Mask stored API keys for display — show last 6 chars only
    def mask(key: str) -> str:
        key = key.strip() if key else ""
        if not key:
            return ""
        return "•" * max(0, len(key) - 6) + key[-6:]

    masked = {
        "scoring_key_masked": mask(current["scoring"].get("api_key", "")),
        "research_key_masked": mask(current["research"].get("api_key", "")),
    }

    # Detect which keys are coming from env vars
    env_status = {}
    for role, provider_key in [("scoring", "scoring"), ("research", "research")]:
        stored = current[role].get("api_key", "").strip()
        provider = current[role].get("provider", "")
        env_var = cfg.ENV_KEY_MAP.get(provider, "")
        env_val = os.environ.get(env_var, "").strip()
        if stored:
            env_status[role] = "config"
        elif env_val:
            env_status[role] = f"env:{env_var}"
        else:
            env_status[role] = "missing"

    config_exists = cfg.CONFIG_PATH.exists()
    last_saved = None
    if config_exists:
        import datetime
        mtime = cfg.CONFIG_PATH.stat().st_mtime
        last_saved = datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")

    return render_template(
        "settings.html",
        cfg=current,
        masked=masked,
        env_status=env_status,
        provider_models=cfg.PROVIDER_MODELS,
        env_key_map=cfg.ENV_KEY_MAP,
        config_exists=config_exists,
        last_saved=last_saved,
    )


@app.route("/save", methods=["POST"])
def save():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"ok": False, "error": "No data received"}), 400

        current = cfg.load_config()

        # Scoring section
        current["scoring"]["provider"] = data.get("scoring_provider", current["scoring"]["provider"])
        current["scoring"]["model"] = data.get("scoring_model", current["scoring"]["model"]).strip()

        # Only update key if a real value was sent (not masked, not empty)
        scoring_key = data.get("scoring_api_key", "").strip()
        if scoring_key and not scoring_key.startswith("•"):
            current["scoring"]["api_key"] = scoring_key

        # Research section
        current["research"]["provider"] = data.get("research_provider", current["research"]["provider"])
        current["research"]["model"] = data.get("research_model", current["research"]["model"]).strip()

        research_key = data.get("research_api_key", "").strip()
        if research_key and not research_key.startswith("•"):
            current["research"]["api_key"] = research_key

        # Defaults section
        try:
            current["defaults"]["location"] = data.get("location", current["defaults"]["location"]).strip()
            current["defaults"]["budget_min"] = int(data.get("budget_min", current["defaults"]["budget_min"]))
            current["defaults"]["budget_max"] = int(data.get("budget_max", current["defaults"]["budget_max"]))
        except (ValueError, TypeError) as e:
            return jsonify({"ok": False, "error": f"Invalid budget value: {e}"}), 400

        cfg.save_config(current)
        return jsonify({"ok": True})

    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/test-key", methods=["POST"])
def test_key():
    try:
        data = request.get_json()
        provider = data.get("provider", "").strip()
        model = data.get("model", "").strip()
        api_key = data.get("api_key", "").strip()

        if not provider or not model:
            return jsonify({"ok": False, "error": "Provider and model are required"})

        # If key is masked or empty, try to resolve from saved config / env
        if not api_key or api_key.startswith("•"):
            current = cfg.load_config()
            # Determine which role this is
            if provider == current["scoring"]["provider"]:
                api_key = current["scoring"].get("api_key", "")
            elif provider == current["research"]["provider"]:
                api_key = current["research"].get("api_key", "")
            # Final fallback: env var
            if not api_key:
                api_key = os.environ.get(cfg.ENV_KEY_MAP.get(provider, ""), "")

        result = cfg.test_connection(provider, model, api_key)
        return jsonify(result)

    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/status", methods=["GET"])
def status():
    """Returns current effective config with masked keys — used by UI on load."""
    current = cfg.load_config()
    out = {
        "scoring": {
            "provider": current["scoring"]["provider"],
            "model": current["scoring"]["model"],
            "has_key": bool(current["scoring"].get("api_key", "").strip()),
        },
        "research": {
            "provider": current["research"]["provider"],
            "model": current["research"]["model"],
            "has_key": bool(current["research"].get("api_key", "").strip()),
        },
        "defaults": current["defaults"],
        "config_file_exists": cfg.CONFIG_PATH.exists(),
    }
    return jsonify(out)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 7860))
    url = dashboard_url(port)
    if should_auto_open_browser():
        open_browser_later(url)
        open_note = "opening in your default browser"
    else:
        open_note = "browser auto-open disabled"
    print(f"\n  autobiz Dashboard\n  {url}\n  {open_note}\n")
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
