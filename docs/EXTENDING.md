# Extending autobiz

Use this guide when adding sources, scoring dimensions, or orchestration stages.

## Extension Principles

- Keep source collection separate from scoring.
- Preserve raw source facts before asking an LLM to interpret them.
- Add shared listing transformations to `listing_utils.py`, not `agent.py`.
- Add proximity-only behavior to `proximity.py`.
- Add report formatting to `reporting.py`.
- Keep `program.md` as the first place to tune investment judgment.

## Add a New Listing Source

Best target file:

```text
scraper.py
```

Recommended shape:

```python
def scrape_new_source(location: str, min_price: int, max_price: int, verbose: bool) -> list[dict]:
    return [
        {
            "business_name": "...",
            "business_type": "...",
            "asking_price": 125000,
            "location": "Philadelphia, PA",
            "cash_flow_annual": None,
            "gross_revenue_annual": None,
            "year_established": None,
            "employees": "",
            "description": "...",
            "seller_motivation": "",
            "source_url": "https://...",
            "listing_date": "",
            "_source": "NewSource",
        }
    ]
```

Then feed the records into `normalize_listings()` so dedupe, placeholder
filtering, proximity enrichment, and budget filtering stay consistent.

## Add a New Proximity Location

Best target file:

```text
proximity.py
```

Add common cities to `LOCATION_COORDS` and counties to `COUNTY_COORDS`.
Use approximate city-center or county-seat coordinates unless exact locations
are available.

## Add a New Scoring Rule

If the rule is judgment/rubric text, start in:

```text
program.md
```

If the rule is deterministic and should override the LLM, add it to:

```text
agent.py::apply_hard_rules()
```

Examples of deterministic rules:

- cap A-tier when owner independence is too low
- penalize unrealistic margins
- disqualify missing financials
- cap unverified or estimated records

## Add a New Report Section

Best target file:

```text
reporting.py
```

Keep report rendering read-only. It should not mutate scoring results except
for harmless presentation fields such as proximity ranks.

## Add a New Agent Stage

Best target file:

```text
agent.py
```

Use the existing stage pattern:

```python
with timed_stage("stage_name", stage_timings):
    ...
```

Save any durable output into the current run directory and add a summary to
`meta.json`.

## Add a New CLI Knob

1. Add the flag in `main()`.
2. Pass it explicitly into `orchestrate()`.
3. Save it in `meta.json`.
4. Document it in `docs/RUNBOOK.md`.

## Definition of Done

Before opening a PR:

```bash
uv run python -m py_compile app.py config.py analyze.py research.py scraper.py agent.py proximity.py listing_utils.py reporting.py
```

For data changes:

```bash
uv run scraper.py --location "Pennsylvania" --min-budget 75000 --budget 250000 \
  --output data_pa_wide.csv --json data_pa_wide.json
```

For orchestration changes, run at least one cheap smoke test:

```bash
uv run agent.py --from-json data_pa_wide.json --verify-top 0 --no-deep-dive --no-commit --scoring-workers 1
```

That last command still calls the scoring model, so use it only when API usage
is acceptable.
