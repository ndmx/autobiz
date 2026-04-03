# autobiz

*autoresearch, but for businesses.*

Give autobiz a CSV of business listings and it autonomously analyzes each one against a set of viability parameters — extracting financials buried in descriptions, scoring every dimension, and ranking the batch from strongest to weakest.

## How it works

Three files that matter:

- **`program.md`** — the scoring framework. Edit this to tune what "strong business" means to you. This is your human-facing lever.
- **`analyze.py`** — the engine. Reads the CSV, calls Claude to extract financials and score each business, renders a ranked report. Don't edit this unless you want to change the tool itself.
- **`pyproject.toml`** — dependencies (just `anthropic`).

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
