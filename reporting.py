"""Report renderers for autobiz research runs."""

from __future__ import annotations

from listing_utils import assign_result_proximity_ranks


TIER_ICONS = {"A": "🟢", "B": "🟡", "C": "🟠", "D": "🔴"}


def render_agent_report(results: list[dict], deep_dives: dict, budget: int, min_budget: int = 0) -> str:
    good = [r for r in results if "error" not in r]
    assign_result_proximity_ranks(good)

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

        adjustments = r.get("_rule_adjustments", [])
        if adjustments:
            lines.append(f"  ↓ Adjusted: {'; '.join(adjustments)}")

        confidence = r.get("financial_confidence", {})
        if confidence:
            reasons = ", ".join(confidence.get("reasons", [])[:3])
            lines.append(
                f"  Financial confidence: {confidence.get('level', 'unknown')} "
                f"({confidence.get('score', 0)}/100)  |  {reasons}"
            )

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

    if verified:
        lines.append("  ── Verified / Real Listings ──")
        lines.append("")
        for i, r in enumerate(verified, 1):
            format_entry(i, r, show_dd=(i <= 3))

    if estimated:
        lines.append("  ── Market Estimates (unverified — use for benchmarking only) ──")
        lines.append("")
        for i, r in enumerate(estimated, 1):
            format_entry(i, r, show_dd=False)

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
