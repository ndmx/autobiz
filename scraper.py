"""
scraper.py — Real Business Listing Scraper

Pulls actual live listings from multiple sources for a given location:
  - Craigslist (direct HTML scrape — works without auth)
  - BizBuySell (via Grok browser proxy — bypasses 403)
  - BusinessBroker.net (via Grok browser proxy)

Outputs a CSV ready for analyze.py OR JSON for the agent pipeline.

Usage:
    uv run scraper.py --location "Philadelphia PA" --budget 50000
    uv run scraper.py --location "Philadelphia PA" --budget 50000 --output philly.csv
    uv run scraper.py --location "Philadelphia PA" --budget 50000 --json philly.json
    uv run scraper.py --location "Philadelphia PA" --radius 50  # include suburbs
"""

from __future__ import annotations

import argparse
import csv
import json
import time

from source_adapters.craigslist import scrape_craigslist
from source_adapters.grok_pages import build_grok_source_urls, grok_scrape_url

from dedupe import dedupe_listings
from listing_utils import financial_confidence
from proximity import add_proximity_fields, assign_proximity_ranks

# ---------------------------------------------------------------------------
# Deduplication + normalization
# ---------------------------------------------------------------------------

def normalize_listings(listings: list[dict]) -> list[dict]:
    """Clean placeholders and deduplicate likely cross-source repeats."""
    clean = []
    for item in listings:
        name = (item.get("business_name") or "").strip()[:60].lower()
        description = (item.get("description") or "").strip().lower()
        source_url = (item.get("source_url") or "").strip().lower()
        if (
            name in {"string", "business name", "name or description"}
            or source_url.startswith("string ")
            or "include all text you can see from the listing card" in description
        ):
            continue
        if not name:
            continue

        # Ensure required fields exist
        item.setdefault("business_type", "")
        item.setdefault("cash_flow_annual", None)
        item.setdefault("gross_revenue_annual", None)
        item.setdefault("year_established", None)
        item.setdefault("employees", "")
        item.setdefault("seller_motivation", "")
        item.setdefault("listing_date", "")
        add_proximity_fields(item)
        item["financial_confidence"] = financial_confidence(item)
        clean.append(item)
    return assign_proximity_ranks(dedupe_listings(clean))


def as_int(value) -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    cleaned = "".join(ch for ch in str(value) if ch.isdigit())
    return int(cleaned) if cleaned else None


def filter_by_budget(listings: list[dict], min_price: int, max_price: int) -> list[dict]:
    filtered = []
    for item in listings:
        asking = as_int(item.get("asking_price"))
        if asking is None or min_price <= asking <= max_price:
            filtered.append(item)
    return assign_proximity_ranks(filtered)


# ---------------------------------------------------------------------------
# Output writers
# ---------------------------------------------------------------------------

CSV_FIELDS = [
    "business_name", "business_type", "asking_price", "location", "city", "county",
    "distance_to_philly_miles", "proximity_bucket", "proximity_rank",
    "cash_flow_annual", "gross_revenue_annual", "year_established",
    "employees", "description", "seller_motivation",
    "source_url", "listing_date", "_source",
]


def write_csv(listings: list[dict], path: str):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(listings)
    print(f"CSV saved → {path}  ({len(listings)} listings)")


def write_json(listings: list[dict], path: str):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(listings, f, indent=2)
    print(f"JSON saved → {path}  ({len(listings)} listings)")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="autobiz scraper — real listing data from multiple sources")
    parser.add_argument("--location", default=None, help="Target location (default: saved config, usually Pennsylvania)")
    parser.add_argument("--budget", type=int, default=None, help="Max asking price filter (default: saved config)")
    parser.add_argument("--min-budget", type=int, default=None, help="Min asking price filter (default: saved config)")
    parser.add_argument("--output", type=str, default=None, help="Save as CSV (for analyze.py)")
    parser.add_argument("--json", type=str, default=None, dest="json_out", help="Save as JSON (for agent pipeline)")
    parser.add_argument("--no-grok", action="store_true", help="Skip Grok-proxied sources (CL only)")
    parser.add_argument("--no-craigslist", action="store_true", help="Skip Craigslist")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    from config import load_config
    app_cfg = load_config()
    args.location = args.location or app_cfg["defaults"].get("location", "Pennsylvania")
    args.budget = args.budget if args.budget is not None else int(app_cfg["defaults"].get("budget_max", 250000))
    args.min_budget = args.min_budget if args.min_budget is not None else int(app_cfg["defaults"].get("budget_min", 75000))

    grok = None
    if not args.no_grok:
        from config import get_research_client
        try:
            grok = get_research_client(app_cfg)
        except ValueError as e:
            print(f"Warning: {e} — skipping Grok-proxied sources (use --no-grok to suppress)")

    print("=" * 64)
    print(f"  autobiz scraper")
    print(f"  Location: {args.location}  |  Price range: ${args.min_budget:,}–${args.budget:,}")
    print("=" * 64)

    all_listings: list[dict] = []

    # --- Craigslist ---
    if not args.no_craigslist:
        print("\n[ Craigslist ]")
        cl = scrape_craigslist(args.location, args.budget + 10000, args.verbose, min_price=args.min_budget)
        all_listings.extend(cl)

    # --- BizBuySell + BusinessBroker via Grok ---
    if grok:
        print("\n[ Grok-proxied sources ]")
        for label, url in build_grok_source_urls(args.location, args.budget, min_price=args.min_budget):
            batch = grok_scrape_url(grok, url, label, args.verbose)
            all_listings.extend(batch)
            time.sleep(0.8)

    # --- Normalize + dedup ---
    listings = filter_by_budget(normalize_listings(all_listings), args.min_budget, args.budget)
    print(f"\n  Total unique listings: {len(listings)}")

    # --- Source breakdown ---
    from collections import Counter
    sources = Counter(item.get("_source", "unknown") for item in listings)
    for src, count in sources.most_common():
        print(f"    {src}: {count}")

    if not listings:
        print("\nNo listings found. Try adjusting --location or --budget.")
        return

    # --- Default outputs ---
    if not args.output and not args.json_out:
        # Auto-name based on location
        slug = args.location.lower().replace(" ", "_").replace(",", "")
        args.output = f"data_{slug}.csv"
        args.json_out = f"data_{slug}.json"

    if args.output:
        write_csv(listings, args.output)
    if args.json_out:
        write_json(listings, args.json_out)

    # Print quick preview
    print(f"\n{'#':<4} {'Title':<44} {'Price':<10} {'Miles':<7} {'Source':<20}")
    print("-" * 92)
    for i, l in enumerate(listings[:20], 1):
        price = f"${l['asking_price']:,}" if l.get("asking_price") else "N/A"
        name = (l.get("business_name") or "")[:42]
        miles = l.get("distance_to_philly_miles")
        miles = str(miles) if miles is not None else "?"
        src = (l.get("_source") or "")[:18]
        print(f"{i:<4} {name:<44} {price:<10} {miles:<7} {src}")
    if len(listings) > 20:
        print(f"  ... and {len(listings) - 20} more")


if __name__ == "__main__":
    main()
