"""
app.py — autobiz Settings Web UI

Run with:
    uv run app.py

Opens the dashboard at: http://localhost:7860/dashboard
"""

import json
import os
import threading
import webbrowser
from pathlib import Path

from flask import Flask, jsonify, redirect, render_template, request, url_for

import config as cfg
from listing_utils import as_int, financial_confidence
from proximity import add_proximity_fields

app = Flask(__name__)

PROJECT_DIR = Path(__file__).parent
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
# Dashboard helpers
# ---------------------------------------------------------------------------

def _format_money(value) -> str:
    number = as_int(value)
    return f"${number:,.0f}" if number else "N/A"


def _score_to_tier(score: float | int | None) -> str:
    if score is None:
        return "N/A"
    if score >= 80:
        return "A"
    if score >= 60:
        return "B"
    if score >= 40:
        return "C"
    return "D"


def _latest_scored_file() -> Path | None:
    runs_dir = PROJECT_DIR / "runs"
    if not runs_dir.exists():
        return None
    candidates = sorted(
        [path / "scored.json" for path in runs_dir.iterdir() if (path / "scored.json").exists()],
        key=lambda path: path.parent.name,
        reverse=True,
    )
    return candidates[0] if candidates else None


def _dashboard_sources() -> list[dict]:
    sources = []
    latest = _latest_scored_file()
    if latest:
        sources.append({
            "id": latest.relative_to(PROJECT_DIR).as_posix(),
            "label": f"Latest scored run ({latest.parent.name})",
            "path": latest,
        })

    for filename, label in [
        ("data_pa_wide.json", "PA-wide scraped listings"),
        ("data_philadelphia_pa.json", "Philadelphia scraped listings"),
        ("data_pa.json", "Earlier PA dataset"),
    ]:
        path = PROJECT_DIR / filename
        if path.exists():
            sources.append({"id": filename, "label": label, "path": path})
    return sources


def _load_dashboard_data(path: Path) -> list[dict]:
    try:
        data = json.loads(path.read_text())
    except Exception:
        return []
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        for key in ("results", "listings", "data"):
            value = data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def _coerce_dashboard_row(item: dict, index: int) -> dict:
    row = dict(item)
    add_proximity_fields(row)

    fin = row.get("extracted_financials") or {}
    deal = row.get("deal_structure") or {}
    score = row.get("weighted_score")
    try:
        score = float(score) if score is not None else None
    except (TypeError, ValueError):
        score = None

    confidence = row.get("financial_confidence") or financial_confidence(row)
    asking = row.get("asking_price_usd") or row.get("asking_price") or fin.get("asking_price_usd")
    cash_flow = fin.get("cash_flow_annual") or fin.get("annual_cash_flow") or row.get("cash_flow_annual")
    revenue = fin.get("gross_revenue_annual") or fin.get("annual_revenue") or row.get("gross_revenue_annual")
    distance = row.get("distance_to_philly_miles")
    tier = row.get("tier") or _score_to_tier(score)
    structure = (
        deal.get("structure_summary")
        or row.get("payback_projection")
        or row.get("seller_finance_signal")
        or row.get("seller_motivation")
        or "Needs terms"
    )

    return {
        "index": index,
        "name": row.get("business_name") or row.get("_input_business_name") or "Unknown business",
        "type": row.get("business_type") or "Uncategorized",
        "location": row.get("location") or row.get("_input_location") or "Unknown",
        "source": row.get("_source") or "unknown",
        "source_url": row.get("source_url") or row.get("_input_source_url") or "",
        "score": score,
        "score_display": f"{score:.0f}" if score is not None else "N/A",
        "score_pct": max(0, min(100, score or 0)),
        "tier": tier,
        "tier_class": tier.lower() if tier in {"A", "B", "C", "D"} else "na",
        "distance": distance,
        "distance_display": f"{distance} mi" if distance is not None else "Unknown",
        "distance_pct": max(4, min(100, 100 - ((distance or 250) / 250 * 100))) if distance is not None else 4,
        "proximity_rank": row.get("proximity_rank") or index,
        "bucket": row.get("proximity_bucket") or "unknown distance",
        "asking": _format_money(asking),
        "cash_flow": _format_money(cash_flow),
        "revenue": _format_money(revenue),
        "structure": structure,
        "confidence": confidence,
        "confidence_score": confidence.get("score", 0),
        "confidence_level": confidence.get("level", "unknown"),
        "confidence_reasons": ", ".join(confidence.get("reasons", [])[:3]),
        "estimated": bool(row.get("is_estimated")),
        "summary": row.get("summary") or row.get("description") or "",
    }


def _dashboard_summary(rows: list[dict]) -> dict:
    scored = [row for row in rows if row["score"] is not None]
    distances = [row["distance"] for row in rows if row["distance"] is not None]
    high_confidence = [row for row in rows if row["confidence_score"] >= 80]
    return {
        "total": len(rows),
        "scored": len(scored),
        "avg_score": round(sum(row["score"] for row in scored) / len(scored), 1) if scored else None,
        "closest": min(distances) if distances else None,
        "high_confidence": len(high_confidence),
        "sources": sorted({row["source"] for row in rows}),
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return redirect(url_for("dashboard"))


@app.route("/dashboard", methods=["GET"])
def dashboard():
    sources = _dashboard_sources()
    selected_id = request.args.get("source", "")
    selected = next((source for source in sources if source["id"] == selected_id), sources[0] if sources else None)
    raw_rows = _load_dashboard_data(selected["path"]) if selected else []
    rows = [_coerce_dashboard_row(item, index) for index, item in enumerate(raw_rows, 1)]
    rows.sort(key=lambda row: (
        row["distance"] is None,
        row["distance"] or 10_000,
        -(row["score"] or 0),
        row["name"].lower(),
    ))
    for rank, row in enumerate(rows, 1):
        row["proximity_rank"] = rank

    return render_template(
        "dashboard.html",
        rows=rows,
        summary=_dashboard_summary(rows),
        sources=sources,
        selected_id=selected["id"] if selected else "",
        selected_label=selected["label"] if selected else "No data file found",
    )


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
