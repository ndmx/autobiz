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

import argparse
import csv
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlencode, quote_plus

import requests
from bs4 import BeautifulSoup
from xai_sdk import Client as XaiClient
from xai_sdk.chat import user as xai_user

GROK_MODEL = "grok-4.20-multi-agent-latest"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# Keywords that suggest a real operating business (not equipment/vehicles)
BUSINESS_KEYWORDS = [
    "cash flow", "revenue", "established", "turnkey", "annual", "gross",
    "net income", "retiring", "retirement", "customers", "clients",
    "lease", "employees", "business for sale", "owner retiring",
    "profitable", "absentee", "semi-absentee", "route", "laundromat",
    "franchise", "years in business", "years old",
]

# Keywords that suggest it's NOT a business listing
EXCLUDE_KEYWORDS = [
    "jeep", "truck", "van", "vehicle", "car ", "suv", "trailer",
    "equipment only", "machinery", "job opportunity", "hiring",
    "work from home", "mlm", "opportunity to earn",
    "laminator", "laminating", "paper cutter", "shredder", "folder inserter",
    "tabber", "feeder", "copier", "printer", "laser cutter", "engraver",
    "freezer", "compressor", "generator", "pump ", "excavator", "skid steer",
    "forklift", "pallet", "conveyor", "oven ", "griddle", "fryer",
    "espresso machine", "ice cream machine", "hvac", "lift ", "ascender",
    "c-corp", "aged corporation", "shelf corporation", "shell company",
    "spectrometer", "ellipsometer", "pcr system", "microscope",
    "sonic", "welder", "grinder", "cutter", "router ",
]


# ---------------------------------------------------------------------------
# Craigslist scraper
# ---------------------------------------------------------------------------

# Map common city names to Craigslist subdomains + nearby cities to include
CRAIGSLIST_SITES = {
    "philadelphia": ["philadelphia", "southjersey", "delaware"],
    "pennsylvania": [
        "philadelphia", "pittsburgh", "allentown", "harrisburg",
        "lancaster", "reading", "scranton", "statecollegepenn",
        "lehighvalley", "poconos", "williamsport", "york",
        "southjersey", "delaware",  # Philly metro overflow
    ],
    "default": ["philadelphia"],
}


def get_cl_sites(location: str) -> list[str]:
    loc = location.lower()
    for key, sites in CRAIGSLIST_SITES.items():
        if key in loc:
            return sites
    return ["philadelphia"]  # default


def scrape_craigslist_index(subdomain: str, max_price: int, verbose: bool, min_price: int = 1000) -> list[dict]:
    """Fetch the bfs (business for sale) listing index for a CL subdomain."""
    url = (
        f"https://{subdomain}.craigslist.org/search/bfs"
        f"?min_price={min_price}&max_price={max_price}&query=business"
    )
    if verbose:
        print(f"  [CL] {subdomain}: {url}")
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
    except Exception as e:
        print(f"  [CL] {subdomain} error: {e}")
        return []

    soup = BeautifulSoup(r.text, "html.parser")
    items = soup.select("li.cl-static-search-result")

    listings = []
    for item in items:
        title_el = item.select_one(".title")
        price_el = item.select_one(".price")
        loc_el = item.select_one(".location")
        link_el = item.select_one("a")

        title = title_el.text.strip() if title_el else ""
        price_raw = price_el.text.strip() if price_el else ""
        location = loc_el.text.strip() if loc_el else subdomain
        href = link_el["href"] if link_el else ""

        # Filter out obvious non-business listings
        title_lower = title.lower()
        if any(kw in title_lower for kw in EXCLUDE_KEYWORDS):
            continue

        # Parse price
        price_num = None
        m = re.search(r"[\d,]+", price_raw.replace(",", ""))
        if m:
            try:
                price_num = int(m.group().replace(",", ""))
            except ValueError:
                pass

        listings.append({
            "title": title,
            "price_raw": price_raw,
            "asking_price": price_num,
            "location": location,
            "url": href,
            "source": f"craigslist/{subdomain}",
        })

    return listings


def fetch_craigslist_detail(url: str, verbose: bool) -> dict:
    """Fetch full text from a Craigslist listing detail page."""
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        title = ""
        for sel in ["#titletextonly", "h1.postingtitle", "title"]:
            el = soup.select_one(sel)
            if el:
                title = el.text.strip()
                break

        price = ""
        price_el = soup.select_one(".price")
        if price_el:
            price = price_el.text.strip()

        body = ""
        body_el = soup.select_one("#postingbody")
        if body_el:
            # Remove the QR code notice
            for tag in body_el.select(".print-information"):
                tag.decompose()
            body = body_el.get_text(separator=" ").strip()

        # Extract posted date
        date = ""
        date_el = soup.select_one("time.date")
        if date_el:
            date = date_el.get("datetime", "")[:10]

        return {
            "title": title,
            "price": price,
            "description": body,
            "listing_date": date,
        }
    except Exception as e:
        if verbose:
            print(f"    Detail fetch error: {e}")
        return {}


