"""Grok-proxied listing page adapters."""

from __future__ import annotations

import json

from source_adapters import bizbuysell, bizquest, businessbroker, dealstream
from xai_sdk import Client as XaiClient
from xai_sdk.chat import user as xai_user


GROK_MODEL = "grok-4.20-multi-agent-latest"


GROK_SCRAPE_PROMPT = """You have live web access. Please browse to the following URL right now and extract all business listings you find on that page.

URL: {url}

For each listing found on that page, extract:
- business name or description
- asking price (number)
- location (city, state)
- annual cash flow or net income (if shown)
- annual gross revenue (if shown)
- brief description
- the direct URL to the full listing detail page
- any mention of why the owner is selling

Return ONLY a valid JSON array with this schema (no markdown):
[
  {{
    "business_name": "string",
    "business_type": "string",
    "asking_price": number or null,
    "location": "string",
    "cash_flow_annual": number or null,
    "gross_revenue_annual": number or null,
    "year_established": null,
    "employees": "",
    "description": "string — include all text you can see from the listing card",
    "seller_motivation": "string",
    "source_url": "string — the full listing URL",
    "listing_date": ""
  }}
]

If the page is blocked, returns an error, or has no listings, return an empty array: []
Do NOT generate hypothetical listings — only return what you actually see on the page.
"""


def grok_scrape_url(grok: XaiClient, url: str, label: str, verbose: bool) -> list[dict]:
    if verbose:
        print(f"  [Grok→{label}] {url[:80]}")
    try:
        chat = grok.chat.create(model=GROK_MODEL)
        chat.append(xai_user(GROK_SCRAPE_PROMPT.format(url=url)))
        raw = chat.sample().content.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:].strip()

        data = json.loads(raw)
        if isinstance(data, list):
            for item in data:
                if not item.get("source_url"):
                    item["source_url"] = url
                item["_source"] = label
            print(f"  [Grok→{label}] Found {len(data)} listings")
            return data
        return []
    except json.JSONDecodeError:
        if verbose:
            print(f"  [Grok→{label}] JSON parse error — no listings extracted")
        return []
    except Exception as e:
        print(f"  [Grok→{label}] Error: {e}")
        return []


def build_grok_source_urls(location: str, max_price: int, min_price: int = 0) -> list[tuple[str, str]]:
    urls: list[tuple[str, str]] = []
    for adapter in (bizbuysell, businessbroker, bizquest, dealstream):
        urls.extend(adapter.build_urls(location, max_price, min_price=min_price))

    urls.extend([
        ("LoopNet-PA", "https://www.loopnet.com/biz/pennsylvania-businesses-for-sale/"),
        ("PennBBA-PA", "https://www.pennbba.com/buy_a_business.php"),
    ])

    seen = set()
    unique = []
    for label, url in urls:
        if url in seen:
            continue
        seen.add(url)
        unique.append((label, url))
    return unique
