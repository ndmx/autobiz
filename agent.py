"""
agent.py — Autonomous Multi-Agent Research Orchestrator

Spawns parallel search agents, merges and scores results, commits
every run to git so you have a full searchable history of findings.

Architecture:
  Orchestrator
    ├── SearchAgent × N  (parallel Grok web searches)
    ├── ScoringAgent × M (parallel Claude scoring)
    ├── DeepDiveAgent    (Grok due diligence on top 3)
    └── GitAgent         (commits run output, tags top finds)

Usage:
    uv run agent.py
    uv run agent.py --budget 50000 --rounds 5 --type "vending route"
    uv run agent.py --budget 50000 --location "Pennsylvania" --rounds 4
    uv run agent.py --no-commit          # skip git commit (dry run)
    uv run agent.py --list-runs          # show git log of past runs
    uv run agent.py --show-run <sha>     # print a past run's report
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import anthropic
from xai_sdk import Client as XaiClient

from proximity import add_proximity_fields, assign_proximity_ranks

# Re-use all logic from research.py
from research import (
    build_search_queries,
    build_scoring_prompt,
    compute_weighted_score,
    deep_dive,
    deduplicate,
    discover_listings,
    load_program,
    market_enrich,
    score_to_tier,
    CLAUDE_MODEL,
    GROK_MODEL,
    MAX_RETRIES,
    RETRY_DELAY,
    get_industry_margin_norm,
    RED_FLAG_WEIGHT,
)

RUNS_DIR = Path(__file__).parent / "runs"
SEEN_FILE = RUNS_DIR / "seen.json"
FINDINGS_FILE = RUNS_DIR / "findings.md"

# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

def git(*args) -> str:
    """Run a git command and return stdout."""
    result = subprocess.run(
        ["git"] + list(args),
        capture_output=True, text=True, cwd=Path(__file__).parent
    )
    return result.stdout.strip()


def git_is_clean() -> bool:
    return git("status", "--porcelain") == ""


def git_commit_run(run_dir: Path, message: str) -> str:
    """Stage the run directory and commit. Returns the commit SHA."""
    rel = str(run_dir.relative_to(Path(__file__).parent))
    git("add", "-f", rel)  # -f bypasses .gitignore if needed
    git("commit", "-m", message, "--no-gpg-sign")
    return git("rev-parse", "--short", "HEAD")


def git_tag(tag: str, message: str):
    git("tag", "-a", tag, "-m", message, "--no-sign")


# ---------------------------------------------------------------------------
# Cross-run memory
# ---------------------------------------------------------------------------

def load_seen() -> dict:
    """Load the cross-run seen type:location fingerprints."""
    if SEEN_FILE.exists():
        try:
            return json.loads(SEEN_FILE.read_text())
        except Exception:
            return {}
    return {}


def update_seen(seen: dict, results: list[dict]) -> dict:
    """Add scored results to the seen fingerprint map."""
    from datetime import date
    today = str(date.today())
    for r in results:
        btype = r.get("business_type", "unknown").lower().strip()[:30]
        loc = r.get("location", "unknown").lower().strip()[:25]
        key = f"{btype}:{loc}"
        entry = seen.get(key, {"count": 0})
        entry["count"] = entry.get("count", 0) + 1
        entry["last_seen"] = today
        seen[key] = entry
    return seen


def save_seen(seen: dict):
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    SEEN_FILE.write_text(json.dumps(seen, indent=2))


def load_findings() -> str:
    """Load the findings log from past runs."""
    if FINDINGS_FILE.exists():
        return FINDINGS_FILE.read_text()
    return ""


def save_findings(results: list[dict], run_id: str, budget: int):
    """Append top findings from this run to the persistent findings log."""
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    top = [r for r in results if r.get("tier") in ("A", "B") and "error" not in r][:5]
    if not top:
        return
    lines = [f"\n## Run {run_id} (budget ${budget:,})\n"]
    for r in top:
        fin = r.get("extracted_financials", {})
        payback = fin.get("payback_years")
        pb_str = f"{payback:.1f}yr payback" if payback else "payback unknown"
        lines.append(
            f"- [{r.get('tier')}] {r.get('weighted_score', 0):.0f}/100 | "
            f"{r.get('business_type', 'unknown')} | {r.get('location', 'unknown')} | "
            f"Ask ${r.get('asking_price_usd') or 0:,.0f} | {pb_str} | "
            f"Verified: {'yes' if not r.get('is_estimated') else 'no'}"
        )
    with open(FINDINGS_FILE, "a") as f:
        f.write("\n".join(lines) + "\n")


# ---------------------------------------------------------------------------
# Hard rules post-processor (fix #1 — score inflation)
# ---------------------------------------------------------------------------

def apply_hard_rules(results: list[dict]) -> list[dict]:
    """Apply tier caps and margin sanity checks after Claude scoring."""
    from research import get_industry_margin_norm, compute_weighted_score, score_to_tier, RED_FLAG_WEIGHT

    for r in results:
        if "error" in r:
            continue

        scores = r.get("scores", {})
        fin = r.get("extracted_financials", {})
        downgrades = []

        # Rule 1: owner-only (independence 1/5) → cap weighted_score at 74
        independence = scores.get("owner_independence", {}).get("score", 3)
        if independence <= 1:
            if r.get("weighted_score", 0) > 74:
                r["weighted_score"] = 74.0
                downgrades.append("owner-only: no staff (score capped at 74)")

        # Rule 2: estimated listing → cap at B-tier
        if r.get("is_estimated") or r.get("source_url", "") in ("estimated", ""):
            r["is_estimated"] = True
            if r.get("weighted_score", 0) >= 80:
                r["weighted_score"] = min(r["weighted_score"], 79.0)
                downgrades.append("estimated listing (capped at B-tier)")

        # Rule 3: margin > 2x industry norm → extra -2 penalty
        margin = fin.get("profit_margin_pct") or 0
        btype = r.get("business_type", "")
        norm = get_industry_margin_norm(btype)
        if margin and margin > (norm * 2):
            rf = scores.get("red_flags", {})
            existing_penalty = rf.get("penalty", 0)
            rf["penalty"] = existing_penalty + 2
            rf["flags"] = rf.get("flags", []) + [
                f"Margin {margin:.0f}% is {margin/norm:.1f}x the {norm}% industry norm — verify owner labor is costed"
            ]
            scores["red_flags"] = rf
            r["scores"] = scores
            new_score = compute_weighted_score(scores)
            r["weighted_score"] = new_score
            downgrades.append(f"margin {margin:.0f}% exceeds 2x norm ({norm}%) — penalty applied")

        # Recompute tier from final score
        r["tier"] = score_to_tier(r["weighted_score"])

        if downgrades:
            r["_rule_adjustments"] = downgrades

    return results


# ---------------------------------------------------------------------------
# URL verification (fix #5)
# ---------------------------------------------------------------------------

def verify_listings(grok: XaiClient, results: list[dict], top_n: int = 5) -> list[dict]:
    """Ask Grok to verify whether top candidate listings appear to be real."""
    candidates = [
        r for r in results
        if "error" not in r and r.get("source_url", "") not in ("", "estimated")
    ][:top_n]

    for candidate in candidates:
        url = candidate.get("source_url", "")
        name = candidate.get("business_name", "")
        btype = candidate.get("business_type", "")
        loc = candidate.get("location", "")

        prompt = (
            f"Search the web to verify: is there a currently active for-sale business listing matching this?\n"
            f"Business: '{name}' — a {btype} in {loc}\n"
            f"URL provided: {url}\n\n"
            f"Check if this URL exists and is an active listing, OR if a real listing matching this "
            f"description appears in current search results.\n"
            f"Reply with exactly one of:\n"
            f"VERIFIED — real active listing confirmed\n"
            f"LIKELY_REAL — similar real listings found, this appears genuine\n"
            f"UNVERIFIED — could not confirm, URL may be generated\n"
            f"Then one sentence explaining why."
        )
        try:
            from research import grok_call
            verdict = grok_call(grok, prompt)
            if verdict.startswith("VERIFIED"):
                candidate["_verified"] = "VERIFIED"
            elif verdict.startswith("LIKELY_REAL"):
                candidate["_verified"] = "LIKELY_REAL"
            else:
                candidate["_verified"] = "UNVERIFIED"
                candidate["is_estimated"] = True
            candidate["_verify_note"] = verdict
        except Exception as e:
            candidate["_verified"] = "UNKNOWN"

    return results


# ---------------------------------------------------------------------------
# Listing metadata preservation
# ---------------------------------------------------------------------------

LISTING_METADATA_FIELDS = [
    "business_type",
    "asking_price",
    "location",
    "city",
    "county",
    "distance_to_philly_miles",
    "proximity_bucket",
    "proximity_rank",
    "seller_motivation",
    "source_url",
    "listing_date",
    "_source",
]


def as_int(value) -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    cleaned = "".join(ch for ch in str(value) if ch.isdigit())
    return int(cleaned) if cleaned else None


def in_price_range(item: dict, min_budget: int, max_budget: int) -> bool:
    asking = as_int(item.get("asking_price") or item.get("asking_price_usd"))
    return asking is None or min_budget <= asking <= max_budget


def enrich_listing_for_philly(item: dict) -> dict:
    add_proximity_fields(item)
    return item


def attach_listing_metadata(result: dict, listing: dict) -> dict:
    """Keep source/proximity facts that are not part of the LLM JSON schema."""
    for field in LISTING_METADATA_FIELDS:
        value = listing.get(field)
        if value not in (None, "") and result.get(field) in (None, ""):
            result[field] = value

    result["_source"] = result.get("_source") or listing.get("_source", "")
    result["_input_business_name"] = listing.get("business_name", "")
    result["_input_location"] = listing.get("location", "")
    result["_input_source_url"] = listing.get("source_url", "")
    add_proximity_fields(result)
    return result


def assign_result_proximity_ranks(results: list[dict]) -> list[dict]:
    good = [r for r in results if "error" not in r]
    assign_proximity_ranks(good)
    return results


# ---------------------------------------------------------------------------
# Context-aware search query builder (fix #3 + #6)
# ---------------------------------------------------------------------------

def build_search_queries_with_context(
    budget: int,
    min_budget: int,
    biz_type: str,
    location: str,
    seen: dict,
    findings: str,
    rounds: int,
) -> list[str]:
    """Build search queries steered by cross-run memory and past findings."""
    loc = f" in {location}" if location else ""
    btype = f" {biz_type}" if biz_type else ""
    budget_k = budget // 1000
    min_k = max(1, min_budget // 1000)

    # Seller-finance acquisition thesis: ~$50k down, seller carries note, CF services debt
    down_k = 50  # assumed down payment in $k
    max_k = budget_k  # max total asking price
    target_range = f"${min_k}000 to ${max_k}000"

    # Base queries targeting seller-finance-friendly deals
    queries = [
        f"Philadelphia PA{btype} business for sale {target_range} seller financing owner retiring established cash flow",
        f"Philadelphia metro Bucks Montgomery Delaware Chester{btype} business for sale {target_range} seller will finance",
        f"baby boomer retiring{btype} business for sale{loc} {target_range} seller financing ${down_k}k down established cash flow",
        f"owner retiring{btype} business{loc} asking price {target_range} seller will finance motivated",
        f"retirement sale{btype} business{loc} seller carry note under ${max_k}k cash flow positive 10 years established",
        f"{btype} business for sale{loc} ${down_k}000 down seller financing 5 year note boomer owner retiring",
        f"buy{btype} business{loc} under ${max_k}000 seller financing accepted motivated seller semi-absentee staff",
        f"semi-absentee{btype} business{loc} {target_range} existing staff recurring revenue seller finance",
        f"{btype} business for sale{loc} under ${max_k}000 established route vending laundromat cleaning contracts seller note",
    ]

    # Steer away from over-explored type:location combos
    overexplored = [k for k, v in seen.items() if v.get("count", 0) >= 2]
    if overexplored:
        avoid_str = ", ".join(overexplored[:8])
        queries.append(
            f"business for sale{loc} under ${max_k}000 owner retiring seller financing — "
            f"NOT these already-explored types/locations: {avoid_str} — find something different"
        )

    # Learn from best past findings
    if findings:
        recent = "\n".join(findings.strip().splitlines()[-12:])
        queries.append(
            f"business for sale under ${max_k}000 owner retiring seller financing — "
            f"similar to these successful past finds but different locations or types:\n{recent}"
        )

    return queries[:rounds]


# ---------------------------------------------------------------------------
# Report rendering (local override with verified badges + estimated section)
# ---------------------------------------------------------------------------

TIER_ICONS = {"A": "🟢", "B": "🟡", "C": "🟠", "D": "🔴"}


def render_report(results: list[dict], deep_dives: dict, budget: int, min_budget: int = 0) -> str:
    good = [r for r in results if "error" not in r]
    assign_result_proximity_ranks(good)

    # Split verified-capable from pure estimates for separate display
    verified = [r for r in good if not r.get("is_estimated")]
    estimated = [r for r in good if r.get("is_estimated")]

    verified.sort(key=lambda x: x.get("weighted_score", 0), reverse=True)
    estimated.sort(key=lambda x: x.get("weighted_score", 0), reverse=True)

    lines = []
    lines.append("=" * 72)
    lines.append("  autobiz — Auto-Research Report")
    if min_budget:
        lines.append(f"  Asking price: ${min_budget:,}–${budget:,}  |  Goal: Fastest payback from boomer seller")
    else:
        lines.append(f"  Budget: ${budget:,}  |  Goal: Fastest payback from boomer seller")
    lines.append("=" * 72)
    lines.append(
        f"  Total scored: {len(good)}  |  "
        f"Verified/real: {len(verified)}  |  "
        f"Estimated: {len(estimated)}  |  "
        f"A-tier: {sum(1 for r in good if r.get('tier')=='A')}"
    )
    lines.append("")

    closest = sorted(
        good,
        key=lambda r: (
            r.get("distance_to_philly_miles") is None,
            r.get("distance_to_philly_miles") or 10_000,
            -r.get("weighted_score", 0),
        ),
    )[:10]
    if closest:
        lines.append("  -- Closest to Philadelphia --")
        for r in closest:
            dist = r.get("distance_to_philly_miles")
            dist_str = f"{dist} mi" if dist is not None else "unknown"
            lines.append(
                f"  #{r.get('proximity_rank', '?'):<2} {dist_str:<10} "
                f"[{r.get('tier', '?')}] {r.get('weighted_score', 0):.0f}/100  "
                f"{r.get('business_name', 'Unknown')[:45]}  |  {r.get('location', '')}"
            )
        lines.append("")

    def format_entry(i, r, show_dd=True):
        tier = r.get("tier", "?")
        score = r.get("weighted_score", 0)
        icon = TIER_ICONS.get(tier, "⚪")
        name = r.get("business_name", "Unknown")[:50]
        btype = r.get("business_type", "")
        loc = r.get("location", "")
        fin = r.get("extracted_financials", {})
        distance = r.get("distance_to_philly_miles")
        distance_str = f"{distance} mi from Philly" if distance is not None else "distance unknown"

        lines.append(f"{icon} #{i}  [{tier}] {score:.0f}/100  —  {name}")
        if btype or loc:
            lines.append(f"     {btype}  |  {loc}")
        lines.append(
            f"     Proximity: #{r.get('proximity_rank', '?')} closest  |  "
            f"{distance_str}  |  {r.get('proximity_bucket', 'unknown distance')}"
        )
        lines.append("-" * 72)

        # Rule adjustments note
        adjustments = r.get("_rule_adjustments", [])
        if adjustments:
            lines.append(f"  ↓ Adjusted: {'; '.join(adjustments)}")

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

        margin_flag = r.get("margin_sanity_flag")
        if margin_flag:
            lines.append(f"  ⚠ Margin: {margin_flag}")

        pb = r.get("payback_projection", "")
        if pb:
            lines.append(f"  Payback:    {pb}")

        bs = r.get("boomer_signal", "")
        if bs and bs != "None detected":
            lines.append(f"  Seller:     {bs}")

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

        flag_list = flags.get("flags", [])
        if flag_list:
            lines.append(f"  ⚠ Flags: {'; '.join(flag_list)}")

        neg = r.get("negotiation_note", "")
        if neg:
            lines.append(f"  Negotiate: {neg}")

        url = r.get("source_url", "")
        verified_badge = r.get("_verified", "")
        badge_str = ""
        if verified_badge == "VERIFIED":
            badge_str = " [VERIFIED ✓]"
        elif verified_badge == "LIKELY_REAL":
            badge_str = " [LIKELY_REAL]"
        elif r.get("is_estimated"):
            badge_str = " [ESTIMATED]"

        if url and url not in ("", "estimated"):
            lines.append(f"  Source:   {url}{badge_str}")
        else:
            lines.append(f"  Source:   [market estimate — verify independently]")

        if show_dd and r.get("business_name") in deep_dives:
            dd = deep_dives[r["business_name"]]
            lines.append("")
            lines.append("  --- Due Diligence Brief (Grok) ---")
            for ddline in dd.split("\n"):
                lines.append(f"  {ddline}")

        lines.append("")

    # Main section: verified/real listings
    if verified:
        lines.append("  ── Verified / Real Listings ──")
        lines.append("")
        for i, r in enumerate(verified, 1):
            format_entry(i, r, show_dd=(i <= 3))

    # Estimated section
    if estimated:
        lines.append("  ── Market Estimates (unverified — use for benchmarking only) ──")
        lines.append("")
        for i, r in enumerate(estimated, 1):
            format_entry(i, r, show_dd=False)

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


def list_runs() -> list[dict]:
    """Return git log entries that are research run commits."""
    log = git("log", "--oneline", "--grep=autobiz-run", "--format=%H %ai %s")
    runs = []
    for line in log.splitlines():
        if line.strip():
            parts = line.split(" ", 3)
            runs.append({"sha": parts[0], "date": parts[1], "msg": parts[-1]})
    return runs


def show_run(sha: str) -> str:
    """Print the report from a past run commit."""
    files = git("show", "--name-only", "--format=", sha)
    report_path = None
    for f in files.splitlines():
        if f.endswith("report.txt"):
            report_path = f
            break
    if not report_path:
        return f"No report found in commit {sha}"
    return git("show", f"{sha}:{report_path}")


# ---------------------------------------------------------------------------
# Parallel search agent
# ---------------------------------------------------------------------------

def search_agent(grok: XaiClient, query: str, agent_id: int) -> tuple[int, list[dict]]:
    """A single search agent. Returns (agent_id, listings)."""
    listings = discover_listings(grok, query, verbose=False)
    return agent_id, listings


# ---------------------------------------------------------------------------
# Parallel scoring agent
# ---------------------------------------------------------------------------

def scoring_agent(
    claude: anthropic.Anthropic,
    grok: XaiClient,
    business: dict,
    program: str,
    budget: int,
    agent_id: int,
) -> tuple[int, dict]:
    """A single scoring agent. Returns (agent_id, result)."""
    market_context = market_enrich(grok, business)
    prompt = build_scoring_prompt(business, program, market_context, budget)

    try:
        from config import llm_score_call
        raw = llm_score_call(prompt)
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:].strip()
        result = json.loads(raw)
        if "scores" in result:
            result["weighted_score"] = compute_weighted_score(result["scores"])
            result["tier"] = score_to_tier(result["weighted_score"])
        return agent_id, result
    except json.JSONDecodeError as e:
        return agent_id, {
            "business_name": business.get("business_name", "Unknown"),
            "error": f"JSON parse failed: {e}",
            "weighted_score": 0, "tier": "D",
        }
    except Exception as e:
        return agent_id, {
            "business_name": business.get("business_name", "Unknown"),
            "error": f"Scoring error: {e}",
            "weighted_score": 0, "tier": "D",
        }


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def orchestrate(
    claude: anthropic.Anthropic,
    grok: XaiClient,
    budget: int,
    min_budget: int,
    rounds: int,
    biz_type: str,
    location: str,
    no_deep_dive: bool,
    no_commit: bool,
    from_json: str = None,
) -> None:
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    run_dir = RUNS_DIR / timestamp
    run_dir.mkdir(parents=True, exist_ok=True)

    program = load_program()
    seen = load_seen()
    findings = load_findings()

    print("=" * 72)
    print("  autobiz — Multi-Agent Research Orchestrator")
    print(f"  Asking price: ${min_budget:,}–${budget:,}  |  Agents: {rounds} search + parallel scoring")
    if biz_type:
        print(f"  Type: {biz_type}")
    if location:
        print(f"  Location: {location}")
    print(f"  Run ID: {timestamp}")
    print("=" * 72)
    print()

    # --- Phase 1: Discovery (search agents OR pre-scraped JSON) ---
    if from_json:
        print(f"[ Discovery ] Loading pre-scraped listings from {from_json}...")
        with open(from_json) as f:
            raw = json.load(f)
        # Normalize field names from scraper.py format if needed
        listings = []
        for item in raw:
            # scraper.py uses cash_flow_annual / gross_revenue_annual; research.py expects
            # annual_cash_flow / annual_revenue — map if needed
            normalized = dict(item)
            if "cash_flow_annual" in normalized and "annual_cash_flow" not in normalized:
                normalized["annual_cash_flow"] = normalized["cash_flow_annual"]
            if "gross_revenue_annual" in normalized and "annual_revenue" not in normalized:
                normalized["annual_revenue"] = normalized["gross_revenue_annual"]
            enrich_listing_for_philly(normalized)
            listings.append(normalized)
        listings = deduplicate(listings)
        listings = [item for item in listings if in_price_range(item, min_budget, budget)]
        assign_proximity_ranks(listings)
        print(f"  Loaded {len(listings)} unique listings from file\n")
    else:
        queries = build_search_queries_with_context(budget, min_budget, biz_type, location, seen, findings, rounds)
        print(f"[ SearchAgents ] Launching {len(queries)} parallel search agents via Grok...")
        all_listings: list[dict] = []

        with ThreadPoolExecutor(max_workers=len(queries)) as executor:
            futures = {
                executor.submit(search_agent, grok, q, i): i
                for i, q in enumerate(queries, 1)
            }
            for future in as_completed(futures):
                agent_id, llist = future.result()
                print(f"  Agent-{agent_id}: found {len(llist)} listings")
                all_listings.extend(llist)

        listings = deduplicate(all_listings)
        for item in listings:
            enrich_listing_for_philly(item)
        listings = [item for item in listings if in_price_range(item, min_budget, budget)]
        assign_proximity_ranks(listings)
        print(f"  Deduplicated: {len(listings)} unique listings\n")

    if not listings:
        print("No listings found. Adjust --type or --location.")
        return

    # Save raw discovery to run dir
    with open(run_dir / "discovered.json", "w") as f:
        json.dump(listings, f, indent=2)

    # --- Phase 2: Parallel Scoring Agents ---
    print(f"[ ScoringAgents ] Launching {len(listings)} parallel scoring agents (Claude + Grok)...")
    results: list[dict] = [None] * len(listings)

    with ThreadPoolExecutor(max_workers=min(5, len(listings))) as executor:
        futures = {
            executor.submit(scoring_agent, claude, grok, biz, program, budget, i): i
            for i, biz in enumerate(listings)
        }
        completed = 0
        for future in as_completed(futures):
            agent_id, result = future.result()
            results[agent_id] = attach_listing_metadata(result, listings[agent_id])
            completed += 1
            name = result.get("business_name", "Unknown")[:45]
            score = result.get("weighted_score", 0)
            tier = result.get("tier", "?")
            print(f"  [{completed}/{len(listings)}] [{tier}] {score:.0f}  {name}")

    results = [r for r in results if r is not None]
    results.sort(key=lambda x: x.get("weighted_score", 0), reverse=True)

    # Apply hard rules (score inflation fix)
    results = apply_hard_rules(results)
    assign_result_proximity_ranks(results)
    results.sort(key=lambda x: x.get("weighted_score", 0), reverse=True)

    top_ab = [r for r in results if r.get("tier") in ("A", "B")]
    print(f"\n  After hard rules — Tier A/B: {len(top_ab)}\n")

    # Verify top candidate URLs
    print("[ VerifyAgent ] Checking top candidate URLs via Grok...")
    results = verify_listings(grok, results, top_n=5)
    print()

    # Save scored results
    with open(run_dir / "scored.json", "w") as f:
        json.dump(results, f, indent=2)

    # --- Phase 3: Deep Dive Agent on Top 3 ---
    deep_dives: dict = {}
    if not no_deep_dive:
        top3 = [r for r in results if "error" not in r][:3]
        if top3:
            print(f"[ DeepDiveAgent ] Running Grok due diligence on top {len(top3)} candidates...")
            for candidate in top3:
                name = candidate.get("business_name", "Unknown")
                print(f"  Deep dive: {name[:55]}...")
                deep_dives[name] = deep_dive(grok, candidate)
                time.sleep(0.3)
            print()

    # Save deep dives
    if deep_dives:
        with open(run_dir / "deep_dives.json", "w") as f:
            json.dump(deep_dives, f, indent=2)

    # --- Generate Report ---
    report = render_report(results, deep_dives, budget, min_budget=min_budget)
    print(report)

    with open(run_dir / "report.txt", "w") as f:
        f.write(report)

    # Update cross-run memory
    seen = update_seen(seen, results)
    save_seen(seen)
    save_findings(results, timestamp, budget)

    # Save run metadata
    meta = {
        "timestamp": timestamp,
        "budget": budget,
        "min_budget": min_budget,
        "biz_type": biz_type,
        "location": location,
        "rounds": rounds,
        "total_found": len(listings),
        "unique_listings": len(listings),
        "scored": len(results),
        "tier_a": sum(1 for r in results if r.get("tier") == "A"),
        "tier_b": sum(1 for r in results if r.get("tier") == "B"),
        "tier_c": sum(1 for r in results if r.get("tier") == "C"),
        "tier_d": sum(1 for r in results if r.get("tier") == "D"),
        "top_pick": results[0].get("business_name") if results else None,
        "top_score": results[0].get("weighted_score") if results else None,
        "top_payback": (results[0].get("extracted_financials") or {}).get("payback_years") if results else None,
    }
    with open(run_dir / "meta.json", "w") as f:
        json.dump(meta, f, indent=2)

    # --- Git Agent: commit the run ---
    if not no_commit:
        top = results[0] if results else {}
        top_name = top.get("business_name", "unknown")[:40]
        top_score = top.get("weighted_score", 0)
        top_tier = top.get("tier", "?")
        top_payback = (top.get("extracted_financials") or {}).get("payback_years")
        payback_str = f"{top_payback:.1f}yr payback" if top_payback else "payback unknown"

        commit_msg = (
            f"autobiz-run {timestamp}\n\n"
            f"Asking price: ${min_budget:,}-${budget:,} | Type: {biz_type or 'any'} | Location: {location or 'any'}\n"
            f"Found: {len(listings)} listings | Scored: {len(results)} | A/B: {len(top_ab)}\n"
            f"Top pick: [{top_tier}] {top_score:.0f}/100 — {top_name} ({payback_str})"
            f"\nSeen fingerprints total: {len(seen)}"
        )

        sha = git_commit_run(run_dir, commit_msg)
        print(f"\n[ GitAgent ] Run committed: {sha}")

        # Tag if we found any A-tier businesses
        if meta["tier_a"] > 0:
            tag_name = f"A-tier-{timestamp}"
            git_tag(tag_name, f"{meta['tier_a']} A-tier business(es) found — {top_name}")
            print(f"[ GitAgent ] Tagged: {tag_name} (A-tier find)")
    else:
        print(f"\n[ GitAgent ] Skipped commit (--no-commit). Run saved to: {run_dir}")

    print(f"\n  Run artifacts: {run_dir}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="autobiz agent — autonomous multi-agent business research")
    parser.add_argument("--budget", type=int, default=None, help="Max asking price to consider (default: saved config). Down payment ~$50k, seller finances balance.")
    parser.add_argument("--min-budget", type=int, default=None, help="Min asking price to consider (default: saved config).")
    parser.add_argument("--rounds", type=int, default=3, help="Parallel search agents to run (default: 3)")
    parser.add_argument("--type", type=str, default="", dest="biz_type")
    parser.add_argument("--location", type=str, default=None)
    parser.add_argument("--no-deep-dive", action="store_true")
    parser.add_argument("--no-commit", action="store_true", help="Don't commit to git (dry run)")
    parser.add_argument("--from-json", type=str, metavar="FILE", help="Skip discovery — score listings from a pre-scraped JSON file")
    parser.add_argument("--list-runs", action="store_true", help="Show git log of past research runs")
    parser.add_argument("--show-run", type=str, metavar="SHA", help="Print report from a past run")
    args = parser.parse_args()

    # Show past runs
    if args.list_runs:
        runs = list_runs()
        if not runs:
            print("No research runs in git history yet.")
        else:
            print(f"{'SHA':<10} {'Date':<22} Message")
            print("-" * 72)
            for r in runs:
                print(f"{r['sha']:<10} {r['date']:<22} {r['msg']}")
        return

    if args.show_run:
        print(show_run(args.show_run))
        return

    # API clients — reads from config.json first, falls back to env vars
    from config import load_config, get_research_client, llm_score_call
    app_cfg = load_config()
    args.location = args.location if args.location is not None else app_cfg["defaults"].get("location", "Pennsylvania")
    args.budget = args.budget if args.budget is not None else int(app_cfg["defaults"].get("budget_max", 250000))
    args.min_budget = args.min_budget if args.min_budget is not None else int(app_cfg["defaults"].get("budget_min", 75000))
    try:
        grok = get_research_client(app_cfg)
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)
    try:
        _, claude = __import__("config").get_scoring_client(app_cfg)
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)

    orchestrate(
        claude=claude,
        grok=grok,
        budget=args.budget,
        min_budget=args.min_budget,
        rounds=args.rounds,
        biz_type=args.biz_type,
        location=args.location,
        no_deep_dive=args.no_deep_dive,
        no_commit=args.no_commit,
        from_json=args.from_json,
    )


if __name__ == "__main__":
    main()
