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
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import anthropic
from xai_sdk import Client as XaiClient

from listing_utils import (
    assign_result_proximity_ranks,
    attach_listing_metadata,
    enrich_listing_for_philly,
    financial_confidence,
    financial_value_present,
    filter_and_rank_listings,
    proximity_breakdown,
    source_breakdown,
)
from reporting import render_agent_report

# Re-use all logic from research.py
from research import (
    build_scoring_prompt,
    compute_weighted_score,
    deep_dive,
    deduplicate,
    discover_listings,
    load_program,
    market_enrich,
    score_to_tier,
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
    from research import get_industry_margin_norm, compute_weighted_score, score_to_tier

    for r in results:
        if "error" in r:
            continue

        scores = r.get("scores", {})
        fin = r.get("extracted_financials", {})
        downgrades = []
        confidence = r.get("financial_confidence") or financial_confidence(r)
        r["financial_confidence"] = confidence
        cash_flow = fin.get("cash_flow_annual") or fin.get("annual_cash_flow") or r.get("cash_flow_annual")

        red_flags = scores.get("red_flags", {})
        flags = red_flags.get("flags", [])
        if not isinstance(flags, list):
            flags = [str(flags)]

        def add_flag(message: str, penalty: int = 0) -> None:
            red_flags["flags"] = flags
            flags.append(message)
            if penalty:
                red_flags["penalty"] = red_flags.get("penalty", 0) + penalty
            scores["red_flags"] = red_flags
            r["scores"] = scores

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

        # Rule 3: missing cash flow cannot rank as a top acquisition candidate.
        if not financial_value_present(cash_flow):
            if r.get("weighted_score", 0) > 69:
                r["weighted_score"] = 69.0
                downgrades.append("missing verified cash flow (score capped at 69)")
            add_flag("Missing verified cash flow: cannot rank as top-tier until owner benefit is confirmed")

        # Rule 4: low financial evidence cannot rank above a diligence queue.
        if confidence["score"] < 30:
            if r.get("weighted_score", 0) > 49:
                r["weighted_score"] = 49.0
                downgrades.append("very low financial confidence (score capped at 49)")
            add_flag("Very low financial confidence: verify asking price, cash flow, and revenue before ranking")
        elif confidence["score"] < 55:
            cap = 59.0 if not cash_flow else 69.0
            if r.get("weighted_score", 0) > cap:
                r["weighted_score"] = cap
                label = "missing cash flow" if not cash_flow else "low financial confidence"
                downgrades.append(f"{label} (score capped at {cap:.0f})")
            add_flag("Low financial confidence: hard financials are incomplete")

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

@contextmanager
def timed_stage(name: str, timings: dict[str, float]):
    started = time.monotonic()
    try:
        yield
    finally:
        timings[name] = round(time.monotonic() - started, 2)


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
    scoring_workers: int = 5,
    verify_top: int = 5,
    from_json: str = None,
) -> None:
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    run_dir = RUNS_DIR / timestamp
    run_dir.mkdir(parents=True, exist_ok=True)

    program = load_program()
    seen = load_seen()
    findings = load_findings()
    stage_timings: dict[str, float] = {}

    print("=" * 72)
    print("  autobiz — Multi-Agent Research Orchestrator")
    print(
        f"  Asking price: ${min_budget:,}–${budget:,}  |  "
        f"Agents: {rounds} search + {scoring_workers} scoring"
    )
    if biz_type:
        print(f"  Type: {biz_type}")
    if location:
        print(f"  Location: {location}")
    print(f"  Run ID: {timestamp}")
    print("=" * 72)
    print()

    # --- Phase 1: Discovery (search agents OR pre-scraped JSON) ---
    with timed_stage("discovery", stage_timings):
        if from_json:
            print(f"[ Discovery ] Loading pre-scraped listings from {from_json}...")
            with open(from_json) as f:
                raw = json.load(f)
            listings = []
            for item in raw:
                normalized = dict(item)
                if "cash_flow_annual" in normalized and "annual_cash_flow" not in normalized:
                    normalized["annual_cash_flow"] = normalized["cash_flow_annual"]
                if "gross_revenue_annual" in normalized and "annual_revenue" not in normalized:
                    normalized["annual_revenue"] = normalized["gross_revenue_annual"]
                enrich_listing_for_philly(normalized)
                listings.append(normalized)
            listings = filter_and_rank_listings(deduplicate(listings), min_budget, budget)
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

            listings = filter_and_rank_listings(deduplicate(all_listings), min_budget, budget)
            print(f"  Deduplicated: {len(listings)} unique listings\n")

    if not listings:
        print("No listings found. Adjust --type or --location.")
        return

    # Save raw discovery to run dir
    with open(run_dir / "discovered.json", "w") as f:
        json.dump(listings, f, indent=2)

    # --- Phase 2: Parallel Scoring Agents ---
    with timed_stage("scoring", stage_timings):
        print(f"[ ScoringAgents ] Launching {len(listings)} scoring jobs (Claude + Grok)...")
        results: list[dict] = [None] * len(listings)
        max_workers = max(1, min(scoring_workers, len(listings)))

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
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
        results = apply_hard_rules(results)
        assign_result_proximity_ranks(results)
        results.sort(key=lambda x: x.get("weighted_score", 0), reverse=True)

    top_ab = [r for r in results if r.get("tier") in ("A", "B")]
    print(f"\n  After hard rules — Tier A/B: {len(top_ab)}\n")

    # Verify top candidate URLs
    with timed_stage("verification", stage_timings):
        if verify_top > 0:
            print(f"[ VerifyAgent ] Checking top {verify_top} candidate URLs via Grok...")
            results = verify_listings(grok, results, top_n=verify_top)
        else:
            print("[ VerifyAgent ] Skipped (--verify-top 0).")
        print()

    # Save scored results
    with open(run_dir / "scored.json", "w") as f:
        json.dump(results, f, indent=2)

    # --- Phase 3: Deep Dive Agent on Top 3 ---
    deep_dives: dict = {}
    with timed_stage("deep_dive", stage_timings):
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
    report = render_agent_report(results, deep_dives, budget, min_budget=min_budget)
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
        "scoring_workers": scoring_workers,
        "verify_top": verify_top,
        "total_found": len(listings),
        "unique_listings": len(listings),
        "scored": len(results),
        "stage_seconds": stage_timings,
        "source_breakdown": source_breakdown(listings),
        "proximity_breakdown": proximity_breakdown(listings),
        "tier_a": sum(1 for r in results if r.get("tier") == "A"),
        "tier_b": sum(1 for r in results if r.get("tier") == "B"),
        "tier_c": sum(1 for r in results if r.get("tier") == "C"),
        "tier_d": sum(1 for r in results if r.get("tier") == "D"),
        "top_pick": results[0].get("business_name") if results else None,
        "top_score": results[0].get("weighted_score") if results else None,
        "closest_distance_miles": min(
            (r["distance_to_philly_miles"] for r in results if r.get("distance_to_philly_miles") is not None),
            default=None,
        ),
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
    parser.add_argument("--scoring-workers", type=int, default=5, help="Parallel scoring workers to run (default: 5)")
    parser.add_argument("--verify-top", type=int, default=5, help="Top candidate URLs to verify via Grok; use 0 to skip (default: 5)")
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
        scoring_workers=args.scoring_workers,
        verify_top=args.verify_top,
        from_json=args.from_json,
    )


if __name__ == "__main__":
    main()
