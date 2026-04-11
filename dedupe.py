"""Fuzzy duplicate detection for listings gathered from multiple sources."""

from __future__ import annotations

import re
from copy import deepcopy
from urllib.parse import urlparse


GENERIC_NAME_WORDS = {
    "a",
    "an",
    "and",
    "biz",
    "business",
    "company",
    "established",
    "for",
    "franchise",
    "inc",
    "llc",
    "opportunity",
    "pa",
    "pennsylvania",
    "profitable",
    "sale",
    "the",
    "turnkey",
}


def as_int(value) -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    cleaned = "".join(ch for ch in str(value) if ch.isdigit())
    return int(cleaned) if cleaned else None


def normalize_text(value: str) -> str:
    text = (value or "").lower()
    text = text.replace("&", " and ")
    text = re.sub(r"[^a-z0-9\s-]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def name_tokens(name: str) -> set[str]:
    return {
        token
        for token in normalize_text(name).split()
        if len(token) > 2 and token not in GENERIC_NAME_WORDS
    }


def source_host(url: str) -> str:
    try:
        return urlparse(url or "").netloc.lower().removeprefix("www.")
    except Exception:
        return ""


def price_bucket(value) -> int | None:
    number = as_int(value)
    if number is None:
        return None
    return round(number / 10_000)


def location_key(location: str) -> str:
    text = normalize_text(location)
    if not text:
        return ""
    parts = [part.strip() for part in text.split(",") if part.strip()]
    return parts[0][:28] if parts else text[:28]


def token_similarity(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def listing_fingerprint(listing: dict) -> dict:
    asking = listing.get("asking_price") or listing.get("asking_price_usd")
    return {
        "name": normalize_text(listing.get("business_name", "")),
        "tokens": name_tokens(listing.get("business_name", "")),
        "location": location_key(listing.get("location", "")),
        "price_bucket": price_bucket(asking),
        "host": source_host(listing.get("source_url", "")),
    }


def is_probable_duplicate(a: dict, b: dict) -> bool:
    af = listing_fingerprint(a)
    bf = listing_fingerprint(b)
    if af["host"] and af["host"] == bf["host"] and af["name"] and af["name"] == bf["name"]:
        return True

    same_location = af["location"] and af["location"] == bf["location"]
    close_price = (
        af["price_bucket"] is None
        or bf["price_bucket"] is None
        or abs(af["price_bucket"] - bf["price_bucket"]) <= 1
    )
    similarity = token_similarity(af["tokens"], bf["tokens"])
    return same_location and close_price and similarity >= 0.72


def listing_quality_score(listing: dict) -> int:
    score = 0
    for field in ("asking_price", "asking_price_usd", "cash_flow_annual", "gross_revenue_annual"):
        if as_int(listing.get(field)):
            score += 5
    if listing.get("source_url") and listing.get("source_url") != "estimated":
        score += 4
    if listing.get("description"):
        score += min(6, len(str(listing["description"])) // 300)
    if listing.get("seller_motivation"):
        score += 2
    return score


def merge_listings(primary: dict, duplicate: dict) -> dict:
    merged = primary if listing_quality_score(primary) >= listing_quality_score(duplicate) else deepcopy(duplicate)
    other = duplicate if merged is primary else primary

    for key, value in other.items():
        if merged.get(key) in (None, "") and value not in (None, ""):
            merged[key] = value
        elif key == "description" and len(str(value or "")) > len(str(merged.get(key) or "")):
            merged[key] = value

    sources = set(merged.get("_duplicate_sources", []))
    for item in (primary, duplicate):
        source = item.get("_source") or source_host(item.get("source_url", "")) or "unknown"
        sources.add(source)
    merged["_duplicate_sources"] = sorted(sources)
    merged["_duplicate_count"] = max(primary.get("_duplicate_count", 1), duplicate.get("_duplicate_count", 1)) + 1
    merged["_dedupe_note"] = "merged probable duplicate by name, location, and price"
    return merged


def dedupe_listings(listings: list[dict]) -> list[dict]:
    unique: list[dict] = []
    for listing in listings:
        match_index = next(
            (index for index, existing in enumerate(unique) if is_probable_duplicate(existing, listing)),
            None,
        )
        if match_index is None:
            item = deepcopy(listing)
            item["_duplicate_count"] = item.get("_duplicate_count", 1)
            source = item.get("_source") or source_host(item.get("source_url", "")) or "unknown"
            item["_duplicate_sources"] = item.get("_duplicate_sources", [source])
            unique.append(item)
        else:
            unique[match_index] = merge_listings(unique[match_index], listing)
    return unique
