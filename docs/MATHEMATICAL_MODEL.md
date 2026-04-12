# autobiz Mathematical Model

This document describes the scoring and ranking model used by the current
autobiz codebase. It is meant to be a practical source of truth for reviewing
results, not a theoretical idealization.

The main source files are:

- `agent.py`: orchestration, verification, hard-rule post processing
- `research.py`: LLM scoring prompt, weighted score formula, DSCR assumptions
- `listing_utils.py`: financial confidence, metadata merge, Philly proximity
- `dedupe.py`: duplicate detection and merge-quality scoring
- `proximity.py`: Pennsylvania/Philadelphia distance enrichment
- `reporting.py`: text and HTML dashboard output

## 1. Pipeline Overview

autobiz turns raw business-for-sale listings into a Philly-prioritized
acquisition shortlist. The scoring flow is:

```text
discover listings
  -> normalize and enrich source fields
  -> dedupe probable duplicate listings
  -> attach Philadelphia distance and proximity rank
  -> score with Claude using the acquisition rubric
  -> apply deterministic hard rules and confidence caps
  -> verify top source URLs with Grok
  -> re-apply deterministic hard rules after verification
  -> render reports, dashboard, and run metadata
```

The core score is a weighted composite:

```text
S in [0, 100]
```

The score can be reduced by red-flag penalties and capped by hard rules. Hard
rules are intentionally conservative because listings with weak financial
evidence should not rank above better-documented listings.

## 2. Symbols

| Symbol | Meaning | Code source |
|---|---|---|
| `P` | asking price in USD | listing fields / extracted financials |
| `F` | annual cash flow in USD | listing fields / extracted financials |
| `R` | annual gross revenue in USD | listing fields / extracted financials |
| `D` | assumed down payment | `min(budget, 50000)` |
| `s_i` | rubric score for a dimension, 1 to 5 | Claude scoring JSON |
| `w_i` | rubric weight | `research.SCORING_WEIGHTS` |
| `p` | red-flag penalty count | Claude scoring JSON plus hard rules |
| `C` | financial confidence score, 0 to 100 | `listing_utils.financial_confidence` |

## 3. Financial Confidence

Financial confidence measures how much hard evidence a listing has. It is not a
quality score. A bad deal can have high confidence, and a great-sounding deal
can be capped if the hard numbers are missing.

The current formula is:

```text
C = 25 * I[asking price present]
  + 35 * I[cash flow present]
  + 20 * I[revenue present]
  + 10 * I[seller motivation signal present]
  + 10 * I[non-estimated source URL present]
```

Important wording: the final 10-point term is not a VerifyAgent confirmation.
It only means `source_url` is present, is not one of the estimate placeholders,
and the row is not already marked `is_estimated`.

Caps:

| Condition | Confidence cap |
|---|---:|
| listing is estimated or source is `estimated` / `market estimate` | `C <= 45` |
| source URL is missing but the listing is not explicitly estimated | `C <= 70` |
| otherwise | `C <= 100` |

Confidence levels:

| Score range | Level |
|---|---|
| `C >= 80` | high |
| `55 <= C < 80` | medium |
| `30 <= C < 55` | low |
| `C < 30` | very_low |

The provenance label `verified_url` in `financial_field_provenance` means
"non-estimated URL present." It does not mean Grok has confirmed the URL.

## 4. Deduplication

Deduplication tries to merge the same business appearing from multiple sources.

### Name Tokens

Business names are normalized to lowercase tokens. Generic words such as
`business`, `company`, `llc`, `opportunity`, `pa`, `pennsylvania`, `sale`, and
`turnkey` are removed.

```text
J(A, B) = |tokens(A) intersect tokens(B)| / |tokens(A) union tokens(B)|
```

If either token set is empty, similarity is `0`.

### Price Bucket

```text
bucket(P) = round(P / 10000)
```

Two listings are considered close in price when their buckets differ by at most
one. This is a bucket comparison, not a strict `$10k` absolute difference.

### Duplicate Condition

Listings are probable duplicates when either:

```text
same source host and exact normalized name
```

or:

```text
same normalized location
and price buckets differ by <= 1
and Jaccard similarity >= 0.72
```

### Merge Quality

When two records are merged, the project keeps the higher-quality record and
fills missing fields from the other record.

The quality score is:

```text
Q = 5 * I[asking_price present]
  + 5 * I[asking_price_usd present]
  + 5 * I[cash_flow_annual present]
  + 5 * I[gross_revenue_annual present]
  + 4 * I[source_url present and source_url != "estimated"]
  + min(6, floor(len(description) / 300))
  + 2 * I[seller_motivation present]
```

The description term uses integer floor division, not a fractional ratio.

## 5. Philadelphia Proximity

The default anchor is Philadelphia:

```text
lat = 39.9526
lon = -75.1652
```

Distances use the Haversine formula with earth radius `3958.8` miles.

Proximity buckets:

| Bucket | Distance |
|---|---:|
| Philadelphia | `<= 5 mi` |
| Within 25 mi | `> 5` and `<= 25 mi` |
| 25-50 mi | `> 25` and `<= 50 mi` |
| 50-100 mi | `> 50` and `<= 100 mi` |
| 100-200 mi | `> 100` and `<= 200 mi` |
| 200+ mi | `> 200 mi` |

`assign_proximity_ranks` assigns `proximity_rank` by sorted distance. The helper
mutates the listing dictionaries and returns the original list order.

## 6. Deal Structure and DSCR

