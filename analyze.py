"""
autobiz — Business Viability Analyzer

Reads a CSV of business listings, uses Claude to extract financials and score
each business against the criteria defined in program.md.

Usage:
    uv run analyze.py --csv ../j_mnirekmw25v4gb0tgn.csv
    uv run analyze.py --csv ../j_mnirekmw25v4gb0tgn.csv --top 10
    uv run analyze.py --csv ../j_mnirekmw25v4gb0tgn.csv --output results.json
    uv run analyze.py --csv ../j_mnirekmw25v4gb0tgn.csv --tier A
"""

import argparse
import csv
import json
import os
import sys
import time
from pathlib import Path
from typing import Optional

import anthropic
from openai import OpenAI

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

PROGRAM_MD = Path(__file__).parent / "program.md"
MODEL = "claude-opus-4-6"
GROK_MODEL = "grok-3"
XAI_BASE_URL = "https://api.x.ai/v1"
MAX_RETRIES = 3
RETRY_DELAY = 2  # seconds between retries on rate limit

# ---------------------------------------------------------------------------
# Grok web search enrichment
# ---------------------------------------------------------------------------

def grok_enrich(grok_client: OpenAI, business: dict) -> str:
    """Ask Grok (with live web search) for current market context on this business type."""
    name = business.get("business_name", "")
    location = business.get("location", "")
    description = business.get("description", "")[:400]

    # Infer business type from name/description for a focused search
    query = (
        f"I'm evaluating a small business acquisition. The business is: '{name}' located in {location}. "
        f"Brief description: {description}\n\n"
        f"Using current web data, tell me:\n"
        f"1. What is the typical asking price multiple (revenue or earnings multiple) for this type of business right now?\n"
        f"2. Is this industry/business type currently growing, stable, or declining?\n"
        f"3. What are typical profit margins for this business type?\n"
        f"4. Any known risks or trends for this type of business in {location or 'the US'}?\n"
        f"Keep your answer concise — 150 words max."
    )

    try:
        response = grok_client.chat.completions.create(
            model=GROK_MODEL,
            messages=[{"role": "user", "content": query}],
            max_tokens=300,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        return f"[Grok enrichment unavailable: {e}]"


# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------

def load_program() -> str:
    return PROGRAM_MD.read_text()


def build_extraction_prompt(business: dict, program: str, market_context: str = "") -> str:
    market_section = ""
    if market_context:
        market_section = f"""
Current market research (from live web search via Grok):
<market_context>
{market_context}
</market_context>

Use this market context to calibrate your scoring, especially for revenue multiples, margins, and market position.
"""
    return f"""You are a business acquisition analyst. Your job is to evaluate small business listings.

Below is the scoring framework you must follow:

<framework>
{program}
</framework>
{market_section}
Here is the business listing to analyze:

Business Name: {business.get('business_name', 'Unknown')}
Asking Price: {business.get('asking_price', 'Not stated')}
Location: {business.get('location', 'Not stated')}
Cash Flow (field): {business.get('cash_flow', 'Not stated')}
Gross Revenue (field): {business.get('gross_revenue', 'Not stated')}
Year Established: {business.get('year_established', 'Not stated')}
Employees: {business.get('employees', 'Not stated')}
Inventory: {business.get('inventory', 'Not stated')}
Description:
{business.get('description', 'No description provided')}

Your task:
1. Extract all financial figures mentioned anywhere in the description (asking price, revenue, net income, cash flow, rent, etc.)
2. Score this business on each parameter from the framework (1–5 scale per parameter)
3. Apply red flag penalties
4. Calculate the final weighted score out of 100
5. Assign a tier: A (80–100), B (60–79), C (40–59), D (<40)
6. Write a 2–3 sentence plain-English verdict

Respond ONLY with a valid JSON object matching this exact schema:
{{
  "business_name": "string",
  "asking_price_usd": number or null,
  "extracted_financials": {{
    "gross_revenue_annual": number or null,
    "net_income_annual": number or null,
    "cash_flow_annual": number or null,
    "rent_monthly": number or null,
    "profit_margin_pct": number or null,
    "roi_pct": number or null,
    "revenue_multiple": number or null,
    "years_in_operation": number or null,
    "notes": "string — any relevant financial context extracted from text"
  }},
  "scores": {{
    "roi_payback": {{"score": 1-5, "reason": "string"}},
    "revenue_multiple": {{"score": 1-5, "reason": "string"}},
    "profit_margin": {{"score": 1-5, "reason": "string"}},
    "business_age": {{"score": 1-5, "reason": "string"}},
    "owner_independence": {{"score": 1-5, "reason": "string"}},
    "market_position": {{"score": 1-5, "reason": "string"}},
    "red_flags": {{"penalty": 0-5, "flags": ["list of flags found"]}}
  }},
  "weighted_score": number,
  "tier": "A" or "B" or "C" or "D",
  "summary": "2-3 sentence verdict",
  "key_strength": "string",
  "key_risk": "string"
}}

Do not include any text outside the JSON object.
"""


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

WEIGHTS = {
    "roi_payback": 25,
    "revenue_multiple": 15,
    "profit_margin": 15,
    "business_age": 15,
    "owner_independence": 15,
    "market_position": 10,
}

def compute_weighted_score(scores: dict) -> float:
    total = 0.0
    for key, weight in WEIGHTS.items():
        raw = scores.get(key, {}).get("score", 1)
        total += (raw / 5) * weight
    penalty = scores.get("red_flags", {}).get("penalty", 0)
    total = max(0, total - (penalty * 5))
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


# ---------------------------------------------------------------------------
# Claude call
# ---------------------------------------------------------------------------

def analyze_business(
    client: anthropic.Anthropic,
    business: dict,
    program: str,
    grok_client: Optional[OpenAI] = None,
) -> dict:
    market_context = ""
    if grok_client:
        market_context = grok_enrich(grok_client, business)

    prompt = build_extraction_prompt(business, program, market_context)

    for attempt in range(MAX_RETRIES):
        try:
            response = client.messages.create(
                model=MODEL,
                max_tokens=1500,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = response.content[0].text.strip()

            # Strip markdown code fences if present
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]

            result = json.loads(raw)

            # Recompute weighted score locally to ensure consistency
            if "scores" in result:
                result["weighted_score"] = compute_weighted_score(result["scores"])
                result["tier"] = score_to_tier(result["weighted_score"])

            return result

        except (anthropic.RateLimitError, anthropic.APIStatusError) as e:
            if attempt < MAX_RETRIES - 1:
                print(f"  Rate limit / API error, retrying in {RETRY_DELAY}s... ({e})")
                time.sleep(RETRY_DELAY)
            else:
                raise
        except json.JSONDecodeError as e:
            print(f"  JSON parse error on attempt {attempt + 1}: {e}")
            if attempt == MAX_RETRIES - 1:
                return {
                    "business_name": business.get("business_name", "Unknown"),
                    "error": f"JSON parse failed: {e}",
                    "raw_response": raw[:500],
                    "weighted_score": 0,
                    "tier": "D",
                }


# ---------------------------------------------------------------------------
# CSV loading
# ---------------------------------------------------------------------------

def load_csv(path: str) -> list[dict]:
    businesses = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Skip rows with errors
            if row.get("error"):
                continue
            businesses.append(dict(row))
    return businesses


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------

TIER_COLORS = {"A": "🟢", "B": "🟡", "C": "🟠", "D": "🔴"}

def render_report(results: list[dict], top: Optional[int] = None, tier_filter: Optional[str] = None) -> str:
    # Filter and sort
    filtered = [r for r in results if "error" not in r]
    if tier_filter:
        filtered = [r for r in filtered if r.get("tier") == tier_filter.upper()]
    filtered.sort(key=lambda x: x.get("weighted_score", 0), reverse=True)
    if top:
        filtered = filtered[:top]

    lines = []
    lines.append("=" * 70)
    lines.append("  autobiz — Business Viability Report")
    lines.append("=" * 70)
    lines.append(f"  Analyzed: {len(results)} businesses  |  Showing: {len(filtered)}")
    lines.append("")

    for i, r in enumerate(filtered, 1):
        tier = r.get("tier", "?")
        score = r.get("weighted_score", 0)
        icon = TIER_COLORS.get(tier, "⚪")
        name = r.get("business_name", "Unknown")[:55]
        fin = r.get("extracted_financials", {})

        lines.append(f"{icon} #{i}  [{tier}] {score:.0f}/100  —  {name}")
        lines.append("-" * 70)

        # Financials
        ap = r.get("asking_price_usd")
        rev = fin.get("gross_revenue_annual")
        cf = fin.get("cash_flow_annual") or fin.get("net_income_annual")
        roi = fin.get("roi_pct")
        margin = fin.get("profit_margin_pct")

        fin_parts = []
        if ap:
            fin_parts.append(f"Ask: ${ap:,.0f}")
        if rev:
            fin_parts.append(f"Revenue: ${rev:,.0f}")
        if cf:
            fin_parts.append(f"CF: ${cf:,.0f}")
        if roi:
            fin_parts.append(f"ROI: {roi:.1f}%")
        if margin:
            fin_parts.append(f"Margin: {margin:.1f}%")
        if fin_parts:
            lines.append("  Financials: " + "  |  ".join(fin_parts))

        # Scores breakdown
        scores = r.get("scores", {})
        score_parts = []
        labels = {
            "roi_payback": "ROI",
            "revenue_multiple": "RevMult",
            "profit_margin": "Margin",
            "business_age": "Age",
            "owner_independence": "Independence",
            "market_position": "Market",
        }
        for key, label in labels.items():
            s = scores.get(key, {}).get("score", "?")
            score_parts.append(f"{label}: {s}/5")
        flags = scores.get("red_flags", {})
        if flags.get("penalty", 0) > 0:
            score_parts.append(f"Flags: -{flags['penalty']}")
        lines.append("  Scores:  " + "  |  ".join(score_parts))

        # Verdict
        lines.append(f"  Strength: {r.get('key_strength', 'N/A')}")
        lines.append(f"  Risk:     {r.get('key_risk', 'N/A')}")
        lines.append(f"  Verdict:  {r.get('summary', 'N/A')}")

        # Red flags list
        flag_list = flags.get("flags", [])
        if flag_list:
            lines.append(f"  ⚠ Flags: {'; '.join(flag_list)}")

        lines.append("")

    # Tier summary
    tier_counts = {"A": 0, "B": 0, "C": 0, "D": 0}
    for r in results:
        t = r.get("tier", "D")
        if t in tier_counts:
            tier_counts[t] += 1

    lines.append("=" * 70)
    lines.append("  Tier Summary")
    lines.append("-" * 70)
    for t, icon in TIER_COLORS.items():
        lines.append(f"  {icon} Tier {t}: {tier_counts[t]} businesses")
    lines.append("=" * 70)

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="autobiz — Business Viability Analyzer")
    parser.add_argument("--csv", required=True, help="Path to business listings CSV")
    parser.add_argument("--top", type=int, default=None, help="Show only top N results")
    parser.add_argument("--tier", type=str, default=None, help="Filter by tier: A, B, C, D")
    parser.add_argument("--output", type=str, default=None, help="Save JSON results to file")
    parser.add_argument("--report", type=str, default=None, help="Save text report to file")
    parser.add_argument("--limit", type=int, default=None, help="Only analyze first N businesses (for testing)")
    parser.add_argument("--grok", action="store_true", help="Enrich each listing with live web search via Grok (xAI)")
    args = parser.parse_args()

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("Error: ANTHROPIC_API_KEY environment variable not set.")
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)

    grok_client = None
    if args.grok:
        xai_key = os.environ.get("XAI_API_KEY")
        if not xai_key:
            print("Error: XAI_API_KEY environment variable not set (required for --grok).")
            sys.exit(1)
        grok_client = OpenAI(api_key=xai_key, base_url=XAI_BASE_URL)
        print("Grok web search enrichment: ENABLED")

    program = load_program()

    print(f"Loading businesses from {args.csv}...")
    businesses = load_csv(args.csv)
    if args.limit:
        businesses = businesses[:args.limit]
    print(f"Loaded {len(businesses)} businesses.\n")

    results = []
    for i, biz in enumerate(businesses, 1):
        name = biz.get("business_name", "Unknown")[:60]
        print(f"[{i}/{len(businesses)}] Analyzing: {name}...")
        result = analyze_business(client, biz, program, grok_client=grok_client)
        result["_source_url"] = biz.get("input_url", "")
        result["_location"] = biz.get("location", "")
        results.append(result)
        # Brief pause to be kind to the API
        time.sleep(0.5)

    print("\nAnalysis complete.\n")

    # Output JSON
    if args.output:
        with open(args.output, "w") as f:
            json.dump(results, f, indent=2)
        print(f"JSON results saved to {args.output}")

    # Render report
    report_text = render_report(results, top=args.top, tier_filter=args.tier)
    print(report_text)

    if args.report:
        with open(args.report, "w") as f:
            f.write(report_text)
        print(f"\nReport saved to {args.report}")


if __name__ == "__main__":
    main()
