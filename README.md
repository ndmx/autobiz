# autobiz

*autoresearch, but for businesses.*

Give autobiz a CSV of business listings and it autonomously analyzes each one against a set of viability parameters — extracting financials buried in descriptions, scoring every dimension, and ranking the batch from strongest to weakest.

## How it works

Core files:

- **`program.md`** — the scoring framework. Edit this to tune what "strong business" means to you. This is your human-facing lever.
- **`scraper.py`** — source-backed listing collection, Philadelphia-first and Pennsylvania-wide.
- **`agent.py`** — multi-agent orchestration: discovery, scoring, verification, deep dives, reports, and run artifacts.
- **`listing_utils.py`** — shared listing filtering, metadata preservation, and proximity ranking helpers.
- **`proximity.py`** — approximate distance-to-Philadelphia enrichment.
- **`reporting.py`** — text report rendering.
- **`config.py`** — config, `.env` loading, and provider client factories.
- **`pyproject.toml`** — dependencies.

## Documentation

- [Architecture](docs/ARCHITECTURE.md)
- [Runbook](docs/RUNBOOK.md)
- [Data Quality](docs/DATA_QUALITY.md)
- [Extending autobiz](docs/EXTENDING.md)

## Quick start

```bash
# 1. Set your API key
export ANTHROPIC_API_KEY=sk-ant-...

# 2. Install uv if you don't have it
curl -LsSf https://astral.sh/uv/install.sh | sh

# 3. Install dependencies
cd autobiz && uv sync

# 4. Run on the CSV
uv run analyze.py --csv ../j_mnirekmw25v4gb0tgn.csv

# Show only top 10
uv run analyze.py --csv ../j_mnirekmw25v4gb0tgn.csv --top 10

# Filter to Tier A only
uv run analyze.py --csv ../j_mnirekmw25v4gb0tgn.csv --tier A

# Save results
uv run analyze.py --csv ../j_mnirekmw25v4gb0tgn.csv --output results.json --report report.txt

# Test on first 5 listings
uv run analyze.py --csv ../j_mnirekmw25v4gb0tgn.csv --limit 5
```

## Philadelphia-first, PA-wide workflow

The scraper now prioritizes Philadelphia and the nearby Bucks / Montgomery /
Delaware / Chester county market, then sweeps statewide Pennsylvania sources.
Every listing gets approximate proximity fields:

- `distance_to_philly_miles`
- `proximity_bucket`
- `proximity_rank`

```bash
# Pull source-backed PA listings, Philly-first
uv run scraper.py --location "Pennsylvania" --min-budget 75000 --budget 250000 \
  --output data_pa_wide.csv --json data_pa_wide.json

# Score the scraped listings and include a closest-to-Philadelphia ranking
uv run agent.py --from-json data_pa_wide.json --location "Pennsylvania" \
  --min-budget 75000 --budget 250000 --no-commit
```

Useful orchestration controls:

```bash
# Limit parallel scoring and verify fewer URLs
uv run agent.py --from-json data_pa_wide.json --scoring-workers 2 --verify-top 3 --no-commit

# Fast local run shape check without URL verification or deep dives
uv run agent.py --from-json data_pa_wide.json --verify-top 0 --no-deep-dive --no-commit
```

## Scoring Parameters

| Parameter           | Weight |
|---------------------|--------|
| ROI / Payback       | 25%    |
| Revenue Multiple    | 15%    |
| Profit Margin       | 15%    |
| Business Age        | 15%    |
| Owner Independence  | 15%    |
| Market Position     | 10%    |
| Red Flag Penalties  | up to -25 |

Each parameter is scored 1–5. Final score is weighted and expressed out of 100.

**Tiers:**
- 🟢 **A** (80–100): Strong buy candidate
- 🟡 **B** (60–79): Worth deeper diligence
- 🟠 **C** (40–59): Significant concerns
- 🔴 **D** (<40): Avoid or investigate heavily

## Tuning

Edit `program.md` to change:
- What scores mean (the rubrics)
- What constitutes a red flag
- Analyst notes for your specific batch

The framework in `program.md` is passed verbatim to Claude as the evaluation lens, so plain-English changes take effect immediately on the next run.
