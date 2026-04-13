"""Dashboard data loading and display shaping."""

from __future__ import annotations

import json
import csv
from pathlib import Path
from urllib.parse import urlparse

from listing_utils import as_int, financial_confidence
from proximity import add_proximity_fields


PROJECT_DIR = Path(__file__).parent
UPLOAD_DIR = PROJECT_DIR / "data_uploads"

SOURCE_LABELS = {
    "bizbuysell.com": "BizBuySell",
    "businessbroker.net": "BusinessBroker",
    "bizquest.com": "BizQuest",
    "dealstream.com": "DealStream",
    "craigslist.org": "Craigslist",
    "loopnet.com": "LoopNet",
    "pennbba.com": "PennBBA",
}


def format_money(value) -> str:
    number = as_int(value)
    return f"${number:,.0f}" if number else "N/A"


def score_to_tier(score: float | int | None) -> str:
    if score is None:
        return "N/A"
    if score >= 80:
        return "A"
    if score >= 60:
        return "B"
    if score >= 40:
        return "C"
    return "D"


def source_label_from_url(url: str) -> str:
    host = urlparse(url or "").netloc.lower().removeprefix("www.")
    if not host:
        return ""
    for domain, label in SOURCE_LABELS.items():
        if host == domain or host.endswith(f".{domain}"):
            return label
    parts = host.split(".")
    return parts[-2].replace("-", " ").title() if len(parts) >= 2 else host.title()


def latest_scored_file(project_dir: Path = PROJECT_DIR) -> Path | None:
    runs_dir = project_dir / "runs"
    if not runs_dir.exists():
        return None
    candidates = sorted(
        [path / "scored.json" for path in runs_dir.iterdir() if (path / "scored.json").exists()],
        key=lambda path: path.parent.name,
        reverse=True,
    )
    return candidates[0] if candidates else None


def dashboard_sources(project_dir: Path = PROJECT_DIR) -> list[dict]:
    sources = []
    latest = latest_scored_file(project_dir)
    if latest:
        sources.append({
            "id": latest.relative_to(project_dir).as_posix(),
            "label": f"Latest scored run ({latest.parent.name})",
            "path": latest,
        })

    for filename, label in [
        ("data_pa_wide.json", "PA-wide scraped listings"),
        ("data_philadelphia_pa.json", "Philadelphia scraped listings"),
        ("data_pa.json", "Earlier PA dataset"),
    ]:
        path = project_dir / filename
        if path.exists():
            sources.append({"id": filename, "label": label, "path": path})

    upload_dir = project_dir / "data_uploads"
    if upload_dir.exists():
        uploads = sorted(
            [
                path for path in upload_dir.iterdir()
                if path.is_file() and path.suffix.lower() in {".json", ".csv"}
            ],
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        for path in uploads:
            sources.append({
                "id": path.relative_to(project_dir).as_posix(),
                "label": f"Uploaded: {path.name}",
                "path": path,
            })
    return sources


def load_dashboard_data(path: Path) -> list[dict]:
    if path.suffix.lower() == ".csv":
        try:
            with path.open(newline="", encoding="utf-8") as handle:
                return [dict(row) for row in csv.DictReader(handle)]
        except Exception:
            return []

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


def coerce_dashboard_row(item: dict, index: int) -> dict:
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
    confidence.setdefault("provenance", financial_confidence(row).get("provenance", {}))
    provenance = confidence.get("provenance", {})
    asking = row.get("asking_price_usd") or row.get("asking_price") or fin.get("asking_price_usd")
    cash_flow = fin.get("cash_flow_annual") or fin.get("annual_cash_flow") or row.get("cash_flow_annual")
    revenue = fin.get("gross_revenue_annual") or fin.get("annual_revenue") or row.get("gross_revenue_annual")
    distance = row.get("distance_to_philly_miles")
    tier = row.get("tier") or score_to_tier(score)
    source_url = row.get("source_url") or row.get("_input_source_url") or ""
    source = row.get("_source") or row.get("source") or source_label_from_url(source_url) or "No source"
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
        "source": source,
        "source_url": source_url,
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
        "asking": format_money(asking),
        "cash_flow": format_money(cash_flow),
        "revenue": format_money(revenue),
        "structure": structure,
        "confidence": confidence,
        "confidence_score": confidence.get("score", 0),
        "confidence_level": confidence.get("level", "unknown"),
        "confidence_reasons": ", ".join(confidence.get("reasons", [])[:3]),
        "provenance": provenance,
        "provenance_summary": ", ".join(f"{key}: {value}" for key, value in provenance.items()),
        "estimated": bool(row.get("is_estimated")),
        "duplicate_count": row.get("_duplicate_count", 1),
        "duplicate_sources": ", ".join(row.get("_duplicate_sources", [])),
        "summary": row.get("summary") or row.get("description") or "",
    }


def rows_for_items(items: list[dict]) -> list[dict]:
    rows = [coerce_dashboard_row(item, index) for index, item in enumerate(items, 1)]
    rows.sort(key=lambda row: (
        row["distance"] is None,
        row["distance"] or 10_000,
        -(row["score"] or 0),
        row["name"].lower(),
    ))
    for rank, row in enumerate(rows, 1):
        row["proximity_rank"] = rank
    return rows


def dashboard_summary(rows: list[dict]) -> dict:
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


def dashboard_context(selected_id: str = "", project_dir: Path = PROJECT_DIR) -> dict:
    sources = dashboard_sources(project_dir)
    selected = next((source for source in sources if source["id"] == selected_id), sources[0] if sources else None)
    raw_rows = load_dashboard_data(selected["path"]) if selected else []
    rows = rows_for_items(raw_rows)
    return {
        "rows": rows,
        "summary": dashboard_summary(rows),
        "sources": sources,
        "selected_id": selected["id"] if selected else "",
        "selected_label": selected["label"] if selected else "No data file found",
    }
