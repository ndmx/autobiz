"""DealStream URL builder for Grok-backed extraction."""

from __future__ import annotations


def build_urls(location: str, max_price: int, min_price: int = 0) -> list[tuple[str, str]]:
    return [("DealStream-PA", "https://dealstream.com/pennsylvania-businesses-for-sale/5")]