def is_real_business(title: str, description: str, price: int = None) -> bool:
    """Heuristic: does this look like an actual operating business listing?"""
    text = (title + " " + description).lower()
    if any(kw in text for kw in EXCLUDE_KEYWORDS):
        return False
    # Price sanity: equipment/parts typically cheap, businesses typically >$3k
    if price and price < 3000:
        return False
    score = sum(1 for kw in BUSINESS_KEYWORDS if kw in text)
    return score >= 2


def scrape_craigslist(location: str, max_price: int, verbose: bool, min_price: int = 1000) -> list[dict]:
    """Full Craigslist pipeline: index → detail pages → filtered results."""
    sites = get_cl_sites(location)
    print(f"  [CL] Scanning {len(sites)} Craigslist site(s): {', '.join(sites)}")

    raw = []
    for site in sites:
        raw.extend(scrape_craigslist_index(site, max_price, verbose, min_price=min_price))
        time.sleep(0.5)

    print(f"  [CL] Raw listings: {len(raw)} — fetching detail pages...")

    results = []
    for i, listing in enumerate(raw):
        if not listing.get("url"):
            continue
        detail = fetch_craigslist_detail(listing["url"], verbose)
        if not detail.get("description"):
            continue

        full_desc = detail.get("description", "")
        title = detail.get("title") or listing["title"]

        # Parse price from detail page if missing
        asking = listing.get("asking_price")
        if not asking and detail.get("price"):
            m = re.search(r"[\d,]+", detail["price"].replace(",", ""))
            if m:
                try:
                    asking = int(m.group().replace(",", ""))
                except ValueError:
                    pass

        if not is_real_business(title, full_desc, price=asking):
            if verbose:
                print(f"    skip (not a business): {title[:60]}")
            continue

        results.append({
            "business_name": title,
            "business_type": "",          # will be inferred by Claude
            "asking_price": asking,
            "location": listing.get("location", location),
            "cash_flow_annual": None,
            "gross_revenue_annual": None,
            "year_established": None,
            "employees": "",
            "description": full_desc[:2000],
            "seller_motivation": "",
            "source_url": listing["url"],
            "listing_date": detail.get("listing_date", ""),
            "_source": listing["source"],
        })
        time.sleep(0.3)

    print(f"  [CL] Real business listings: {len(results)}")
    return results


# ---------------------------------------------------------------------------
# BizBuySell + BusinessBroker via Grok browser proxy
# ---------------------------------------------------------------------------

GROK_SCRAPE_PROMPT = """You have live web access. Please browse to the following URL right now and extract all business listings you find on that page.

URL: {url}

For each listing found on that page, extract:
- business name or description
- asking price (number)
- location (city, state)
- annual cash flow or net income (if shown)
- annual gross revenue (if shown)
- brief description
- the direct URL to the full listing detail page
- any mention of why the owner is selling

Return ONLY a valid JSON array with this schema (no markdown):
[
  {{
    "business_name": "string",
    "business_type": "string",
    "asking_price": number or null,
    "location": "string",
    "cash_flow_annual": number or null,
    "gross_revenue_annual": number or null,
    "year_established": null,
    "employees": "",
    "description": "string — include all text you can see from the listing card",
    "seller_motivation": "string",
    "source_url": "string — the full listing URL",
    "listing_date": ""
  }}
]

If the page is blocked, returns an error, or has no listings, return an empty array: []
Do NOT generate hypothetical listings — only return what you actually see on the page.
"""


def grok_scrape_url(grok: XaiClient, url: str, label: str, verbose: bool) -> list[dict]:
    """Use Grok's live web access to scrape a listing page that blocks direct requests."""
    if verbose:
        print(f"  [Grok→{label}] {url[:80]}")
    try:
        chat = grok.chat.create(model=GROK_MODEL)
        chat.append(xai_user(GROK_SCRAPE_PROMPT.format(url=url)))
        raw = chat.sample().content.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:].strip()

        data = json.loads(raw)
        if isinstance(data, list):
            # Tag source and mark as real (came from actual URL)
            for item in data:
                if not item.get("source_url"):
                    item["source_url"] = url
                item["_source"] = label
            print(f"  [Grok→{label}] Found {len(data)} listings")
            return data
        return []
    except json.JSONDecodeError:
        if verbose:
            print(f"  [Grok→{label}] JSON parse error — no listings extracted")
        return []
    except Exception as e:
        print(f"  [Grok→{label}] Error: {e}")
        return []


