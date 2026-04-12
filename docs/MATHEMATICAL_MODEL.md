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
| `w_i` | rubric weight | `research.WEIGHTS` |
| `p` | red-flag penalty count | Claude scoring JSON plus hard rules |
| `C` | financial confidence score, 0 to 100 | `listing_utils.financial_confidence` |

## 3. Financial Confidence

Financial confidence measures how much hard evidence a listing has. It is not a
quality score. A bad deal can have high confidence, and a great-sounding deal
can be capped if the hard numbers are missing.

**Important:** confidence is computed on the *merged* record after Claude scores
it, not on the raw scraped listing alone (`listing_utils.attach_listing_metadata`
calls `financial_confidence({**listing, **result})`). This means fields that
Claude extracted from the listing description — cash flow, revenue, asking price
— count toward `C` even if they were absent in the original scraped data. A
listing that looked sparse before scoring can have meaningfully higher confidence
after Claude extracts structured financials from its narrative text.

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

**`_duplicate_count` semantics:** the first unique occurrence is stored with
`_duplicate_count = 1`, not `0`. Each time a duplicate is merged in, the count
increments by one (`dedupe.py:141`). A count of `2` means one duplicate was
found and absorbed — two sources carried the same listing. This is easy to
misread as "two duplicates were found."

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

**Down payment mismatch with scoring prompt:** the Claude scoring prompt
describes the down payment as "~$50,000 (flexible $30k–$80k)". Claude may
therefore score DSCR against a different assumed down payment than what the code
computes in `deal_structure`. The code always uses the fixed `min(budget,
50000)` value. If Claude assumes a lower down payment, the note is larger, the
annual payment is higher, and DSCR appears worse — potentially producing a lower
`dscr_score` than the code's own deal structure would imply. This is a latent
inconsistency between the LLM judgment and the deterministic formula. See
Section 12, limitation 9.

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

**Score floor before penalties:** when every dimension scores 1 out of 5,
`S_raw = (1/5) * (30+20+15+15+10+10) = 20`. So `S_raw` is bounded `[20, 100]`
— a listing cannot fall below 20 from rubric scores alone. Penalties are what
push the score below 20, and `max(0, ...)` prevents it going negative. This
means a score in the range 1–19 is only reachable through penalty deductions,
not through low rubric scores alone. A score of exactly 20 indicates all six
dimensions were scored at the minimum with zero penalty.

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

**Rule 4 / Rule 5 overlap:** when cash flow is absent and `30 <= C < 55`, both
rules fire on the same listing. Rule 4 caps at 69; Rule 5 then caps at 59
(because cash flow is absent). Rule 5 is always the tighter constraint in this
combination, so Rule 4's cap of 69 is immediately overridden. The effective
ceiling is 59. This is not a bug — the rules are applied sequentially and the
last cap wins — but it means Rule 4 is redundant whenever Rule 5's `30 <= C <
55` branch also triggers. If you want to reason about the binding rule for a
given listing, check Rule 5 first when confidence is low.

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

**Conditional on `--verify-top`:** the hard-rule re-pass only happens when
`verify_top > 0` (the default). When verification is skipped with
`--verify-top 0`, `apply_hard_rules` is called exactly once — after scoring,
before verification — and never again (`agent.py:550` vs `agent.py:562`). A
fast or dry run with verification disabled therefore gets one fewer hard-rule
pass. Any score inflation that verification would have caught (e.g. a listing
with a bad URL that would have been marked `is_estimated`) is not corrected.

**Listings with no source URL are never verified.** Only listings with a
non-empty, non-estimated `source_url` are submitted to Grok. Listings without a
URL stay `_verified = "UNKNOWN"` and are unaffected by verification. The
estimated cap (Rule 3) already applies to them from the first hard-rule pass.

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
9. The Claude scoring prompt describes the down payment as "flexible $30k–$80k"
   but the code always computes deal structure using `min(budget, 50000)`. Claude
   may evaluate DSCR against a different assumed down payment than what appears
   in the `deal_structure` output block, producing a `dscr_score` that does not
   exactly match the code's own arithmetic. Aligning the prompt to use the exact
   same fixed `D` value would remove this ambiguity.

