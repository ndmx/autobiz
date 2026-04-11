"""Craigslist business-for-sale adapter."""

from __future__ import annotations

import re
import time

import requests
from bs4 import BeautifulSoup


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


BUSINESS_KEYWORDS = [
    "cash flow", "revenue", "established", "turnkey", "annual", "gross",
    "net income", "retiring", "retirement", "customers", "clients",
    "lease", "employees", "business for sale", "owner retiring",
    "profitable", "absentee", "semi-absentee", "route", "laundromat",
    "franchise", "years in business", "years old",
]


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


CRAIGSLIST_SITES = {
    "philadelphia": ["philadelphia", "southjersey", "delaware"],
    "pennsylvania": [
        "philadelphia", "pittsburgh", "allentown", "harrisburg",
        "lancaster", "reading", "scranton", "pennstate",
        "poconos", "williamsport", "york",
        "southjersey", "delaware",
    ],
    "default": ["philadelphia"],
}


def get_cl_sites(location: str) -> list[str]:
    loc = location.lower()
    for key, sites in CRAIGSLIST_SITES.items():
        if key in loc:
            return sites
    return ["philadelphia"]


def parse_price(value: str) -> int | None:
    match = re.search(r"[\d,]+", (value or "").replace(",", ""))
    if not match:
        return None
    try:
        return int(match.group().replace(",", ""))
    except ValueError:
        return None


def scrape_craigslist_index(subdomain: str, max_price: int, verbose: bool, min_price: int = 1000) -> list[dict]:
    url = (
        f"https://{subdomain}.craigslist.org/search/bfs"
        f"?min_price={min_price}&max_price={max_price}&query=business"
    )
    if verbose:
        print(f"  [CL] {subdomain}: {url}")
    try:
        response = requests.get(url, headers=HEADERS, timeout=15)
        response.raise_for_status()
    except Exception as e:
        print(f"  [CL] {subdomain} error: {e}")
        return []

    soup = BeautifulSoup(response.text, "html.parser")
    listings = []
    for item in soup.select("li.cl-static-search-result"):
        title_el = item.select_one(".title")
        price_el = item.select_one(".price")
        loc_el = item.select_one(".location")
        link_el = item.select_one("a")

        title = title_el.text.strip() if title_el else ""
        title_lower = title.lower()
        if any(kw in title_lower for kw in EXCLUDE_KEYWORDS):
            continue

        listings.append({
            "title": title,
            "price_raw": price_el.text.strip() if price_el else "",
            "asking_price": parse_price(price_el.text if price_el else ""),
            "location": loc_el.text.strip() if loc_el else subdomain,
            "url": link_el["href"] if link_el else "",
            "source": f"craigslist/{subdomain}",
        })

    return listings


def fetch_craigslist_detail(url: str, verbose: bool) -> dict:
    try:
        response = requests.get(url, headers=HEADERS, timeout=15)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        title = ""
        for selector in ["#titletextonly", "h1.postingtitle", "title"]:
            el = soup.select_one(selector)
            if el:
                title = el.text.strip()
                break

        price_el = soup.select_one(".price")
        body_el = soup.select_one("#postingbody")
        body = ""
        if body_el:
            for tag in body_el.select(".print-information"):
                tag.decompose()
            body = body_el.get_text(separator=" ").strip()

        date_el = soup.select_one("time.date")
        return {
            "title": title,
            "price": price_el.text.strip() if price_el else "",
            "description": body,
            "listing_date": date_el.get("datetime", "")[:10] if date_el else "",
        }
    except Exception as e:
        if verbose:
            print(f"    Detail fetch error: {e}")
        return {}


def is_real_business(title: str, description: str, price: int = None) -> bool:
    text = (title + " " + description).lower()
    if any(kw in text for kw in EXCLUDE_KEYWORDS):
        return False
    if price and price < 3000:
        return False
    score = sum(1 for kw in BUSINESS_KEYWORDS if kw in text)
    return score >= 2


def scrape_craigslist(location: str, max_price: int, verbose: bool, min_price: int = 1000) -> list[dict]:
    sites = get_cl_sites(location)
    print(f"  [CL] Scanning {len(sites)} Craigslist site(s): {', '.join(sites)}")

    raw = []
    for site in sites:
        raw.extend(scrape_craigslist_index(site, max_price, verbose, min_price=min_price))
        time.sleep(0.5)

    print(f"  [CL] Raw listings: {len(raw)} — fetching detail pages...")

    results = []
    for listing in raw:
        if not listing.get("url"):
            continue
        detail = fetch_craigslist_detail(listing["url"], verbose)
        if not detail.get("description"):
            continue

        title = detail.get("title") or listing["title"]
        asking = listing.get("asking_price") or parse_price(detail.get("price", ""))
        description = detail.get("description", "")

        if not is_real_business(title, description, price=asking):
            if verbose:
                print(f"    skip (not a business): {title[:60]}")
            continue

        results.append({
            "business_name": title,
            "business_type": "",
            "asking_price": asking,
            "location": listing.get("location", location),
            "cash_flow_annual": None,
            "gross_revenue_annual": None,
            "year_established": None,
            "employees": "",
            "description": description[:2000],
            "seller_motivation": "",
            "source_url": listing["url"],
            "listing_date": detail.get("listing_date", ""),
            "_source": listing["source"],
        })
        time.sleep(0.3)

    print(f"  [CL] Real business listings: {len(results)}")
    return results
