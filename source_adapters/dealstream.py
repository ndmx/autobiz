"""DealStream URL builder for Grok-backed extraction."""

from __future__ import annotations

from source_adapters.parsing import parse_listing_cards


def build_urls(location: str, max_price: int, min_price: int = 0) -> list[tuple[str, str]]:
    return [("DealStream-PA", "https://dealstream.com/pennsylvania-businesses-for-sale/5")]


def parse_listings(html: str, source_url: str, label: str = "DealStream") -> list[dict]:
    return parse_listing_cards(
        html=html,
        base_url=source_url,
        source=label,
        card_selectors=[
            ".listing",
            ".deal-card",
            ".business-card",
            ".result",
            "article",
            "li",
        ],
        title_selectors=[".title", ".listing-title", ".deal-title", "h3", "h2", "a"],
        price_selectors=[".price", ".asking-price", ".deal-price", ".financials"],
        location_selectors=[".location", ".city-state"],
        description_selectors=[".description", ".summary", ".teaser", "p"],
        link_selectors=["a[href*='business']", "a[href*='businesses-for-sale']", "a[href]"],
    )
