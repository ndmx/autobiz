"""Shared listing normalization helpers for scraper and agent workflows."""

from __future__ import annotations

from collections import Counter
from typing import Any

from proximity import add_proximity_fields, assign_proximity_ranks


LISTING_METADATA_FIELDS = [
    "business_type",
    "asking_price",
    "location",
    "city",
    "county",
    "distance_to_philly_miles",
    "proximity_bucket",
    "proximity_rank",
    "seller_motivation",
    "source_url",
    "listing_date",
    "_source",
]


def as_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    cleaned = "".join(ch for ch in str(value) if ch.isdigit())
    return int(cleaned) if cleaned else None


def in_price_range(item: dict, min_budget: int, max_budget: int) -> bool:
    asking = as_int(item.get("asking_price") or item.get("asking_price_usd"))
    return asking is None or min_budget <= asking <= max_budget


def enrich_listing_for_philly(item: dict) -> dict:
    add_proximity_fields(item)
    return item


def filter_and_rank_listings(listings: list[dict], min_budget: int, max_budget: int) -> list[dict]:
    filtered = [item for item in listings if in_price_range(item, min_budget, max_budget)]
    for item in filtered:
        enrich_listing_for_philly(item)
    return assign_proximity_ranks(filtered)


def attach_listing_metadata(result: dict, listing: dict) -> dict:
    """Keep source/proximity facts that are not part of the LLM JSON schema."""
    for field in LISTING_METADATA_FIELDS:
        value = listing.get(field)
        if value not in (None, "") and result.get(field) in (None, ""):
            result[field] = value

    result["_source"] = result.get("_source") or listing.get("_source", "")
    result["_input_business_name"] = listing.get("business_name", "")
    result["_input_location"] = listing.get("location", "")
    result["_input_source_url"] = listing.get("source_url", "")
    add_proximity_fields(result)
    return result


def assign_result_proximity_ranks(results: list[dict]) -> list[dict]:
    good = [r for r in results if "error" not in r]
    assign_proximity_ranks(good)
    return results


def source_breakdown(items: list[dict]) -> dict[str, int]:
    return dict(Counter(item.get("_source") or "unknown" for item in items))


def proximity_breakdown(items: list[dict]) -> dict[str, int]:
    return dict(Counter(item.get("proximity_bucket") or "unknown distance" for item in items))