The model assumes a seller-financed note:

```text
D = min(budget, 50000)
note = P - D
annual_payment = note * 0.07 / (1 - (1.07)^-6)
DSCR = F / annual_payment
monthly_net = (F - annual_payment) / 12
```

With the common default max budget of `$250k`, `D` is `$50k`. If a run uses a
budget below `$50k`, the down payment becomes that lower budget value.

DSCR score:

| DSCR | Score |
|---|---:|
| `>= 2.5` | 5 |
| `>= 2.0` and `< 2.5` | 4 |
| `>= 1.5` and `< 2.0` | 3 |
| `>= 1.0` and `< 1.5` | 2 |
| `< 1.0` or unknown | 1 |

## 7. Weighted Score

Claude scores six acquisition dimensions from 1 to 5.

| Dimension | Key | Weight |
|---|---|---:|
| Debt-service coverage | `dscr_score` | 30 |
| Seller-finance likelihood | `seller_finance_likelihood` | 20 |
| Seller motivation / boomer exit | `seller_motivation` | 15 |
| Owner independence | `owner_independence` | 15 |
| Business age and stability | `business_age` | 10 |
| Operational simplicity | `operational_simplicity` | 10 |

Raw score:

```text
S_raw = sum((s_i / 5) * w_i)
```

Penalty-adjusted score:

```text
S_scored = max(0, S_raw - p * 5)
```

Each red-flag penalty point subtracts 5 score points.

Tier mapping:

| Score | Tier |
|---|---|
| `S >= 80` | A |
| `60 <= S < 80` | B |
| `40 <= S < 60` | C |
| `S < 40` | D |

## 8. Hard Rules

Hard rules are applied after Claude scoring and again after verification. They
are ordered so that margin penalties happen before final score caps. This keeps
caps deterministic: a later margin recompute cannot raise a listing above an
earlier cap.

### Rule 1: Margin Sanity Penalty

If reported profit margin exceeds twice the industry norm:

```text
p = p + 2
S = recompute_weighted_score(scores)
```

The margin penalty is recorded once and is safe to reapply. Existing margin
flags do not add duplicate penalties.

Industry margin norms:

| Business type | Norm | Trigger |
|---|---:|---:|
| laundromat / coin laundry | 22% | `> 44%` |
| vending / vending route | 30% | `> 60%` |
| cleaning / cleaning service | 14% | `> 28%` |
| landscaping / lawn care | 12% | `> 24%` |
| bookkeeping | 38% | `> 76%` |
| coffee / coffee cart | 10% | `> 20%` |
| retail / snack | 8% | `> 16%` |
| flower / florist | 11% | `> 22%` |
| default | 15% | `> 30%` |

### Rule 2: Owner-Only Cap

```text
if owner_independence <= 1 and S > 74:
    S = 74
```

### Rule 3: Estimated Listing Cap

```text
if is_estimated or source_url in {"estimated", ""}:
    is_estimated = True
    if S >= 80:
        S = min(S, 79)
```

This prevents estimated listings from remaining Tier A.

### Rule 4: Missing Cash Flow Cap

```text
if cash_flow is absent and S > 69:
    S = 69
```

This prevents a listing with no usable owner benefit or cash-flow figure from
ranking as a top acquisition candidate.

### Rule 5: Low Financial Confidence Cap

```text
if C < 30 and S > 49:
    S = 49
elif 30 <= C < 55:
    cap = 59 if cash_flow is absent else 69
    if S > cap:
        S = cap
```

After all rules, the tier is recomputed from the final score.

## 9. Verification

The top `N` listings with non-empty, non-estimated URLs are checked by Grok.

| Verdict | Meaning | Immediate effect |
|---|---|---|
| `VERIFIED` | active listing confirmed | no estimate flag added |
| `LIKELY_REAL` | similar real listing found | no estimate flag added |
| `UNVERIFIED` | URL/listing could not be confirmed | sets `is_estimated = True` |

After verification, the orchestrator re-runs hard rules in the same run. That
means an `UNVERIFIED` result can immediately lower financial confidence, cap the
score, and change the tier before reports, dashboard output, metadata, and job
history are written.

## 10. Cross-Run Memory

autobiz keeps two lightweight memory files under `runs/`:

- `seen.json`: type/location fingerprints and counts
- `findings.md`: prior Tier A/B highlights

The search query builder uses these files to avoid repeating over-explored
type/location combinations and to bias future searches toward patterns that
previously produced promising listings.

## 11. Dashboard and Run History

Each agent run writes:

- `discovered.json`
- `scored.json`
- `deep_dives.json` when deep dives are enabled
- `report.txt`
- `dashboard.html`
- `meta.json`

The app dashboard also persists job history through the run-jobs layer, so the
browser UI can show current background status and prior runs without requiring
manual dashboard startup.

## 12. Current Limitations

1. DSCR uses a fixed seller-note assumption: 7% interest and 6-year amortization.
2. Down payment is modeled as `min(budget, 50000)`, not negotiated deal by deal.
3. Claude scoring is subjective, so mid-range scores can vary between runs.
4. Industry margin norms are fixed constants, not live market data.
5. Proximity uses geographic distance, not drive time or traffic.
6. Financial confidence treats field presence mostly as binary. Provenance is
   tracked, but the scoring contribution is not yet graded by source reliability.
7. Payback years are reported but are not a deterministic hard filter.
8. Red-flag penalties are capped by the scoring schema, so many simultaneous
   risks can compress into the same maximum penalty band.

