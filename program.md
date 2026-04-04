# autobiz — Business Viability Scoring Framework

This file defines the evaluation criteria used to score businesses from the CSV.
Edit this file to tune what "strong business" means to you.

---

## Investment Thesis

**Budget**: $50,000 maximum (acquisition price, not startup costs)
**Seller profile**: Baby boomer / retirement sale preferred — motivated sellers, cleaner transitions
**Primary goal**: Fastest possible payback period (business cash flow repays the purchase price)
**Target payback**: Under 3 years (33%+ annual ROI on purchase price)
**Operator profile**: Semi-absentee or simple enough to learn quickly; existing staff preferred

---

## Hard Filters (automatic D-tier if failed)

- Asking price > $50,000 → disqualify unless strong negotiation signals
- Business < 1 year old → disqualify
- Requires specialized license or degree → disqualify
- No verifiable revenue or cash flow anywhere in listing → disqualify

---

## What You're Looking For

A strong acquisition for this thesis has:
- Asking price ≤ $50,000 with documented cash flow
- Owner retiring / selling for lifestyle reasons (not fleeing a failing business)
- Cash flow that pays back the purchase in ≤ 3 years
- Established customer base that isn't dependent on the owner's personality
- Simple operations (service routes, vending, laundry, simple retail, etc.)
- Some staff or systems already in place

---

## Scoring Parameters (weights sum to 100)

### 1. Payback Speed — Weight: 30
Primary metric. Annual cash flow divided by asking price.
- 40%+ annual ROI (≤ 2.5 year payback) = **5/5**
- 33–40% (≤ 3 year payback) = **4/5**
- 25–33% (≤ 4 year payback) = **3/5**
- 15–25% (≤ 6.5 year payback) = **2/5**
- < 15% or unknown = **1/5**

### 2. Price vs. Budget Fit — Weight: 20
How well does the asking price fit the $50k budget?
- ≤ $30,000 = **5/5** (room to negotiate up, or cash left over)
- $30,001–$45,000 = **4/5**
- $45,001–$50,000 = **3/5**
- $50,001–$65,000 = **2/5** (might negotiate down)
- > $65,000 = **1/5**

### 3. Seller Motivation (Boomer Exit) — Weight: 15
Signals that the owner is motivated to sell cleanly.
- Explicit retirement / health / age mention = **5/5**
- "Lifestyle change", "moving", "other interests" = **4/5**
- No reason given but long-tenured owner (10+ yrs) = **3/5**
- Vague or no seller context = **2/5**
- Signals urgency without explanation = **1/5** (red flag)

### 4. Owner Independence — Weight: 15
Can the business survive and be run by a new owner quickly?
- Route/vending/franchise with documented systems = **5/5**
- Staff in place, semi-absentee possible = **4/5**
- Some staff, owner trains buyer = **3/5**
- Owner-operator but simple enough to learn = **2/5**
- Completely owner-dependent, no staff = **1/5**

### 5. Business Age & Stability — Weight: 10
Years in operation with consistent performance.
- 15+ years = **5/5**
- 10–15 years = **4/5**
- 5–10 years = **3/5**
- 2–5 years = **2/5**
- < 2 years = **1/5**

### 6. Operational Simplicity — Weight: 10
How easy is it for a new owner to step in?
- Route business, vending, laundromat, simple service = **5/5**
- Retail with staff, franchise = **4/5**
- Food service with staff = **3/5**
- Skilled trade (plumbing, HVAC) = **2/5**
- Requires specialized expertise = **1/5**

### 7. Red Flags — Penalty (subtract from total)
- Missing financials entirely = -2
- Reason for selling is unclear + business < 5 years old = -2
- Asking price >> any stated investment or revenue = -1
- Owner is sole revenue generator (clients follow owner) = -2
- Declining revenue trend mentioned = -2
- Requires significant capex or renovation = -1

---

## Hard Tier Rules (applied after scoring)

These rules override the weighted score:

- **A-tier requires**: `owner_independence` score ≥ 3/5 (some staff or documented systems). Pure solo owner-operators are capped at B-tier no matter the score.
- **A-tier requires**: Asking price ≤ $55,000 (hard ceiling — no exceptions).
- **Estimated listings** (source_url = "estimated") are capped at B-tier. Real verified URLs can reach A-tier.
- **Margin sanity**: If stated profit margin exceeds 2× the industry norm for this business type, apply an automatic extra red flag penalty of 2 points and note the discrepancy. Industry norms: laundromat 18-25%, vending route 25-35%, cleaning service 10-18%, landscaping/lawn care 10-15%, bookkeeping 30-45%, coffee cart 8-12%, retail/snack 5-12%, flower shop 8-14%.
- **Score cap for owner-only**: If `owner_independence = 1/5`, cap `weighted_score` at 74 (forces B-tier max).

---

## Output Format

For each business produce:
- **Score**: weighted total out of 100
- **Tier**: A (80–100), B (60–79), C (40–59), D (< 40)
- **Payback Projection**: estimated years to recoup $50k investment
- **Summary**: 2–3 sentence plain-English verdict
- **Key Strength**: single best thing about this business
- **Key Risk**: single biggest concern
- **Extracted Financials**: asking price, cash flow, gross revenue, margin, ROI as parsed from description
- **Boomer Signal**: quote or paraphrase from listing indicating seller motivation

---

## Analyst Notes

Target business types that historically work well for this thesis:
- **Service routes**: vending, ATM, water treatment, pest control routes
- **Laundromats**: coin-operated, low staff, recurring revenue
- **Simple retail**: convenience, tobacco, small grocery
- **Service businesses**: cleaning, landscaping with contracts in place
- **Home-based**: bookkeeping, tax prep with existing client base
