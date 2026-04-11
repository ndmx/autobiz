"""BizQuest URL builder for Grok-backed extraction."""

from __future__ import annotations


def build_urls(location: str, max_price: int, min_price: int = 0) -> list[tuple[str, str]]:
    loc_lower = location.lower()
    is_statewide = ("pennsylvania" in loc_lower or loc_lower.strip() in {"pa", "statewide"}) and "," not in loc_lower
    is_phillyish = any(x in loc_lower for x in ("philadelphia", "philly", "south jersey", "delaware valley"))

    urls: list[tuple[str, str]] = []
    if is_statewide or is_phillyish:
        urls.append(("BizQuest-Philadelphia", "https://www.bizquest.com/businesses-for-sale-in-philadelphia-pa/"))
    urls.append(("BizQuest-PA", "https://www.bizquest.com/businesses-for-sale-in-pennsylvania-pa/"))
    return urls
