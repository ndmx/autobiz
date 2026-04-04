"""
research.py — Autonomous Business Discovery Loop

Searches the web via Grok for baby boomer retirement business sales
under $50,000, scores each with Claude, and ranks by fastest payback.

Usage:
    uv run research.py
    uv run research.py --rounds 3 --output results.json
    uv run research.py --type "laundromat" --location "Pennsylvania"
    uv run research.py --budget 40000
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Optional

import anthropic
from xai_sdk import Client as XaiClient
from xai_sdk.chat import user as xai_user, system as xai_system

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

PROGRAM_MD = Path(__file__).parent / "program.md"
CLAUDE_MODEL = "claude-opus-4-6"
GROK_MODEL = "grok-4.20-multi-agent-latest"
MAX_RETRIES = 3
RETRY_DELAY = 2


def grok_call(grok: XaiClient, prompt: str, system_msg: str = None, max_tokens: int = 3000) -> str:
    """Unified xai_sdk chat call. Returns text content."""
    chat = grok.chat.create(model=GROK_MODEL)
    if system_msg:
        chat.append(xai_system(system_msg))
    chat.append(xai_user(prompt))
    response = chat.sample()
    return response.content


# ---------------------------------------------------------------------------
# Search query bank
# These queries are designed to surface boomer retirement sales ≤ $50k
# ---------------------------------------------------------------------------

def build_search_queries(budget: int, biz_type: str = "", location: str = "") -> list[str]:
    loc = f" in {location}" if location else ""
    btype = f" {biz_type}" if biz_type else ""
    budget_k = budget // 1000

    return [
        f"baby boomer retiring small{btype} business for sale under ${budget_k}000{loc} established cash flow",
        f"owner retiring{btype} business sale{loc} asking price under ${budget_k}000 motivated seller",
        f"retirement sale{btype} business{loc} under ${budget_k}k cash flow positive established 10 years",
        f"small{btype} business for sale{loc} ${budget_k//2}000 to ${budget_k}000 owner retiring semi-absentee",
        f"buy a{btype} business{loc} under ${budget_k}000 payback period cash flow route vending laundromat",
    ]


# ---------------------------------------------------------------------------
# Phase 1 — Grok Discovery
# Ask Grok to search for listings and return structured data
# ---------------------------------------------------------------------------

DISCOVERY_SYSTEM = """You are a business acquisition researcher. When asked to find small business listings,
you search the web thoroughly and return structured data. You ALWAYS respond with a valid JSON array.
Never return empty results — dig through BizBuySell, BusinessBroker.net, LoopNet, local broker sites,
Craigslist business listings, and any other sources you can find."""

DISCOVERY_PROMPT_TEMPLATE = """Search the web right now for: "{query}"

Find as many specific, real business listings as you can that match this search.
For each listing found, extract the following data.

Return ONLY a valid JSON array (no markdown, no explanation) with objects matching this schema:
[
  {{
    "business_name": "string — name or description of the business",
    "business_type": "string — category (e.g. laundromat, vending route, landscaping)",
    "asking_price": number or null,
    "location": "string",
    "cash_flow_annual": number or null,
    "gross_revenue_annual": number or null,
    "year_established": number or null,
    "employees": "string",
    "description": "string — full description including any financial details, reason for selling, operations info",
    "seller_motivation": "string — any clues about why the owner is selling",
    "source_url": "string or empty",
    "listing_date": "string or empty"
  }}
]

Rules:
- Include ONLY businesses with asking price ≤ $60,000 (or price unknown but likely under $60k)
- Prioritize listings where the owner mentions retirement, age, health, or lifestyle change
- Include at least 3 listings, up to 10
- If you can't find listings from live search, generate realistic hypothetical listings based on
  what you know about current market conditions for this type of business — clearly mark those
  with source_url = "estimated" so the analyst knows
