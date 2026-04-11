"""Shared listing normalization helpers for scraper and agent workflows."""

from __future__ import annotations

from collections import Counter
from typing import Any

from proximity import add_proximity_fields, assign_proximity_ranks


LISTING_METADATA_FIELDS = [
    "business_type",
    "asking_price",
    "asking_price_usd",
    "cash_flow_annual",
    "gross_revenue_annual",
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


def financial_value_present(value: Any) -> bool:
    """Return True when a listing has a usable positive financial value."""
    number = as_int(value)
    return number is not None and number > 0


def financial_confidence(item: dict) -> dict:
    """
    Score how much hard financial evidence a listing has.

    This is intentionally separate from deal quality. A high-confidence bad
    deal is still bad, but a low-confidence deal should not rank highly until
    cash flow, revenue, and asking-price facts are verified.
    """
    fin = item.get("extracted_financials") or {}
    reasons: list[str] = []
    score = 0

    ask = item.get("asking_price_usd") or item.get("asking_price") or fin.get("asking_price_usd")
    cash_flow = (
        fin.get("cash_flow_annual")
        or fin.get("annual_cash_flow")
        or item.get("cash_flow_annual")
        or item.get("annual_cash_flow")
    )
    revenue = (
        fin.get("gross_revenue_annual")
        or fin.get("annual_revenue")
        or item.get("gross_revenue_annual")
        or item.get("annual_revenue")
    )
    source_url = item.get("source_url", "")
    seller_note = item.get("seller_motivation") or item.get("seller_finance_signal") or item.get("boomer_signal")
    is_estimated = item.get("is_estimated") or source_url in {"estimated", "market estimate"}

    if financial_value_present(ask):
        score += 25
        reasons.append("asking price present")
    else:
        reasons.append("asking price missing")

    if financial_value_present(cash_flow):
        score += 35
        reasons.append("cash flow present")
    else:
        reasons.append("cash flow missing")

    if financial_value_present(revenue):
        score += 20
        reasons.append("revenue present")
    else:
        reasons.append("revenue missing")

    if seller_note and str(seller_note).strip().lower() not in {"none", "none detected", "n/a"}:
        score += 10
        reasons.append("seller motivation signal")

    if source_url and source_url not in {"estimated", "market estimate"} and not item.get("is_estimated"):
        score += 10
        reasons.append("source URL present")
    elif is_estimated:
        reasons.append("estimated or unverified source")
    else:
        reasons.append("source URL missing")

    if is_estimated:
        score = min(score, 45)
    elif not source_url:
        score = min(score, 70)

    score = min(100, score)
    if score >= 80:
        level = "high"
    elif score >= 55:
        level = "medium"
    elif score >= 30:
        level = "low"
    else:
        level = "very_low"

    return {"score": score, "level": level, "reasons": reasons}


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
    result["financial_confidence"] = financial_confidence({**listing, **result})
    return result


def assign_result_proximity_ranks(results: list[dict]) -> list[dict]:
    good = [r for r in results if "error" not in r]
    assign_proximity_ranks(good)
    return results


def source_breakdown(items: list[dict]) -> dict[str, int]:
    return dict(Counter(item.get("_source") or "unknown" for item in items))


def proximity_breakdown(items: list[dict]) -> dict[str, int]:
    return dict(Counter(item.get("proximity_bucket") or "unknown distance" for item in items))