def build_bizbuysell_urls(location: str, max_price: int, min_price: int = 0) -> list[tuple[str, str]]:
    """Generate BizBuySell + BusinessBroker search URLs for the target location."""
    loc_enc = quote_plus(location)
    is_statewide = "pennsylvania" in location.lower() and "," not in location.lower()
    price_filter = f"min_price={min_price}&max_price={max_price}" if min_price else f"max_price={max_price}"

    base_urls = [
        (
            "BizBuySell-PA-retiring",
            f"https://www.bizbuysell.com/pennsylvania-businesses-for-sale/?{price_filter}&q=retiring+seller+financing",
        ),
        (
            "BizBuySell-PA-seller-finance",
            f"https://www.bizbuysell.com/pennsylvania-businesses-for-sale/?{price_filter}&q=seller+financing+owner+will+carry",
        ),
        (
            "BizBuySell-PA-routes",
            f"https://www.bizbuysell.com/pennsylvania-businesses-for-sale/?{price_filter}&q=route+vending+laundromat",
        ),
        (
            "BizBuySell-PA-service",
            f"https://www.bizbuysell.com/pennsylvania-businesses-for-sale/?{price_filter}&q=cleaning+service+established",
        ),
        (
            "BizBuySell-PA-food",
            f"https://www.bizbuysell.com/pennsylvania-businesses-for-sale/?{price_filter}&q=restaurant+deli+cafe+retiring",
        ),
        (
            "BizBuySell-PA-retail",
            f"https://www.bizbuysell.com/pennsylvania-businesses-for-sale/?{price_filter}&q=retail+store+established+owner",
        ),
        (
            "BusinessBroker-PA",
            f"https://www.businessbroker.net/businesses-for-sale/pennsylvania/?MinPrice={min_price}&MaxPrice={max_price}",
        ),
        (
            "BusinessBroker-PA-p2",
            f"https://www.businessbroker.net/businesses-for-sale/pennsylvania/?MinPrice={min_price}&MaxPrice={max_price}&Page=2",
        ),
        (
            "BizBuySell-PA-all",
            f"https://www.bizbuysell.com/pennsylvania-businesses-for-sale/?{price_filter}",
        ),
    ]

    if not is_statewide:
        # Add location-specific searches
        base_urls.insert(0, (
            f"BizBuySell-{location.split()[0]}",
            f"https://www.bizbuysell.com/businesses-for-sale/?q={loc_enc}&{price_filter}",
        ))

    return base_urls


# ---------------------------------------------------------------------------
# Deduplication + normalization
# ---------------------------------------------------------------------------

def normalize_listings(listings: list[dict]) -> list[dict]:
    """Clean and deduplicate by title+price fingerprint."""
    seen = set()
    clean = []
    for item in listings:
        name = (item.get("business_name") or "").strip()[:60].lower()
        price = item.get("asking_price") or 0
        key = f"{name}:{price}"
        if key in seen or not name:
            continue
        seen.add(key)

        # Ensure required fields exist
        item.setdefault("business_type", "")
        item.setdefault("cash_flow_annual", None)
        item.setdefault("gross_revenue_annual", None)
        item.setdefault("year_established", None)
        item.setdefault("employees", "")
        item.setdefault("seller_motivation", "")
        item.setdefault("listing_date", "")
        clean.append(item)
    return clean


# ---------------------------------------------------------------------------
# Output writers
# ---------------------------------------------------------------------------

CSV_FIELDS = [
    "business_name", "business_type", "asking_price", "location",
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
    parser.add_argument("--location", default="Philadelphia PA", help="Target location (default: Philadelphia PA)")
    parser.add_argument("--budget", type=int, default=250000, help="Max asking price filter (default: 250000)")
    parser.add_argument("--min-budget", type=int, default=50000, help="Min asking price filter (default: 50000)")
    parser.add_argument("--output", type=str, default=None, help="Save as CSV (for analyze.py)")
    parser.add_argument("--json", type=str, default=None, dest="json_out", help="Save as JSON (for agent pipeline)")
    parser.add_argument("--no-grok", action="store_true", help="Skip Grok-proxied sources (CL only)")
    parser.add_argument("--no-craigslist", action="store_true", help="Skip Craigslist")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    xai_key = os.environ.get("XAI_API_KEY")
    grok = None
    if not args.no_grok:
        if not xai_key:
            print("Warning: XAI_API_KEY not set — skipping Grok-proxied sources (use --no-grok to suppress)")
        else:
            grok = XaiClient(api_key=xai_key)

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
        for label, url in build_bizbuysell_urls(args.location, args.budget, min_price=args.min_budget):
            batch = grok_scrape_url(grok, url, label, args.verbose)
            all_listings.extend(batch)
            time.sleep(0.8)

    # --- Normalize + dedup ---
    listings = normalize_listings(all_listings)
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
    print(f"\n{'#':<4} {'Title':<50} {'Price':<10} {'Source':<20}")
    print("-" * 90)
    for i, l in enumerate(listings[:20], 1):
        price = f"${l['asking_price']:,}" if l.get("asking_price") else "N/A"
        name = (l.get("business_name") or "")[:48]
        src = (l.get("_source") or "")[:18]
        print(f"{i:<4} {name:<50} {price:<10} {src}")
    if len(listings) > 20:
        print(f"  ... and {len(listings) - 20} more")


if __name__ == "__main__":
    main()