- The description field should be detailed — include all financial info you found
"""


def discover_listings(grok: XaiClient, query: str, verbose: bool = False) -> list[dict]:
    """Use Grok to search the web for business listings matching the query."""
    if verbose:
        print(f"  Searching: {query[:80]}...")

    prompt = DISCOVERY_PROMPT_TEMPLATE.format(query=query)

    try:
        raw = grok_call(grok, prompt, system_msg=DISCOVERY_SYSTEM)
        raw = raw.strip()

        # Strip markdown fences if present
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:].strip()

        listings = json.loads(raw)
        if isinstance(listings, list):
            return listings
        return []

    except json.JSONDecodeError as e:
        if verbose:
            print(f"  [parse error: {e}]")
        return []
    except Exception as e:
        if verbose:
            print(f"  [search error: {e}]")
        return []


# ---------------------------------------------------------------------------
# Phase 2 — Grok Market Enrichment
# For each candidate, get current market benchmarks
# ---------------------------------------------------------------------------

def market_enrich(grok: XaiClient, business: dict) -> str:
    """Get current market context for this business type."""
    btype = business.get("business_type", "small business")
    location = business.get("location", "United States")
    asking = business.get("asking_price")
    cf = business.get("cash_flow_annual")

    query = (
        f"I'm considering buying a {btype} in {location} for ${asking:,}" if asking
        else f"I'm considering buying a {btype} in {location}"
    )
    if cf:
        query += f" with ${cf:,}/year cash flow"

    query += (
        ". Using current 2024-2025 market data, tell me:\n"
        "1. Is this a fair asking price? What's the typical multiple for this business type?\n"
        "2. What are realistic annual profit margins for this type of business right now?\n"
        "3. Is demand for this business type growing or shrinking?\n"
        "4. What are the biggest operational risks for a new owner?\n"
        "5. How long does it typically take a new owner to get up to speed?\n"
        "Answer in 120 words max."
    )

    try:
        return grok_call(grok, query)
    except Exception as e:
        return f"[market data unavailable: {e}]"


# ---------------------------------------------------------------------------
# Phase 3 — Claude Scoring
# ---------------------------------------------------------------------------

WEIGHTS = {
    "dscr_score": 30,
    "seller_finance_likelihood": 20,
    "seller_motivation": 15,
    "owner_independence": 15,
    "business_age": 10,
    "operational_simplicity": 10,
}

RED_FLAG_WEIGHT = 5  # multiplier per penalty point

# Industry profit margin norms (realistic %) used for sanity checking
INDUSTRY_MARGIN_NORMS = {
    "laundromat": 22,
    "coin laundry": 22,
    "vending": 30,
    "vending route": 30,
    "cleaning": 14,
    "cleaning service": 14,
    "landscaping": 12,
    "lawn care": 12,
    "bookkeeping": 38,
    "coffee": 10,
    "coffee cart": 10,
    "retail": 8,
    "snack": 8,
    "flower": 11,
    "florist": 11,
}

def get_industry_margin_norm(biz_type: str) -> int:
    """Return typical profit margin % for a business type. Default 15."""
    bt = biz_type.lower()
    for key, norm in INDUSTRY_MARGIN_NORMS.items():
        if key in bt:
            return norm
    return 15


def load_program() -> str:
    return PROGRAM_MD.read_text()


def build_scoring_prompt(business: dict, program: str, market_context: str, budget: int) -> str:
    margin_norm = get_industry_margin_norm(business.get('business_type', ''))
    source_url = business.get('source_url', '')
    is_estimated = source_url == 'estimated' or source_url == ''
    down_payment = min(budget, 50000)  # assumed down payment
    return f"""You are a business acquisition analyst. The buyer's model is:
- Down payment: ~${down_payment:,} (flexible $30k–$80k)
- Seller carries a note for the balance over 5–7 years at 6–8% interest
- Goal: cash flow must comfortably cover the annual note payment (DSCR ≥ 1.5) AND leave monthly income
- Target businesses: $75k–$250k asking price, boomer seller willing to finance, staff in place

Scoring framework:
<framework>
{program}
</framework>

