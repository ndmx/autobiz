# autobiz Runbook

This runbook covers the normal local workflow for Philadelphia-first,
Pennsylvania-wide acquisition research.

## Setup

Install dependencies:

```bash
cd /Users/ndmx0/AI/autobiz
uv sync
```

API keys can live in either shell env, root `.env`, or `env/.env`.
`config.py` loads root `.env` and `env/.env` automatically without overriding
already-exported shell variables.

Expected key names:

```text
ANTHROPIC_API_KEY
XAI_API_KEY
OPENAI_API_KEY
GEMINI_API_KEY
```

Check that the project can see keys without printing values:

```bash
uv run python - <<'PY'
import os
import config
for name in ["ANTHROPIC_API_KEY", "XAI_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY"]:
    print(name, bool(os.environ.get(name)))
PY
```

## Dashboard and Settings UI

Start the dashboard:

```bash
uv run app.py
```

It opens in your default browser:

```text
http://localhost:7860/dashboard
```

Use the dashboard to compare score, Philly distance, source, financial
confidence, and deal terms. Use the settings link for provider/model defaults,
API key testing, default location, and default asking-price range.

For terminal-only or automation runs:

```bash
AUTOBIZ_NO_BROWSER=1 uv run app.py
```

Scored agent runs also save a static visual report at:

```text
runs/<timestamp>/dashboard.html
```

## Source-Backed Scrape

Pull Philly-first, PA-wide listings:

```bash
uv run scraper.py --location "Pennsylvania" --min-budget 75000 --budget 250000 \
  --output data_pa_wide.csv --json data_pa_wide.json
```

For a cheap direct-source-only check without Grok-proxied pages:

```bash
uv run scraper.py --location "Pennsylvania" --min-budget 75000 --budget 250000 \
  --no-grok --output /tmp/autobiz_pa_cl.csv --json /tmp/autobiz_pa_cl.json
```

## Score Pre-Scraped Listings

Score a JSON scrape with the multi-agent orchestrator:

```bash
uv run agent.py --from-json data_pa_wide.json --location "Pennsylvania" \
  --min-budget 75000 --budget 250000 --no-commit
```

Useful orchestration controls:

```bash
# More conservative API usage
uv run agent.py --from-json data_pa_wide.json --scoring-workers 2 --verify-top 3 --no-commit

# Skip URL verification for faster local scoring
uv run agent.py --from-json data_pa_wide.json --verify-top 0 --no-commit

# Skip deep dives but keep scoring and verification
uv run agent.py --from-json data_pa_wide.json --no-deep-dive --no-commit
```

## Autonomous Discovery

Run parallel Grok search agents directly:

```bash
uv run agent.py --location "Pennsylvania" --min-budget 75000 --budget 250000 \
  --rounds 5 --scoring-workers 3 --no-commit
```

Use `--type` to focus a run:

```bash
uv run agent.py --location "Pennsylvania" --type "vending route" \
  --min-budget 75000 --budget 250000 --rounds 5 --no-commit
```

## Run Artifacts

Each run writes:

```text
runs/<timestamp>/discovered.json
runs/<timestamp>/scored.json
runs/<timestamp>/deep_dives.json
runs/<timestamp>/report.txt
runs/<timestamp>/meta.json
```

Use `meta.json` first when comparing runs. It contains tier counts, source
breakdowns, proximity breakdowns, timing, and top-pick summary.

## Past Runs

List committed research runs:

```bash
uv run agent.py --list-runs
```

Show a committed report:

```bash
uv run agent.py --show-run <sha>
```

## Validation Checklist

Before pushing code changes:

```bash
uv run python -m py_compile app.py config.py analyze.py research.py scraper.py agent.py proximity.py listing_utils.py reporting.py
```

Before trusting a dataset:

```bash
python3 - <<'PY'
import csv, json
rows = list(csv.DictReader(open("data_pa_wide.csv", encoding="utf-8")))
data = json.load(open("data_pa_wide.json"))
print("csv_rows", len(rows), "json_rows", len(data))
print("placeholder_rows", sum(1 for r in rows if r.get("business_name") == "string"))
print("has_proximity", {"distance_to_philly_miles", "proximity_rank"}.issubset(rows[0].keys()))
PY
```
