"""BizBuySell URL builder for Grok-backed extraction."""

from __future__ import annotations

from urllib.parse import quote_plus

from source_adapters.parsing import parse_listing_cards


def build_urls(location: str, max_price: int, min_price: int = 0) -> list[tuple[str, str]]:
    loc_enc = quote_plus(location)
    loc_lower = location.lower()
    is_statewide = ("pennsylvania" in loc_lower or loc_lower.strip() in {"pa", "statewide"}) and "," not in loc_lower
    is_phillyish = any(x in loc_lower for x in ("philadelphia", "philly", "south jersey", "delaware valley"))
    price_filter = f"min_price={min_price}&max_price={max_price}" if min_price else f"max_price={max_price}"

    urls: list[tuple[str, str]] = []
    if is_statewide or is_phillyish:
        urls.extend([
            (
                "BizBuySell-Philadelphia",
                f"https://www.bizbuysell.com/businesses-for-sale/?q=Philadelphia+PA&{price_filter}",
            ),
            (
                "BizBuySell-PA-Philly-metro",
                f"https://www.bizbuysell.com/pennsylvania-businesses-for-sale/?{price_filter}&q=Philadelphia+Bucks+Montgomery+Delaware+Chester",
            ),
            (
                "BizBuySell-PA-seller-finance-Philly",
                f"https://www.bizbuysell.com/pennsylvania-businesses-for-sale/?{price_filter}&q=Philadelphia+seller+financing+owner+retiring",
            ),
        ])
    elif location.strip():
        urls.append((
            f"BizBuySell-{location.split()[0]}",
            f"https://www.bizbuysell.com/businesses-for-sale/?q={loc_enc}&{price_filter}",
        ))

    urls.extend([
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
        ("BizBuySell-PA-all", f"https://www.bizbuysell.com/pennsylvania-businesses-for-sale/?{price_filter}"),
    ])
    return urls


def parse_listings(html: str, source_url: str, label: str = "BizBuySell") -> list[dict]:
    return parse_listing_cards(
        html=html,
        base_url=source_url,
        source=label,
        card_selectors=[
            "[data-testid*='listing']",
            ".listing-card",
            ".bfs-card",
            ".result",
            "article",
            "li",
        ],
        title_selectors=[
            "[data-testid*='title']",
            ".business-title",
            ".listing-title",
            "h3",
            "h2",
            "a",
        ],
        price_selectors=[
            "[data-testid*='price']",
            ".asking-price",
            ".price",
            ".financial",
        ],
        location_selectors=[
            "[data-testid*='location']",
            ".location",
            ".city-state",
        ],
        description_selectors=[
            "[data-testid*='description']",
            ".description",
            ".summary",
            "p",
        ],
        link_selectors=["a[href*='/Business-Opportunity/']", "a[href*='/business-for-sale/']", "a[href]"],
    )