Current market research for this business type:
<market_context>
{market_context}
</market_context>

Business listing to score:
Business Name: {business.get('business_name', 'Unknown')}
Business Type: {business.get('business_type', 'Unknown')}
Asking Price: {business.get('asking_price', 'Not stated')}
Location: {business.get('location', 'Not stated')}
Annual Cash Flow: {business.get('cash_flow_annual', 'Not stated')}
Annual Revenue: {business.get('gross_revenue_annual', 'Not stated')}
Year Established: {business.get('year_established', 'Not stated')}
Employees: {business.get('employees', 'Not stated')}
Seller Motivation: {business.get('seller_motivation', 'Not stated')}
Description:
{business.get('description', 'No description provided')}

Assumed down payment: ${down_payment:,}
Industry margin norm for this business type: ~{margin_norm}% (flag if stated margin > {margin_norm * 2}%)
Listing verified: {"NO — treat as market estimate" if is_estimated else "YES — real source URL provided"}

Your tasks:
1. Extract all financials from the description
2. Calculate the seller-finance deal structure:
   - Note amount = asking_price - {down_payment:,}
   - Annual note payment ≈ note_amount × 0.07 / (1 - 1.07^-6)  [7% / 6yr amortization]
   - DSCR = annual_cash_flow / annual_note_payment
   - Monthly net = (annual_cash_flow - annual_note_payment) / 12
3. Score on each parameter from the framework (1–5)
4. Apply red flag penalties
5. Compute weighted score
6. Assign tier: A (80–100), B (60–79), C (40–59), D (<40)
7. Write 2–3 sentence verdict focused on whether the deal cash-flows after debt service

