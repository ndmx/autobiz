# autobiz — Business Viability Scoring Framework

This file defines the evaluation criteria used to score businesses from the CSV.
Edit this file to tune what "strong business" means to you.

---

## Investment Thesis

**Down payment**: ~$50,000 (flexible — could be $30k–$80k depending on deal)
**Total acquisition budget**: Up to $250,000 asking price — seller financing covers the balance
**Financing structure**: Seller carries a note for 5–7 years at 5–8% interest; cash flow services the debt
**Seller profile**: Baby boomer / retirement sale preferred — motivated sellers are most open to seller financing
**Primary goal**: Cash flow covers the seller note payment AND leaves positive monthly income from day one
**Debt service coverage target**: Annual cash flow ≥ 1.5× annual note payment (DSCR ≥ 1.5)
**Operator profile**: Semi-absentee or simple enough to learn quickly; existing staff strongly preferred

---

## Hard Filters (automatic D-tier if failed)

- Asking price > $250,000 → disqualify (too large for seller-finance structure)
- Business < 1 year old → disqualify
- Requires specialized license or degree → disqualify
- No verifiable revenue or cash flow anywhere in listing → disqualify
- Annual cash flow < $20,000 → disqualify (can't service debt + generate income)

---

## What You're Looking For

A strong acquisition under this seller-finance model has:
- Asking price $75,000–$200,000 with documented cash flow ≥ $25,000/yr
- Owner retiring / selling for lifestyle reasons — motivated to accept seller financing
- Cash flow that covers the annual note payment with ≥ 1.5× coverage (DSCR)
- Established customer base not dependent on the owner's personality
- Simple operations (service routes, vending, laundry, simple retail, distribution, etc.)
- Staff or systems already in place — critical for lender/seller confidence
- Seller willing to finance 60–80% of price over 5–7 years

---

## Scoring Parameters (weights sum to 100)

### 1. Debt Service Coverage (DSCR) — Weight: 30
Primary metric. Assumes ~$50k down, seller note on balance at 7% / 6 years.
Calculate: annual note payment = (asking_price - 50000) × 0.07 / (1 - 1.07^-6)
DSCR = annual_cash_flow / annual_note_payment
- DSCR ≥ 2.5 (cash flow is 2.5× note payment — very comfortable) = **5/5**
- DSCR 2.0–2.5 = **4/5**
- DSCR 1.5–2.0 = **3/5**
- DSCR 1.0–1.5 (barely covers payments) = **2/5**
- DSCR < 1.0 or unknown = **1/5**

### 2. Seller Finance Likelihood — Weight: 20
How likely is the seller to carry a note? Boomer retirement sellers are most flexible.
- Explicit retirement + 10+ years owned + staff in place = **5/5**
- Retirement mentioned + 5+ years owned = **4/5**
- Lifestyle/health reason + established business = **3/5**
- Reason unclear but long tenure = **2/5**
- Reason unclear + young business = **1/5**

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
- **A-tier requires**: Asking price $50,000–$250,000 (must be large enough to structure a seller note, small enough for a boomer to accept).
- **Estimated listings** (source_url = "estimated") are capped at B-tier. Real verified URLs can reach A-tier.
- **Margin sanity**: If stated profit margin exceeds 2× the industry norm for this business type, apply an automatic extra red flag penalty of 2 points and note the discrepancy. Industry norms: laundromat 18-25%, vending route 25-35%, cleaning service 10-18%, landscaping/lawn care 10-15%, bookkeeping 30-45%, coffee cart 8-12%, retail/snack 5-12%, flower shop 8-14%.
- **Score cap for owner-only**: If `owner_independence = 1/5`, cap `weighted_score` at 74 (forces B-tier max).

---

## Output Format

For each business produce:
- **Score**: weighted total out of 100
- **Tier**: A (80–100), B (60–79), C (40–59), D (< 40)
- **DSCR**: debt service coverage ratio (cash flow / estimated annual note payment assuming ~$50k down)
- **Estimated deal structure**: down payment, note amount, monthly payment, monthly net after debt service
- **Summary**: 2–3 sentence plain-English verdict
- **Key Strength**: single best thing about this business
- **Key Risk**: single biggest concern
- **Extracted Financials**: asking price, cash flow, gross revenue, margin, ROI as parsed from description
- **Seller Finance Signal**: likelihood seller will carry a note + boomer exit evidence

---

## Analyst Notes

Target business types that work best for seller-finance acquisitions:
- **Service routes**: vending, ATM, water treatment, pest control, distribution routes ($75k–$200k range)
- **Laundromats**: coin-operated, passive, recession-resistant ($80k–$200k)
- **Cleaning services with staff**: recurring contracts, low capex, boomer-owned ($50k–$150k)
- **Landscaping with contracts**: seasonal but predictable ($75k–$175k)
- **Simple food/deli with staff**: neighborhood staples, long tenures ($75k–$200k)
- **Franchise resales**: documented systems, easier seller-finance conversation ($80k–$200k)

Seller financing is most available from:
- Owners who have been in the business 10+ years (they're paid off, profit is enough)
- Retirement/health sellers who want income stream, not lump sum
- Businesses without real estate (nothing for bank to lend against, seller is the bank)
