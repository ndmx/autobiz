"""Shared HTML parsing helpers for marketplace adapters."""

from __future__ import annotations

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag


def text_of(node: Tag | None) -> str:
    return node.get_text(" ", strip=True) if node else ""


def parse_money(value: str) -> int | None:
    match = re.search(r"\$?\s*([0-9][0-9,]{2,})", value or "")
    if not match:
        return None
    return int(match.group(1).replace(",", ""))


def first_text(card: Tag, selectors: list[str]) -> str:
    for selector in selectors:
        text = text_of(card.select_one(selector))
        if text:
            return text
    return ""


def first_href(card: Tag, selectors: list[str], base_url: str) -> str:
    for selector in selectors:
        node = card.select_one(selector)
        if node and node.get("href"):
            return urljoin(base_url, node["href"])
    node = card.find("a", href=True)
    return urljoin(base_url, node["href"]) if node else ""


def value_near_label(text: str, labels: list[str]) -> int | None:
    pattern = "|".join(re.escape(label) for label in labels)
    match = re.search(rf"(?:{pattern})\s*:?\s*\$?\s*([0-9][0-9,]{{2,}})", text, re.IGNORECASE)
    return int(match.group(1).replace(",", "")) if match else None


def location_from_text(text: str) -> str:
    match = re.search(r"\b([A-Z][A-Za-z .'-]+,\s*(?:PA|Pennsylvania))\b", text)
    return match.group(1).strip() if match else ""


def candidate_cards(soup: BeautifulSoup, selectors: list[str]) -> list[Tag]:
    cards: list[Tag] = []
    for selector in selectors:
        cards.extend(node for node in soup.select(selector) if isinstance(node, Tag))
    seen = set()
    unique = []
    for card in cards:
        ident = id(card)
        if ident in seen:
            continue
        seen.add(ident)
        unique.append(card)
    return unique


def parse_listing_cards(
    html: str,
    base_url: str,
    source: str,
    card_selectors: list[str],
    title_selectors: list[str],
    price_selectors: list[str],
    location_selectors: list[str],
    description_selectors: list[str],
    link_selectors: list[str],
) -> list[dict]:
    soup = BeautifulSoup(html or "", "html.parser")
    listings = []
    for card in candidate_cards(soup, card_selectors):
        full_text = text_of(card)
        title = first_text(card, title_selectors)
        price_text = first_text(card, price_selectors) or full_text
        location = first_text(card, location_selectors) or location_from_text(full_text)
        description = first_text(card, description_selectors) or full_text
        url = first_href(card, link_selectors, base_url)

        asking = (
            value_near_label(full_text, ["asking price", "asking", "price"])
            or parse_money(price_text)
        )
        cash_flow = value_near_label(full_text, ["cash flow", "cashflow", "sde", "owner benefit", "earnings"])
        revenue = value_near_label(full_text, ["gross revenue", "revenue", "sales"])

        if not title or len(title) < 4:
            continue
        listings.append({
            "business_name": title,
            "business_type": "",
            "asking_price": asking,
            "location": location,
            "cash_flow_annual": cash_flow,
            "gross_revenue_annual": revenue,
            "year_established": None,
            "employees": "",
            "description": description[:2000],
            "seller_motivation": "owner retiring" if re.search(r"\bretir", full_text, re.IGNORECASE) else "",
            "source_url": url or base_url,
            "listing_date": "",
            "_source": source,
            "_parser": "direct_html",
        })
    return listings