Respond ONLY with a valid JSON object:
{{
  "business_name": "string",
  "business_type": "string",
  "asking_price_usd": number or null,
  "location": "string",
  "source_url": "{business.get('source_url', '')}",
  "extracted_financials": {{
    "gross_revenue_annual": number or null,
    "cash_flow_annual": number or null,
    "profit_margin_pct": number or null,
    "roi_pct": number or null,
    "payback_years": number or null,
    "notes": "string"
  }},
  "deal_structure": {{
    "down_payment": {down_payment},
    "note_amount": number or null,
    "annual_note_payment": number or null,
    "dscr": number or null,
    "monthly_net_after_debt": number or null,
    "structure_summary": "string — e.g. '$50k down, $100k note at 7%/6yr = $1,950/mo payment, $1,200/mo net'"
  }},
  "scores": {{
    "dscr_score": {{"score": 1-5, "reason": "string"}},
    "seller_finance_likelihood": {{"score": 1-5, "reason": "string"}},
    "seller_motivation": {{"score": 1-5, "reason": "string"}},
    "owner_independence": {{"score": 1-5, "reason": "string"}},
    "business_age": {{"score": 1-5, "reason": "string"}},
    "operational_simplicity": {{"score": 1-5, "reason": "string"}},
    "red_flags": {{"penalty": 0-5, "flags": ["list"]}}
  }},
  "weighted_score": number,
  "tier": "A" or "B" or "C" or "D",
  "seller_finance_signal": "string — evidence seller would carry a note, or 'None detected'",
  "boomer_signal": "string — evidence of retirement/motivated seller or 'None detected'",
  "summary": "2-3 sentence verdict focused on deal viability after debt service",
  "key_strength": "string",
  "key_risk": "string",
  "negotiation_note": "string — leverage for price or terms",
  "is_estimated": true or false,
  "margin_sanity_flag": "string or null"
}}
"""


def compute_weighted_score(scores: dict) -> float:
    total = 0.0
    for key, weight in WEIGHTS.items():
        raw = scores.get(key, {}).get("score", 1)
        total += (raw / 5) * weight
    penalty = scores.get("red_flags", {}).get("penalty", 0)
    total = max(0, total - (penalty * RED_FLAG_WEIGHT))
    return round(total, 1)


def score_to_tier(score: float) -> str:
    if score >= 80:
        return "A"
    elif score >= 60:
        return "B"
    elif score >= 40:
        return "C"
    else:
        return "D"


def score_business(
    claude: anthropic.Anthropic,
    grok: OpenAI,
    business: dict,
    program: str,
    budget: int,
) -> dict:
    market_context = market_enrich(grok, business)
    prompt = build_scoring_prompt(business, program, market_context, budget)

    for attempt in range(MAX_RETRIES):
        try:
            response = claude.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=1800,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = response.content[0].text.strip()

            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:].strip()

            result = json.loads(raw)

            if "scores" in result:
                result["weighted_score"] = compute_weighted_score(result["scores"])
                result["tier"] = score_to_tier(result["weighted_score"])

            return result

        except (anthropic.RateLimitError, anthropic.APIStatusError) as e:
            if attempt < MAX_RETRIES - 1:
                print(f"  API error, retrying... ({e})")
                time.sleep(RETRY_DELAY)
            else:
                raise
        except json.JSONDecodeError as e:
            if attempt == MAX_RETRIES - 1:
                return {
                    "business_name": business.get("business_name", "Unknown"),
                    "error": f"JSON parse failed: {e}",
                    "weighted_score": 0,
                    "tier": "D",
                }


# ---------------------------------------------------------------------------
# Phase 4 — Deep Dive on Top Candidates
# ---------------------------------------------------------------------------

def deep_dive(grok: XaiClient, candidate: dict) -> str:
    """Ask Grok for a detailed due diligence brief on the top candidate."""
    name = candidate.get("business_name", "this business")
    btype = candidate.get("business_type", "small business")
    location = candidate.get("location", "")
    asking = candidate.get("asking_price_usd") or candidate.get("asking_price")
    fin = candidate.get("extracted_financials", {})
    cf = fin.get("cash_flow_annual")

    prompt = (
        f"I'm seriously considering buying '{name}', a {btype} in {location}. "
        f"Asking price: ${asking:,}. Annual cash flow: ${cf:,}/year.\n\n" if (asking and cf)
        else f"I'm seriously considering buying '{name}', a {btype} in {location}.\n\n"
    )
    prompt += (
        "Do a thorough due diligence research using current web data:\n"
        "1. What questions should I ask the seller before committing?\n"
        "2. What hidden costs are common for this business type (equipment, licenses, insurance)?\n"
        "3. What's a realistic offer price — is there room to negotiate?\n"
        "4. What does a typical first 90 days look like for a new owner of this type of business?\n"
        "5. What financing options exist for acquiring this type of business (SBA 7a, seller financing, etc.)?\n"
        "6. Any specific red flags or due diligence checklist items for this business type?\n"
        "Be specific and practical. 250 words max."
    )

    try:
        return grok_call(grok, prompt)
    except Exception as e:
        return f"[deep dive unavailable: {e}]"


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

def deduplicate(listings: list[dict]) -> list[dict]:
    """Remove duplicate listings based on business name + location."""
    seen = set()
    unique = []
    for listing in listings:
        key = (
            listing.get("business_name", "").lower().strip()[:40],
            listing.get("location", "").lower().strip()[:20],
        )
        if key not in seen:
            seen.add(key)
            unique.append(listing)
    return unique


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------

TIER_ICONS = {"A": "🟢", "B": "🟡", "C": "🟠", "D": "🔴"}


def render_report(results: list[dict], deep_dives: dict, budget: int) -> str:
    good = [r for r in results if "error" not in r]
    good.sort(key=lambda x: x.get("weighted_score", 0), reverse=True)

    lines = []
    lines.append("=" * 72)
    lines.append("  autobiz — Auto-Research Report")
    lines.append(f"  Budget: ${budget:,}  |  Goal: Fastest payback from boomer seller")
    lines.append("=" * 72)
    lines.append(f"  Candidates analyzed: {len(results)}  |  Viable (B+ tier): {sum(1 for r in good if r.get('tier') in ('A','B'))}")
    lines.append("")

    for i, r in enumerate(good, 1):
        tier = r.get("tier", "?")
        score = r.get("weighted_score", 0)
        icon = TIER_ICONS.get(tier, "⚪")
        name = r.get("business_name", "Unknown")[:52]
        btype = r.get("business_type", "")
        loc = r.get("location", "")
        fin = r.get("extracted_financials", {})

        lines.append(f"{icon} #{i}  [{tier}] {score:.0f}/100  —  {name}")
        if btype or loc:
            lines.append(f"     {btype}  |  {loc}")
        lines.append("-" * 72)

        # Key financials
        ap = r.get("asking_price_usd")
        cf = fin.get("cash_flow_annual")
        rev = fin.get("gross_revenue_annual")
        margin = fin.get("profit_margin_pct")
        payback = fin.get("payback_years")

        fin_parts = []
        if ap:
            fin_parts.append(f"Ask: ${ap:,.0f}")
        if cf:
            fin_parts.append(f"CF: ${cf:,.0f}/yr")
        if rev:
            fin_parts.append(f"Rev: ${rev:,.0f}/yr")
        if margin:
            fin_parts.append(f"Margin: {margin:.0f}%")
        if payback:
            fin_parts.append(f"Payback: {payback:.1f} yrs")
        if fin_parts:
            lines.append("  Financials: " + "  |  ".join(fin_parts))

        # Payback projection
        pb = r.get("payback_projection", "")
        if pb:
            lines.append(f"  Payback:    {pb}")

        # Boomer signal
        bs = r.get("boomer_signal", "")
        if bs and bs != "None detected":
            lines.append(f"  Seller:     {bs}")

        # Scores
        scores = r.get("scores", {})
        score_parts = []
        labels = {
            "payback_speed": "Payback",
            "price_budget_fit": "Price Fit",
            "seller_motivation": "Seller",
            "owner_independence": "Independence",
            "business_age": "Age",
            "operational_simplicity": "Simplicity",
        }
        for key, label in labels.items():
            s = scores.get(key, {}).get("score", "?")
            score_parts.append(f"{label}: {s}/5")
        flags = scores.get("red_flags", {})
        if flags.get("penalty", 0) > 0:
            score_parts.append(f"Flags: -{flags['penalty']}")
        lines.append("  Scores: " + "  |  ".join(score_parts))

        lines.append(f"  Strength: {r.get('key_strength', 'N/A')}")
        lines.append(f"  Risk:     {r.get('key_risk', 'N/A')}")
        lines.append(f"  Verdict:  {r.get('summary', 'N/A')}")

        if flags.get("flags"):
            lines.append(f"  ⚠ Flags: {'; '.join(flags['flags'])}")

        neg = r.get("negotiation_note", "")
        if neg:
            lines.append(f"  Negotiate: {neg}")

        url = r.get("source_url", "")
        if url and url not in ("", "estimated"):
            lines.append(f"  Source:   {url}")
        elif url == "estimated":
            lines.append(f"  Source:   [market estimate — verify independently]")

        # Deep dive for top 3
        if i <= 3 and r.get("business_name") in deep_dives:
            dd = deep_dives[r["business_name"]]
            lines.append("")
            lines.append("  --- Due Diligence Brief (Grok) ---")
            for ddline in dd.split("\n"):
                lines.append(f"  {ddline}")

        lines.append("")

    # Tier summary
    tier_counts = {"A": 0, "B": 0, "C": 0, "D": 0}
    for r in good:
        t = r.get("tier", "D")
        if t in tier_counts:
            tier_counts[t] += 1

    lines.append("=" * 72)
    lines.append("  Tier Summary")
    lines.append("-" * 72)
    for t, icon in TIER_ICONS.items():
        lines.append(f"  {icon} Tier {t}: {tier_counts[t]} businesses")
    lines.append("=" * 72)

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="autobiz research — autonomous business discovery")
    parser.add_argument("--budget", type=int, default=50000, help="Max acquisition budget (default: 50000)")
    parser.add_argument("--rounds", type=int, default=2, help="Number of search rounds (default: 2)")
    parser.add_argument("--type", type=str, default="", dest="biz_type", help="Business type to focus on (e.g. laundromat)")
    parser.add_argument("--location", type=str, default="", help="Geographic focus (e.g. Pennsylvania)")
    parser.add_argument("--output", type=str, default=None, help="Save JSON results to file")
    parser.add_argument("--report", type=str, default=None, help="Save text report to file")
    parser.add_argument("--no-deep-dive", action="store_true", help="Skip deep dive on top 3 (faster)")
    parser.add_argument("--verbose", action="store_true", help="Show search queries and progress")
    args = parser.parse_args()

    # API clients
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    xai_key = os.environ.get("XAI_API_KEY")

    if not anthropic_key:
        print("Error: ANTHROPIC_API_KEY not set.")
        sys.exit(1)
    if not xai_key:
        print("Error: XAI_API_KEY not set.")
        sys.exit(1)

    claude = anthropic.Anthropic(api_key=anthropic_key)
    grok = XaiClient(api_key=xai_key)
    program = load_program()

    print("=" * 72)
    print("  autobiz — Autonomous Business Research")
    print(f"  Budget: ${args.budget:,}  |  Rounds: {args.rounds}  |  Goal: Fastest payback from boomer seller")
    if args.biz_type:
        print(f"  Type filter: {args.biz_type}")
    if args.location:
        print(f"  Location: {args.location}")
    print("=" * 72)
    print()

    # --- Phase 1: Discovery ---
    print("[ Phase 1 ] Searching for listings via Grok...")
    all_queries = build_search_queries(args.budget, args.biz_type, args.location)
    selected_queries = all_queries[:args.rounds]

    raw_listings: list[dict] = []
    for q in selected_queries:
        batch = discover_listings(grok, q, verbose=args.verbose)
        raw_listings.extend(batch)
        print(f"  Found {len(batch)} listings from search round")
        time.sleep(1)  # be kind to the API

    listings = deduplicate(raw_listings)
    print(f"  Total unique listings: {len(listings)}\n")

    if not listings:
        print("No listings found. Try different --type or --location parameters.")
        sys.exit(0)

    # --- Phase 2 & 3: Enrich + Score ---
    print("[ Phase 2 ] Scoring each listing (Claude + Grok market data)...")
    results = []
    for i, biz in enumerate(listings, 1):
        name = biz.get("business_name", "Unknown")[:55]
        print(f"  [{i}/{len(listings)}] {name}...")
        result = score_business(claude, grok, biz, program, args.budget)
        results.append(result)
        time.sleep(0.5)

    # Sort by score
    results.sort(key=lambda x: x.get("weighted_score", 0), reverse=True)
    top_tier = [r for r in results if r.get("tier") in ("A", "B")]
    print(f"\n  Scored {len(results)} businesses. Tier A/B: {len(top_tier)}\n")

    # --- Phase 4: Deep Dive on Top 3 ---
    deep_dives = {}
    if not args.no_deep_dive:
        top3 = [r for r in results if "error" not in r][:3]
        if top3:
            print("[ Phase 3 ] Deep due diligence on top 3 candidates via Grok...")
            for candidate in top3:
                name = candidate.get("business_name", "Unknown")
                print(f"  Deep dive: {name[:55]}...")
                deep_dives[name] = deep_dive(grok, candidate)
                time.sleep(0.5)
            print()

    # --- Output ---
    if args.output:
        with open(args.output, "w") as f:
            json.dump(results, f, indent=2)
        print(f"JSON saved to {args.output}")

    report = render_report(results, deep_dives, args.budget)
    print(report)

    if args.report:
        with open(args.report, "w") as f:
            f.write(report)
        print(f"\nReport saved to {args.report}")


if __name__ == "__main__":
    main()
