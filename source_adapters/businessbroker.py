"""BusinessBroker.net URL builder for Grok-backed extraction."""

from __future__ import annotations


def build_urls(location: str, max_price: int, min_price: int = 0) -> list[tuple[str, str]]:
    loc_lower = location.lower()
    is_statewide = ("pennsylvania" in loc_lower or loc_lower.strip() in {"pa", "statewide"}) and "," not in loc_lower
    is_phillyish = any(x in loc_lower for x in ("philadelphia", "philly", "south jersey", "delaware valley"))

    urls: list[tuple[str, str]] = []
    if is_statewide or is_phillyish:
        urls.append((
            "BusinessBroker-Philadelphia",
            f"https://www.businessbroker.net/businesses-for-sale/philadelphia-pennsylvania.aspx?MinPrice={min_price}&MaxPrice={max_price}",
        ))

    urls.append((
        "BusinessBroker-PA",
        f"https://www.businessbroker.net/businesses-for-sale/pennsylvania/?MinPrice={min_price}&MaxPrice={max_price}",
    ))
    for page in range(2, 8):
        urls.append((
            f"BusinessBroker-PA-p{page}",
            f"https://www.businessbroker.net/businesses-for-sale/pennsylvania/?MinPrice={min_price}&MaxPrice={max_price}&Page={page}",
        ))
    return urls
