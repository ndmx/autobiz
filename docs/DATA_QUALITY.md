# Data Quality

autobiz is only as good as the listing data it scores. This document defines
how to interpret data quality, source confidence, and proximity fields.

## Trust Levels

| Level | Meaning | Expected Use |
| --- | --- | --- |
| Source-backed | Listing has a real source URL from Craigslist, broker pages, or a listing marketplace. | Eligible for normal scoring. |
| Verified | Top candidate URL was checked by the VerifyAgent. | Better diligence candidate. |
| Likely real | Similar current listing or source evidence was found. | Worth manual verification. |
| Estimated | No source URL, or `source_url = "estimated"`. | Benchmark only; capped below A-tier. |
| Placeholder | Schema text such as `business_name = "string"`. | Filtered out by scraper normalization. |

## Current Source Behavior

Direct sources:

- Craigslist business-for-sale pages are fetched directly and detail pages are
  parsed with BeautifulSoup.

Grok-proxied sources:

- BizBuySell
- BusinessBroker.net
- BizQuest
- DealStream
- LoopNet
- PennBBA

These sources can be blocked, paginated, dynamically rendered, or sparse for a
given query. A zero-listing result means "nothing extractable from that page in
this run", not "the source has no relevant businesses."

## Required Fields

Minimum viable listing fields:

- `business_name`
- `asking_price` or enough description text to extract it
- `location`
- `description`
- `source_url`
- `_source`

Good scoring fields:

- `cash_flow_annual`
- `gross_revenue_annual`
- `year_established`
- `employees`
- `seller_motivation`
- `source_url`

Listings with missing financials should be treated cautiously, even when the
business type looks attractive.

## Financial Confidence and Provenance

`financial_confidence` is a separate data-quality score, not a deal-quality
score. It marks whether asking price, cash flow, revenue, and source evidence
are present, and it includes field-level provenance:

- `scraped`: value came directly from the listing object.
- `llm_extracted`: value came from a scored result's extracted financials.
- `estimated`: value belongs to an estimated or unverified listing.
- `missing`: no usable positive value was found.

Hard rules cap listings with missing verified cash flow and listings with low
financial confidence so attractive narratives do not outrank stronger evidence.

## Deduplication

`dedupe.py` merges likely repeats across sources using normalized name tokens,
location, source host, and price bands. Merged rows retain `_duplicate_count`,
`_duplicate_sources`, and a `_dedupe_note` so the dashboard can surface when a
business appeared in more than one place.

## Philly Proximity Fields

Every normalized listing should include:

- `city`
- `county`
- `distance_to_philly_miles`
- `proximity_bucket`
- `proximity_rank`

Distances are approximate. They are based on known city/county coordinates and
Craigslist market fallbacks, not a full street-address geocoder.

## Known Limitations

- Broker pages may show fewer details to unauthenticated users.
- Some pages block automated access or change HTML frequently.
- Grok-proxied extraction can return no rows even when a human browser shows listings.
- County-only locations use county-seat approximations.
- Business names are sometimes generic, which makes deduplication probabilistic.
- Financial fields from descriptions may include seller discretionary earnings,
  owner benefit, cash flow, or EBITDA under inconsistent labels.

## Manual Review Checklist

Before contacting a seller:

1. Open the source URL manually.
2. Confirm the listing is active.
3. Confirm asking price, cash flow, revenue, and included assets.
4. Ask whether cash flow includes owner salary or add-backs.
5. Confirm whether staff, leases, licenses, and equipment transfer.
6. Confirm why the seller is exiting.
7. Compare the business address to the `distance_to_philly_miles` heuristic.

## Improving Data Quality

Highest leverage improvements:

- Add source-specific parsers for high-value listing sites instead of relying
  only on generic Grok extraction.
- Add a geocoder for exact address or city/county lookup.
- Store raw source snippets alongside normalized fields for auditability.
- Store raw source snippets alongside normalized fields for auditability.
- Add source freshness checks so stale listings do not keep ranking.
