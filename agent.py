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
from openai import OpenAI

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
    render_report,
    score_to_tier,
    CLAUDE_MODEL,
    GROK_MODEL,
    XAI_BASE_URL,
    MAX_RETRIES,
    RETRY_DELAY,
)

RUNS_DIR = Path(__file__).parent / "runs"

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
    git("add", str(run_dir.relative_to(Path(__file__).parent)))
    git("commit", "-m", message, "--no-gpg-sign")
    return git("rev-parse", "--short", "HEAD")


def git_tag(tag: str, message: str):
    git("tag", "-a", tag, "-m", message, "--no-sign")


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

def search_agent(grok: OpenAI, query: str, agent_id: int) -> tuple[int, list[dict]]:
    """A single search agent. Returns (agent_id, listings)."""
    listings = discover_listings(grok, query, verbose=False)
    return agent_id, listings


# ---------------------------------------------------------------------------
# Parallel scoring agent
# ---------------------------------------------------------------------------

def scoring_agent(
    claude: anthropic.Anthropic,
    grok: OpenAI,
    business: dict,
    program: str,
    budget: int,
    agent_id: int,
) -> tuple[int, dict]:
    """A single scoring agent. Returns (agent_id, result)."""
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
            return agent_id, result
        except (anthropic.RateLimitError, anthropic.APIStatusError):
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY)
            else:
                return agent_id, {
                    "business_name": business.get("business_name", "Unknown"),
                    "error": "API error after retries",
                    "weighted_score": 0, "tier": "D",
                }
        except json.JSONDecodeError as e:
            if attempt == MAX_RETRIES - 1:
                return agent_id, {
                    "business_name": business.get("business_name", "Unknown"),
                    "error": f"JSON parse failed: {e}",
                    "weighted_score": 0, "tier": "D",
                }


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def orchestrate(
    claude: anthropic.Anthropic,
    grok: OpenAI,
    budget: int,
    rounds: int,
    biz_type: str,
    location: str,
    no_deep_dive: bool,
    no_commit: bool,
) -> None:
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    run_dir = RUNS_DIR / timestamp
    run_dir.mkdir(parents=True, exist_ok=True)

    program = load_program()
    queries = build_search_queries(budget, biz_type, location)[:rounds]

    print("=" * 72)
    print("  autobiz — Multi-Agent Research Orchestrator")
    print(f"  Budget: ${budget:,}  |  Agents: {rounds} search + parallel scoring")
    if biz_type:
        print(f"  Type: {biz_type}")
    if location:
        print(f"  Location: {location}")
    print(f"  Run ID: {timestamp}")
    print("=" * 72)
    print()

    # --- Phase 1: Parallel Search Agents ---
    print(f"[ SearchAgents ] Launching {len(queries)} parallel search agents via Grok...")
    all_listings: list[dict] = []

    with ThreadPoolExecutor(max_workers=len(queries)) as executor:
        futures = {
            executor.submit(search_agent, grok, q, i): i
            for i, q in enumerate(queries, 1)
        }
        for future in as_completed(futures):
            agent_id, listings = future.result()
            print(f"  Agent-{agent_id}: found {len(listings)} listings")
            all_listings.extend(listings)

    listings = deduplicate(all_listings)
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
            results[agent_id] = result
            completed += 1
            name = result.get("business_name", "Unknown")[:45]
            score = result.get("weighted_score", 0)
            tier = result.get("tier", "?")
            print(f"  [{completed}/{len(listings)}] [{tier}] {score:.0f}  {name}")

    results = [r for r in results if r is not None]
    results.sort(key=lambda x: x.get("weighted_score", 0), reverse=True)

    top_ab = [r for r in results if r.get("tier") in ("A", "B")]
    print(f"\n  Tier A/B candidates: {len(top_ab)}\n")

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
    report = render_report(results, deep_dives, budget)
    print(report)

    with open(run_dir / "report.txt", "w") as f:
        f.write(report)

    # Save run metadata
    meta = {
        "timestamp": timestamp,
        "budget": budget,
        "biz_type": biz_type,
        "location": location,
        "rounds": rounds,
        "total_found": len(all_listings),
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
            f"Budget: ${budget:,} | Type: {biz_type or 'any'} | Location: {location or 'any'}\n"
            f"Found: {len(listings)} listings | Scored: {len(results)} | A/B: {len(top_ab)}\n"
            f"Top pick: [{top_tier}] {top_score:.0f}/100 — {top_name} ({payback_str})"
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
    parser.add_argument("--budget", type=int, default=50000)
    parser.add_argument("--rounds", type=int, default=3, help="Parallel search agents to run (default: 3)")
    parser.add_argument("--type", type=str, default="", dest="biz_type")
    parser.add_argument("--location", type=str, default="")
    parser.add_argument("--no-deep-dive", action="store_true")
    parser.add_argument("--no-commit", action="store_true", help="Don't commit to git (dry run)")
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
    grok = OpenAI(api_key=xai_key, base_url=XAI_BASE_URL)

    orchestrate(
        claude=claude,
        grok=grok,
        budget=args.budget,
        rounds=args.rounds,
        biz_type=args.biz_type,
        location=args.location,
        no_deep_dive=args.no_deep_dive,
        no_commit=args.no_commit,
    )


if __name__ == "__main__":
    main()
